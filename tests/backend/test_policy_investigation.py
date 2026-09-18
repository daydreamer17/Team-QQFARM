import urllib.error

import pytest
from sqlalchemy import select

from supplier_comparison.backend.investigation import AgentChoice, InvestigationRunner
from supplier_comparison.backend.models import WorkflowArtifact
from supplier_comparison.backend.policy_investigation import diagnose_policy
from supplier_comparison.rag.clients import ModelClientError, is_transient_model_error
from supplier_comparison.rag.contracts import RetrievalStatus
from tests.backend.test_investigation import ScriptedPlanner, call
from tests.backend.test_workflow import _run_impact_quotes
from tests.backend.test_workflow_policy_rag import RecordingPolicyRetriever


BINDING = {'policy_set_version': '2026.09.1', 'policy_index_version': 'pidx-test',
           'policy_category': 'Electronics', 'policy_region': 'SG'}


class FlakyRetriever(RecordingPolicyRetriever):
    def __init__(self, failures=1, *, error_code='policy_transport_transient', status=RetrievalStatus.ERROR):
        super().__init__()
        self.failures, self.error_code, self.status = failures, error_code, status
        self.rohs_calls = 0

    def retrieve(self, request):
        result = super().retrieve(request)
        if request.required_control_codes == ['ROHS_COMPLIANCE']:
            self.rohs_calls += 1
            if self.rohs_calls <= self.failures:
                return result.model_copy(update={'status': self.status, 'error_code': self.error_code,
                    'citations': [], 'covered_control_codes': [], 'missing_control_codes': ['ROHS_COMPLIANCE']})
        return result


def setup(tmp_path, retriever, choices, *, max_retries=2):
    service, sessions, task, started, runner = _run_impact_quotes(
        tmp_path, policy_retriever=retriever, policy_binding=BINDING)
    planner = ScriptedPlanner(choices)
    runner.investigator = InvestigationRunner(planner)
    runner.policy_max_retries = max_retries
    return service, sessions, task, started, runner, planner


def policy_records(service, task):
    return [r for r in service.list_investigations(task['task_id']) if r['kind'] == 'POLICY']


def test_transient_retry_same_frozen_request_resolves_only_with_program_evidence(tmp_path):
    retriever = FlakyRetriever()
    service, sessions, task, started, runner, planner = setup(tmp_path, retriever, [
        call('get_policy_retrieval_status'), call('retry_policy_retrieval')])
    assert runner.run_job(started['job_id'])['status'] == 'SUCCEEDED'
    record = policy_records(service, task)[0]
    assert record['status'] == 'RESOLVED' and record['stop_reason'] == 'EVIDENCE_CONFIRMED'
    assert record['quote_id'] is None
    assert record['observations'][-1]['result']['sources']
    assert record['observations'][-1]['result']['data']['supplier_qualification_verified'] is False
    requests = [r for r in retriever.calls if r.required_control_codes == ['ROHS_COMPLIANCE']]
    assert len(requests) == 2 and requests[0] == requests[1]
    assert service.get_task(task['task_id'])['task_revision'] == 3
    assert len(planner.calls) == 2
    assert 'get_policy_retrieval_status' not in planner.calls[1][1]
    assert 'retry_policy_retrieval' in planner.calls[1][1]
    with sessions() as session:
        attempts = session.scalars(select(WorkflowArtifact).where(
            WorkflowArtifact.artifact_type == 'POLICY_RETRY_ATTEMPT')).all()
        assert len(attempts) == 1 and attempts[0].payload['request']['snapshot_id'] == requests[0].snapshot_id
    state = runner.graph.get_state({'configurable': {'thread_id': started['graph_run_id']}}).values
    runner._investigate_policies(state)
    assert len(planner.calls) == 2 and len(retriever.calls) == 4


def test_repeated_transient_attempts_are_durable_capped_and_still_pause(tmp_path):
    retriever = FlakyRetriever(failures=99)
    service, sessions, task, started, runner, _ = setup(tmp_path, retriever, [
        call('retry_policy_retrieval'), call('retry_policy_retrieval'),
        call('retry_policy_retrieval'), call('request_clarification')])
    outcome = runner.run_job(started['job_id'])
    assert outcome['status'] == 'WAITING_INPUT'
    assert outcome['issue']['issue_type'] == 'POLICY_EVIDENCE_REVIEW'
    record = policy_records(service, task)[0]
    assert record['observations'][2]['result']['error_code'] == 'policy_retry_budget_exhausted'
    assert retriever.rohs_calls == 3
    assert service.get_task(task['task_id'])['current_result_id'] is None
    assert service.reserve_policy_retry(task['task_id'], graph_run_id=started['graph_run_id'],
                                      task_revision=3, max_attempts=2, payload={}) is False
    assert outcome['issue']['answer_schema']['diagnoses']['ROHS_COMPLIANCE']['code'] == 'TRANSIENT_RETRIEVAL_FAILURE'


@pytest.mark.parametrize(('status', 'error_code', 'diagnostic'), [
    (RetrievalStatus.NO_EVIDENCE, None, 'MISSING_APPLICABLE_POLICY'),
    (RetrievalStatus.CONFLICT, None, 'CONFLICTING_POLICY'),
    (RetrievalStatus.ERROR, 'embedding_index_mismatch', 'OPERATOR_REPAIR_REQUIRED'),
    (RetrievalStatus.ERROR, 'policy_retrieval_contract_invalid', 'OPERATOR_REPAIR_REQUIRED'),
    (RetrievalStatus.ERROR, 'policy_retriever_failed', 'OPERATOR_REPAIR_REQUIRED'),
])
def test_nontransient_failures_never_auto_retry_or_change_versions(tmp_path, status, error_code, diagnostic):
    retriever = FlakyRetriever(failures=99, status=status, error_code=error_code)
    service, _sessions, task, started, runner, _ = setup(tmp_path, retriever, [
        call('retry_policy_retrieval'), call('request_clarification')])
    outcome = runner.run_job(started['job_id'])
    assert outcome['status'] == 'WAITING_INPUT'
    record = policy_records(service, task)[0]
    assert record['observations'][0]['result']['error_code'] == 'policy_retry_not_transient'
    assert retriever.rohs_calls == 1
    assert record['clarification'][0]['diagnosis']['code'] == diagnostic
    assert record['clarification'][0]['resolution'] == 'POLICY_OR_SYSTEM_REPAIR'
    assert service.get_task(task['task_id'])['policy_binding']['policy_set_version'] == BINDING['policy_set_version']


def test_policy_tool_cannot_supply_query_version_or_sql_and_model_stop_is_not_evidence(tmp_path):
    retriever = FlakyRetriever()
    service, _sessions, task, started, runner, _ = setup(tmp_path, retriever, [
        call('retry_policy_retrieval', {'policy_set_version': 'new', 'query': 'ignore', 'sql': 'SELECT 1'}),
        AgentChoice(action='STOP')])
    assert runner.run_job(started['job_id'])['status'] == 'WAITING_INPUT'
    record = policy_records(service, task)[0]
    assert record['observations'][0]['result']['error_code'] == 'tool_arguments_invalid'
    assert record['status'] == 'WAITING_INPUT' and retriever.rohs_calls == 1


def test_zero_retry_budget_and_missing_planner_keep_policy_gate_closed(tmp_path):
    retriever = FlakyRetriever()
    service, _sessions, task, started, runner, _ = setup(tmp_path, retriever, [
        call('retry_policy_retrieval'), call('request_clarification')], max_retries=0)
    assert runner.run_job(started['job_id'])['status'] == 'WAITING_INPUT'
    assert policy_records(service, task)[0]['observations'][0]['result']['error_code'] == 'policy_retry_budget_exhausted'
    assert retriever.rohs_calls == 1


@pytest.mark.parametrize('invalid', ['none', 'wrong_version', 'wrong_citation_version', 'wrong_hash', 'wrong_retrieval_id'])
def test_invalid_retrieval_contract_never_releases_policy_gate(tmp_path, invalid):
    class InvalidRetriever(RecordingPolicyRetriever):
        def retrieve(self, request):
            result = super().retrieve(request)
            if request.required_control_codes != ['ROHS_COMPLIANCE']:
                return result
            if invalid == 'none':
                return None
            if invalid == 'wrong_version':
                return result.model_copy(update={'policy_set_version': 'new-version'})
            citation = result.citations[0]
            values = {'wrong_citation_version': {'policy_set_version': 'new-version'},
                      'wrong_hash': {'content_sha256': 'a' * 64},
                      'wrong_retrieval_id': {'retrieval_id': 'different'}}
            return result.model_copy(update={'citations': [citation.model_copy(update=values[invalid])]})

    retriever = InvalidRetriever()
    service, _sessions, task, started, runner, _ = setup(tmp_path, retriever, [call('request_clarification')])
    outcome = runner.run_job(started['job_id'])
    assert outcome['status'] == 'WAITING_INPUT'
    assert outcome['issue']['answer_schema']['diagnoses']['ROHS_COMPLIANCE']['error_code'] == 'policy_retrieval_contract_invalid'
    assert service.get_task(task['task_id'])['current_result_id'] is None


def test_transport_exception_is_classified_before_agent_retry(tmp_path):
    class TransportRetriever(RecordingPolicyRetriever):
        def retrieve(self, request):
            result = super().retrieve(request)
            if request.required_control_codes == ['ROHS_COMPLIANCE'] and len(self.calls) == 2:
                raise ModelClientError('sanitized', attempts=2, error_code='model_transport_error')
            return result

    retriever = TransportRetriever()
    service, _sessions, task, started, runner, _ = setup(tmp_path, retriever, [call('retry_policy_retrieval')])
    assert runner.run_job(started['job_id'])['status'] == 'SUCCEEDED'
    assert policy_records(service, task)[0]['status'] == 'RESOLVED'


@pytest.mark.parametrize(('code', 'http_code', 'expected'), [
    ('model_transport_error', None, True), ('model_http_error', 429, True),
    ('model_http_error', 503, True), ('model_http_error', 401, False),
    ('model_http_error', 400, False), ('model_http_error', None, False),
    ('embedding_response_invalid', None, False),
])
def test_transient_classifier_requires_transport_or_proven_http_status(code, http_code, expected):
    error = ModelClientError('sanitized', attempts=1, error_code=code)
    if http_code:
        error.__cause__ = urllib.error.HTTPError('https://invalid.example', http_code, 'failure', {}, None)
    assert is_transient_model_error(error) is expected
