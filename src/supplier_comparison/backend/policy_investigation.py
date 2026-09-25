"""Read-only policy diagnosis with durable, same-version, bounded retries."""

from __future__ import annotations

import hashlib
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import select

from supplier_comparison.rag.contracts import RetrievalRequest, RetrievalResult, RetrievalStatus

from .investigation import InvestigationCase, ToolResult
from .investigation_tools import NoArguments
from .models import WorkflowArtifact
from .service import BackendService, ConflictError, content_hash


TRANSIENT_CODES = {'embedding_transient_error', 'rerank_transient_error', 'policy_transport_transient'}


def diagnose_policy(result: RetrievalResult) -> dict:
    if result.status == RetrievalStatus.OK:
        return {'code': 'EVIDENCE_FOUND', 'automatic_retry_allowed': False,
                'next_action': 'Policy was retrieved; this does not mean supplier eligibility is verified or amount approval is complete.'}
    if result.status == RetrievalStatus.NO_EVIDENCE:
        return {'code': 'MISSING_APPLICABLE_POLICY', 'automatic_retry_allowed': False,
                'next_action': 'Check category, region, effective dates, and mandatory clauses. If policy content changed, review and publish a new revision and create a new task.'}
    if result.status == RetrievalStatus.CONFLICT:
        return {'code': 'CONFLICTING_POLICY', 'automatic_retry_allowed': False,
                'next_action': 'The policy administrator must resolve the conflict and publish a new revision; changing the retrieval question cannot establish a pass.'}
    transient = result.error_code in TRANSIENT_CODES
    return {'code': 'TRANSIENT_RETRIEVAL_FAILURE' if transient else 'OPERATOR_REPAIR_REQUIRED',
            'automatic_retry_allowed': transient, 'error_code': result.error_code,
            'next_action': 'A limited retry is allowed for the same revision; pause if it still fails.' if transient else 'Check configuration, index, authentication, or the response contract; do not automatically retry an unknown error.'}


class ScopedPolicyInvestigationTools:
    def __init__(self, service: BackendService, *, task_id: str, task_revision: int,
                 graph_run_id: str, comparison_result_id: str, requests: dict[str, RetrievalRequest],
                 artifact_ids: dict[str, str], retry: Callable[[RetrievalRequest, str], str], max_retries: int = 2):
        if not 0 <= max_retries <= 3:
            raise ValueError('policy retry cap must be between 0 and 3')
        self.service, self.task_id, self.task_revision = service, task_id, task_revision
        self.graph_run_id, self.comparison_result_id = graph_run_id, comparison_result_id
        self.task = service.get_task(task_id)
        self.requests, self.artifact_ids = requests, dict(artifact_ids)
        self.retry, self.max_retries = retry, max_retries
        self.fingerprint = content_hash([self.task['requirement'], self.task['policy_binding'], task_revision,
                                         graph_run_id, comparison_result_id,
                                         {k: r.model_dump(mode='json') for k, r in requests.items()}])
        descriptions = {
            'get_task_context': 'Read frozen requirements and policy revision; modification is not allowed',
            'get_policy_retrieval_status': 'Diagnose current policy retrieval status and next steps without treating policy text as supplier evidence',
            'retry_policy_retrieval': 'Allow only limited retries of the same query and revision for mandatory clauses with a confirmed transient failure',
            'request_clarification': 'List policy issues and recommended actions together, keep publication paused, and do not alter quotation fields',
        }
        self.schemas = {name: {'description': text, 'arguments': NoArguments.model_json_schema()}
                        for name, text in descriptions.items()}
        # Recover an already completed retry if graph replay occurs before node checkpointing.
        with service.session_factory() as session:
            comparison = session.get(WorkflowArtifact, comparison_result_id)
            if (comparison is None or comparison.task_id != task_id or comparison.graph_run_id != graph_run_id
                    or comparison.task_revision != task_revision or comparison.artifact_type not in ('COMPARISON_RESULT', 'BASE_COMPARISON')):
                raise ConflictError('policy_investigation_scope_invalid', 'Comparison is outside the current task.')
            scope_codes = {}
            if comparison.artifact_type == 'BASE_COMPARISON':
                plan = session.scalar(select(WorkflowArtifact).where(
                    WorkflowArtifact.graph_run_id == graph_run_id,
                    WorkflowArtifact.task_revision == task_revision,
                    WorkflowArtifact.artifact_type == 'COMPLIANCE_PLAN',
                ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))
                scope_codes = {c['clause_id']: c['control_code'] for c in plan.payload['clauses']} if plan else {}
            for code, request in requests.items():
                binding = self.task['policy_binding'] or {}
                if (request.snapshot_id != comparison.parent_artifact_id
                        or request.required_control_codes != [scope_codes.get(code) if comparison.artifact_type == 'BASE_COMPARISON' else code]
                        or request.policy_set_version != binding.get('policy_set_version')
                        or request.policy_index_version != binding.get('policy_index_version')
                        or request.category != binding.get('category') or request.region != binding.get('region')):
                    raise ConflictError('policy_investigation_scope_invalid', 'Request does not match the frozen task binding.')
            rows = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id, WorkflowArtifact.task_revision == task_revision,
                WorkflowArtifact.graph_run_id == graph_run_id,
                WorkflowArtifact.parent_artifact_id == comparison_result_id,
                WorkflowArtifact.artifact_type == 'POLICY_RETRIEVAL_RESULT',
            ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())).all()
            for code in self.artifact_ids:
                latest = next((a for a in rows if a.payload.get('filters', {}).get('scope_key') == code or
                    ('scope_key' not in a.payload.get('filters', {}) and code in a.payload.get('filters', {}).get('control_codes', []))), None)
                if latest:
                    self.artifact_ids[code] = latest.artifact_id
        for code in self.artifact_ids:
            self._result(code)

    def current(self) -> bool:
        task = self.service.get_task(self.task_id)
        return (task['task_revision'] == self.task_revision and task['current_graph_run_id'] == self.graph_run_id
                and task['requirement'] == self.task['requirement'] and task['policy_binding'] == self.task['policy_binding'])

    def _result(self, code: str) -> RetrievalResult:
        request = self.requests[code]
        required_code = request.required_control_codes[0]
        with self.service.session_factory() as session:
            artifact = session.get(WorkflowArtifact, self.artifact_ids[code])
            if (artifact is None or artifact.task_id != self.task_id or artifact.graph_run_id != self.graph_run_id
                    or artifact.task_revision != self.task_revision or artifact.artifact_type != 'POLICY_RETRIEVAL_RESULT'
                    or artifact.parent_artifact_id != self.comparison_result_id):
                raise ConflictError('policy_investigation_scope_invalid', 'Policy artifact is outside the frozen comparison.')
            result = RetrievalResult.model_validate(artifact.payload)
        if (request.task_id != self.task_id or request.task_revision != self.task_revision
                or result.policy_set_version != request.policy_set_version
                or result.policy_index_version != request.policy_index_version
                or any(c.policy_set_version != request.policy_set_version
                       or hashlib.sha256(c.text.encode('utf-8')).hexdigest() != c.content_sha256 for c in result.citations)
                or (result.status == RetrievalStatus.OK and (required_code not in result.covered_control_codes
                    or not any(c.control_code == required_code and c.policy_set_version == request.policy_set_version
                               and c.retrieval_id == result.retrieval_id for c in result.citations)))):
            raise ConflictError('policy_investigation_contract_invalid', 'Policy result does not prove the required frozen control.')
        return result

    @staticmethod
    def _code(case):
        return case.unknown_fields[0]

    def cases(self) -> tuple[InvestigationCase, ...]:
        cases = []
        for code in sorted(self.artifact_ids):
            result = self._result(code)
            identity = content_hash([self.fingerprint, code])
            case_id = 'case_' + identity[:32]
            with self.service.session_factory() as session:
                rows = session.scalars(select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == self.task_id, WorkflowArtifact.graph_run_id == self.graph_run_id,
                    WorkflowArtifact.task_revision == self.task_revision,
                    WorkflowArtifact.artifact_type == 'INVESTIGATION_CASE',
                ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())).all()
                existing = next((a for a in rows if a.payload.get('case_id') == case_id), None)
            if existing:
                cases.append(InvestigationCase.model_validate(existing.payload))
            elif result.status != RetrievalStatus.OK:
                cases.append(InvestigationCase(
                    case_id=case_id, task_id=self.task_id, task_revision=self.task_revision,
                    graph_run_id=self.graph_run_id, kind='POLICY', quote_id=None, quote_version=None,
                    impact_input_sha256=self.fingerprint, policy_binding=self.task['policy_binding'] or {},
                    goal='Determine why a mandatory policy was not retrieved. Retry the same revision only when allowed; otherwise state the pause and next step clearly.',
                    known_facts={'control_code': code, 'retrieval': result.model_dump(mode='json'),
                                 'diagnosis': diagnose_policy(result)}, unknown_fields=(code,),
                    impact_status='REQUIRES_INVESTIGATION',
                ))
        return tuple(cases)

    def save(self, case):
        self.service.append_artifact(task_id=self.task_id, task_revision=self.task_revision,
                                     graph_run_id=self.graph_run_id, artifact_type='INVESTIGATION_CASE',
                                     schema_version=case.schema_version, payload=case.model_dump(mode='json'))

    def call_signature(self, case, name, arguments):
        # Retry results change the server state; identical calls on unchanged state still deduplicate.
        return content_hash([name, arguments, self.artifact_ids.get(self._code(case))])

    def available_schemas(self, case):
        code = self._code(case)
        completed = {o.result.tool_name for o in case.observations if o.result.status == 'OK'
                     and (o.result.tool_name == 'get_task_context' or (
                         o.result.tool_name == 'get_policy_retrieval_status'
                         and o.result.data.get('retrieval_artifact_id') == self.artifact_ids[code]))}
        if not diagnose_policy(self._result(code))['automatic_retry_allowed'] or self.max_retries == 0:
            completed.add('retry_policy_retrieval')
        return {name: schema for name, schema in self.schemas.items() if name not in completed}

    def resolution(self, case):
        return 'EVIDENCE_CONFIRMED' if self._result(self._code(case)).status == RetrievalStatus.OK else None

    def clarification_cards(self, case):
        code = self._code(case)
        result = self._result(code)
        return ({'control_code': code, 'policy_binding': self.task['policy_binding'],
                 'retrieval_artifact_id': self.artifact_ids[code], 'status': result.status.value,
                 'diagnosis': diagnose_policy(result), 'resolution': 'POLICY_OR_SYSTEM_REPAIR',
                 'supplier_qualification_verified': False},)

    def execute(self, case, name, arguments):
        common = dict(tool_name=name, task_id=self.task_id, task_revision=self.task_revision,
                      quote_id=None, input_sha256=self.fingerprint)
        if not self.current():
            return ToolResult(**common, status='STALE', error_code='input_changed')
        if (case.kind != 'POLICY' or case.task_id != self.task_id or case.task_revision != self.task_revision
                or case.graph_run_id != self.graph_run_id or case.impact_input_sha256 != self.fingerprint
                or len(case.unknown_fields) != 1 or self._code(case) not in self.requests):
            return ToolResult(**common, status='DENIED', error_code='tool_scope_denied')
        if name not in self.schemas:
            return ToolResult(**common, status='DENIED', error_code='tool_not_allowed')
        try:
            NoArguments.model_validate(arguments)
        except ValidationError:
            return ToolResult(**common, status='DENIED', error_code='tool_arguments_invalid')
        code = self._code(case)
        if name == 'get_task_context':
            return ToolResult(**common, status='OK', data={'requirement': self.task['requirement'],
                                                          'policy_binding': self.task['policy_binding']})
        if name == 'request_clarification':
            return ToolResult(**common, status='NEEDS_INPUT', data={'cards': self.clarification_cards(case)})
        result = self._result(code)
        if name == 'get_policy_retrieval_status':
            return ToolResult(**common, status='OK', data={'retrieval': result.model_dump(mode='json'),
                               'diagnosis': diagnose_policy(result), 'retrieval_artifact_id': self.artifact_ids[code]})
        if not diagnose_policy(result)['automatic_retry_allowed']:
            return ToolResult(**common, status='DENIED', error_code='policy_retry_not_transient')
        request = self.requests[code]
        payload = {'control_code': code, 'failed_artifact_id': self.artifact_ids[code],
                   'request': request.model_dump(mode='json'), 'error_code': result.error_code}
        if not self.service.reserve_policy_retry(self.task_id, graph_run_id=self.graph_run_id,
                    task_revision=self.task_revision, max_attempts=self.max_retries, payload=payload):
            return ToolResult(**common, status='DENIED', error_code='policy_retry_budget_exhausted')
        self.artifact_ids[code] = self.retry(request, code)
        result = self._result(code)
        if not self.current():
            return ToolResult(**common, status='STALE', error_code='input_changed')
        return ToolResult(**common, status='OK' if result.status == RetrievalStatus.OK else 'ERROR',
                          data={'retrieval': result.model_dump(mode='json'), 'diagnosis': diagnose_policy(result),
                                'retrieval_artifact_id': self.artifact_ids[code],
                                'supplier_qualification_verified': False},
                          sources=tuple(c.model_dump(mode='json') for c in result.citations), error_code=result.error_code)
