from __future__ import annotations

import json
import os
from io import BytesIO

import pytest
from sqlalchemy import select

from supplier_comparison.backend.investigation import (
    AgentChoice, AgentConfig, AgentLimits, CaseStatus, InvestigationRunner, LiveInvestigationPlanner,
)
from supplier_comparison.backend.investigation_tools import ScopedInvestigationTools
from supplier_comparison.backend.models import WorkflowArtifact
from supplier_comparison.backend.service import BackendError, BackendService, NotFoundError
from supplier_comparison.rag.clients import ModelClientError
from tests.backend.test_workflow import _run_impact_quotes


class ScriptedPlanner:
    model_id = "fixed-investigation-planner"

    def __init__(self, choices):
        self.choices = list(choices)
        self.calls = []

    def choose(self, case, tools, *, timeout_seconds):
        self.calls.append((case, tools, timeout_seconds))
        choice = self.choices.pop(0)
        if isinstance(choice, Exception):
            raise choice
        return choice


def call(name, arguments=None):
    return AgentChoice(action="CALL", tool_name=name, arguments=arguments or {},
                       plan=("核对当前可用资料",), reason="查证待确认信息")


def setup_agent(tmp_path, choices, *, overrides=None, limits=None):
    rows = {"SUP-023": {"unit_price": "9.80"}}
    for supplier, fields in (overrides or {}).items():
        rows.setdefault(supplier, {}).update(fields)
    service, sessions, task, started, runner = _run_impact_quotes(tmp_path, overrides=rows)
    planner = ScriptedPlanner(choices)
    runner.investigator = InvestigationRunner(planner, limits=limits)
    return service, sessions, task, started, runner, planner


def test_source_then_records_then_one_batch_clarification(tmp_path):
    service, sessions, task, started, runner, planner = setup_agent(tmp_path, [
        call("locate_quote_source"), call("get_confirmed_quote_records"), call("request_clarification"),
    ])
    outcome = runner.run_job(started["job_id"])
    assert outcome["status"] == "WAITING_INPUT"
    assert outcome["issue"]["issue_type"] == "BATCH_FIELD_REVIEW"
    records = service.list_investigations(task["task_id"])
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "WAITING_INPUT"
    assert record["stop_reason"] == "SOURCES_EXHAUSTED"
    assert record["model_calls"] == 3
    assert record["plan"] == ["核对当前可用资料"]
    observations = record["observations"]
    assert [o["result"]["tool_name"] for o in observations] == [
        "locate_quote_source", "get_confirmed_quote_records", "request_clarification",
    ]
    assert [o["result"]["status"] for o in observations] == ["OK", "NOT_FOUND", "NEEDS_INPUT"]
    assert observations[0]["result"]["data"]["absence_not_confirmed"]
    assert observations[0]["result"]["sources"]
    assert {c["field_name"] for c in outcome["issue"]["answer_schema"]["cards"]} == {
        "shipping_fee_status", "shipping_fee_amount",
    }
    assert service.get_task(task["task_id"])["current_result_id"] is None
    with sessions() as session:
        assert not session.scalars(select(WorkflowArtifact).where(
            WorkflowArtifact.artifact_type == "CORRECTION_EVENT"
        )).all()
    # A completed case can be replayed without repeat provider/tool calls.
    state = runner.graph.get_state({"configurable": {"thread_id": started["graph_run_id"]}}).values
    runner._investigate_quotes(state)
    assert len(planner.calls) == 3
    with pytest.raises(BackendError, match="batch field correction"):
        service.answer_issue(task["task_id"], outcome["issue"]["issue_id"], expected_task_revision=3,
                             answer={"answer_type": "BATCH_FIELD_CORRECTIONS"}, idempotency_key="wrong-resume")


def test_agent_selects_different_tools_and_cannot_mark_problem_solved(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(tmp_path, [
        call("get_confirmed_quote_records"), AgentChoice(action="STOP", reason="资料不足"),
    ])
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    record = service.list_investigations(task["task_id"])[0]
    assert [o["result"]["tool_name"] for o in record["observations"]] == ["get_confirmed_quote_records"]
    assert record["status"] == "WAITING_INPUT"  # STOP is not a verified fact.
    assert record["stop_reason"] == "EVIDENCE_INSUFFICIENT"
    assert service.get_task(task["task_id"])["current_result_id"] is None
    assert len(planner.calls) == 2


def test_multiple_quotes_collected_under_one_issue_and_global_budget(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(
        tmp_path, [call("request_clarification")],
        overrides={"SUP-024": {"shipping_fee_status": "", "shipping_fee_amount": ""}},
        limits=AgentLimits(max_model_calls=1, max_tool_calls=2),
    )
    outcome = runner.run_job(started["job_id"])
    cards = outcome["issue"]["answer_schema"]["cards"]
    assert len({card["quote_id"] for card in cards}) == 2
    assert len(cards) == 4
    records = service.list_investigations(task["task_id"])
    assert {r["status"] for r in records} == {"WAITING_INPUT", "LIMIT_REACHED"}
    assert len(planner.calls) == 1
    assert len(service.list_issues(task["task_id"])) == 1


def test_unknown_and_duplicate_tool_calls_are_denied_not_executed(tmp_path):
    service, _sessions, task, started, runner, _planner = setup_agent(tmp_path, [
        call("execute_sql", {"sql": "DROP TABLE tasks"}), call("locate_quote_source"),
        call("locate_quote_source", {"field_names": []}), call("request_clarification"),
    ])
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    observations = service.list_investigations(task["task_id"])[0]["observations"]
    assert observations[0]["result"]["error_code"] == "tool_not_allowed"
    assert observations[2]["result"]["error_code"] == "duplicate_tool_call"
    assert service.get_task(task["task_id"])["task_revision"] == 3


def test_model_error_is_sanitized_and_does_not_remove_review_gate(tmp_path):
    service, _sessions, task, started, runner, _planner = setup_agent(
        tmp_path, [RuntimeError("Authorization: secret-token")],
    )
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    record = service.list_investigations(task["task_id"])[0]
    assert record["error_code"] == "agent_planner_failed"
    assert record["stop_reason"] == "MODEL_UNAVAILABLE"
    assert "secret-token" not in json.dumps(record)
    assert record["clarification"]


def test_dominated_unknown_resolves_without_model_or_false_human_review(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(
        tmp_path, [], overrides={"SUP-023": {"unit_price": "15.00"}},
    )
    assert runner.run_job(started["job_id"])["status"] == "SUCCEEDED"
    record = service.list_investigations(task["task_id"])[0]
    assert record["status"] == "RESOLVED"
    assert record["stop_reason"] == "NO_DECISION_IMPACT"
    assert record["observations"] == [] and record["model_calls"] == 0
    assert planner.calls == []
    assert service.list_review_problems(task["task_id"])["quotes"][0]["review_status"] in {
        "REVIEW_REQUIRED", "READY_FOR_DOWNSTREAM",
    }


def test_unsafe_field_has_no_impact_proof_and_cannot_be_published(tmp_path):
    service, _sessions, task, started, runner, _planner = setup_agent(
        tmp_path, [call("locate_quote_source"), call("request_clarification")],
        overrides={"SUP-023": {"package": ""}},
    )
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    record = service.list_investigations(task["task_id"])[0]
    assert not record["known_facts"]["impact_proof_available"]
    assert record["impact_status"] == "UNDETERMINED"
    assert "package" in record["unknown_fields"]
    assert service.get_task(task["task_id"])["current_result_id"] is None


def tools_from_waiting(service, task, started, runner):
    state = runner.graph.get_state({"configurable": {"thread_id": started["graph_run_id"]}}).values
    return ScopedInvestigationTools(
        service, task_id=task["task_id"], graph_run_id=started["graph_run_id"], task_revision=3,
        impact_artifact_id=state["decision_impact_artifact_id"], evaluated_at=runner.evaluated_at,
    )


def test_scoped_tools_reject_paths_other_quotes_and_stale_input(tmp_path):
    service, _sessions, task, started, runner, _planner = setup_agent(tmp_path, [call("request_clarification")])
    runner.run_job(started["job_id"])
    tools = tools_from_waiting(service, task, started, runner)
    case = tools.cases()[0]
    assert tools.execute(case, "locate_quote_source", {"path": "C:/Users/secrets"}).status == "DENIED"
    assert tools.execute(case, "locate_quote_source", {"quote_id": "another-quote"}).status == "DENIED"
    assert tools.execute(case.model_copy(update={"quote_id": "foreign"}), "locate_quote_source", {}).status == "DENIED"
    service.upload_quote(task["task_id"], expected_task_revision=3, supplier_id="SUP-NEW",
                         original_filename="new.csv", media_type="text/csv", content=b"new", idempotency_key="changed")
    assert tools.execute(case, "locate_quote_source", {}).status == "STALE"
    assert service.list_investigations(task["task_id"])[0]["status"] == "STALE"


def test_investigation_read_enforces_owner(tmp_path):
    service, sessions, task, started, runner, _planner = setup_agent(tmp_path, [call("request_clarification")])
    runner.run_job(started["job_id"])
    outsider = BackendService(sessions, tmp_path / "other", actor_id="other")
    with pytest.raises(NotFoundError):
        outsider.list_investigations(task["task_id"])
    with pytest.raises(NotFoundError):
        ScopedInvestigationTools(outsider, task_id=task["task_id"], graph_run_id=started["graph_run_id"],
                                task_revision=3, impact_artifact_id=None, evaluated_at=runner.evaluated_at)


def test_live_transport_schema_and_failure_validation_without_network(tmp_path, monkeypatch):
    service, _sessions, task, started, runner, _planner = setup_agent(tmp_path, [call("request_clarification")])
    runner.run_job(started["job_id"])
    tools = tools_from_waiting(service, task, started, runner)
    case = tools.cases()[0]
    monkeypatch.setenv("TEST_INVESTIGATION_KEY", "secret-key")
    requests = []

    def opener(request, *, timeout):
        requests.append((json.loads(request.data), timeout))
        return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(call("locate_quote_source").model_dump(mode="json"))
        }}], "usage": {"total_tokens": 100}}).encode())

    planner = LiveInvestigationPlanner(AgentConfig(model_id="test", api_key_env="TEST_INVESTIGATION_KEY"), opener=opener)
    assert planner.choose(case, tools.schemas, timeout_seconds=5).tool_name == "locate_quote_source"
    assert requests[0][1] == 5
    assert requests[0][0]["max_tokens"] == 1024
    assert requests[0][0]["response_format"] == {"type": "json_object"}
    context = json.loads(requests[0][0]['messages'][1]['content'])
    assert context['response_schema'] == AgentChoice.model_json_schema()
    assert context['case']['goal'] == case.goal
    assert planner.telemetry[0]["usage"] == {"total_tokens": 100}
    assert "secret-key" not in json.dumps(requests)

    def malformed(_request, *, timeout):
        return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": '{"action":"STOP","status":"RESOLVED","amount":"0"}'
        }}]}).encode())

    bad = LiveInvestigationPlanner(planner.config, opener=malformed)
    with pytest.raises(ModelClientError) as failed:
        bad.choose(case, tools.schemas, timeout_seconds=5)
    assert failed.value.error_code == "agent_response_invalid"


@pytest.mark.parametrize("endpoint", ["http://remote.example/v1", "https://user:password@example.com/v1"])
def test_live_endpoint_security(endpoint):
    with pytest.raises(ValueError):
        AgentConfig(model_id="test", base_url=endpoint)


def test_batch_answer_reaudits_and_preserves_historical_investigation(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(tmp_path, [call("request_clarification")])
    first = runner.run_job(started["job_id"])
    quote = next(q for q in service.list_review_problems(task["task_id"])["quotes"] if q["supplier_id"] == "SUP-023")
    fields = {f["field_name"]: f for f in quote["fields"]}
    corrected = service.correct_fields(
        task_id=task["task_id"], expected_task_revision=3, idempotency_key="batch-answer",
        corrections=[{"quote_id": quote["quote_id"], "field_name": name,
                      "expected_field_version": fields[name]["field_version"],
                      "raw_value": value, "normalized_value": value, "unit": unit,
                      "reason": "Buyer confirmed supplier shipping"}
                     for name, value, unit in (("shipping_fee_status", "KNOWN_AMOUNT", None),
                                               ("shipping_fee_amount", "200.00", "SGD"))],
    )
    assert runner.run_job(corrected["job_id"])["status"] == "SUCCEEDED"
    history = service.list_investigations(task["task_id"])
    assert len(history) == 1 and history[0]["status"] == "STALE"
    assert history[0]["stored_status"] == "WAITING_INPUT"
    assert len(planner.calls) == 1 and len(runner.processor.calls) == 2
    assert service.get_issue(first["issue"]["issue_id"])["status"] == "SUPERSEDED"
    current = service.get_task(task['task_id'])
    tools = ScopedInvestigationTools(
        service, task_id=task["task_id"], graph_run_id=current['current_graph_run_id'], task_revision=current['task_revision'],
        impact_artifact_id=None, evaluated_at=runner.evaluated_at,
    )
    old_case = planner.calls[0][0]
    current_case = old_case.model_copy(update={"graph_run_id": current['current_graph_run_id'], "task_revision": current['task_revision'],
                                             "impact_input_sha256": tools.input_sha256})
    records = tools.execute(current_case, "get_confirmed_quote_records", {
        "field_names": ["shipping_fee_status", "shipping_fee_amount"]
    }).data["records"]
    assert {r["field_name"] for r in records} == {"shipping_fee_status", "shipping_fee_amount"}


def test_model_and_tool_time_limits_keep_unknowns_pending(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(
        tmp_path, [call("locate_quote_source")], limits=AgentLimits(max_tool_calls=1),
    )
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    record = service.list_investigations(task["task_id"])[0]
    assert record["status"] == "LIMIT_REACHED" and record["stop_reason"] == "BUDGET_EXHAUSTED"
    assert len(planner.calls) == 1 and len(record["observations"]) == 1


def test_elapsed_model_call_does_not_execute_a_late_tool(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(tmp_path, [call("locate_quote_source")])
    now = [0.0]
    runner.investigator.clock = lambda: now[0]
    original = planner.choose

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 100.0
        return result

    planner.choose = slow
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    record = service.list_investigations(task["task_id"])[0]
    assert record["status"] == "LIMIT_REACHED"
    assert record["observations"] == []


def test_inputs_changed_during_planning_stop_before_source_read(tmp_path):
    service, _sessions, task, started, runner, planner = setup_agent(tmp_path, [call("locate_quote_source")])
    original = planner.choose

    def changed(*args, **kwargs):
        service.upload_quote(task["task_id"], expected_task_revision=3, supplier_id="NEW",
                             original_filename="new.csv", media_type="text/csv", content=b"new", idempotency_key="changed-during-plan")
        return original(*args, **kwargs)

    planner.choose = changed
    with pytest.raises(BackendError) as failed:
        runner.run_job(started["job_id"])
    assert failed.value.code == "investigation_input_changed"
    record = service.list_investigations(task["task_id"])[0]
    assert record["stored_status"] == "STALE" and record["observations"] == []
    assert service.get_task(task["task_id"])["current_result_id"] is None


def test_read_only_rag_bound_to_task_cannot_satisfy_mandatory_gate(tmp_path):
    from tests.backend.test_workflow_policy_rag import RecordingPolicyRetriever
    retriever = RecordingPolicyRetriever()
    service, _sessions, task, started, runner = _run_impact_quotes(
        tmp_path, overrides={"SUP-023": {"unit_price": "9.80"}}, policy_retriever=retriever,
        policy_binding={"policy_set_version": "2026.09.1", "policy_index_version": "pidx-test",
                        "policy_category": "Electronics", "policy_region": "SG"},
    )
    planner = ScriptedPlanner([call("retrieve_policy", {"query": "What shipping evidence is required?", "control_code": "ROHS_COMPLIANCE"}),
                              call("request_clarification")])
    runner.investigator = InvestigationRunner(planner)
    assert runner.run_job(started["job_id"])["status"] == "WAITING_INPUT"
    record = service.list_investigations(task["task_id"])[0]
    result = record["observations"][0]["result"]
    assert result["status"] == "OK" and result["sources"]
    assert result["data"]["mandatory_gate_satisfied"] is False
    assert retriever.calls[0].policy_index_version == "pidx-test"
    assert service.get_task(task["task_id"])["current_result_id"] is None


def test_nonblocking_quote_still_reaches_mandatory_policy_pause(tmp_path):
    from tests.backend.test_workflow_policy_rag import RecordingPolicyRetriever
    retriever = RecordingPolicyRetriever(failing_control_code="ROHS_COMPLIANCE")
    service, _sessions, task, started, runner = _run_impact_quotes(
        tmp_path, policy_retriever=retriever,
        policy_binding={"policy_set_version": "2026.09.1", "policy_index_version": "pidx-test",
                        "policy_category": "Electronics", "policy_region": "SG"},
    )
    planner = ScriptedPlanner([call('get_policy_retrieval_status'), call('request_clarification')])
    runner.investigator = InvestigationRunner(planner)
    outcome = runner.run_job(started["job_id"])
    assert outcome["issue"]["issue_type"] == "POLICY_EVIDENCE_REVIEW"
    assert len(retriever.calls) == 3
    assert [c.kind for c, _, _ in planner.calls] == ['POLICY', 'POLICY']


def test_gap_and_draft_tools_preserve_unknown_facts_and_need_no_formal_edit(tmp_path):
    service, _sessions, task, started, runner, _planner = setup_agent(tmp_path, [
        call('analyze_selection_gap'), call('draft_clarification'), call('request_clarification')])
    outcome = runner.run_job(started['job_id'])
    assert outcome['status'] == 'WAITING_INPUT'
    record = service.list_investigations(task['task_id'])[0]
    gap, draft = [o['result'] for o in record['observations'][:2]]
    assert 'analyze_selection_gap' not in _planner.calls[1][1]
    assert 'draft_clarification' in _planner.calls[1][1]
    assert 'draft_clarification' not in _planner.calls[2][1]
    assert gap['status'] == 'OK' and gap['data']['confirmed_total_cost'] is None
    assert gap['data']['pending_reasons']
    assert draft['status'] == 'OK' and draft['data']['sent'] is False
    assert service.get_task(task['task_id'])['task_revision'] == 3
    assert service.get_task(task['task_id'])['current_result_id'] is None
    tools = tools_from_waiting(service, task, started, runner)
    assert 'simulate_requirement_change' not in tools.schemas
    assert tools.execute(tools.cases()[0], 'simulate_requirement_change', {}).status == 'DENIED'
    authorized = ScopedInvestigationTools(
        service, task_id=task['task_id'], graph_run_id=started['graph_run_id'], task_revision=3,
        impact_artifact_id=runner.graph.get_state({'configurable': {'thread_id': started['graph_run_id']}}).values['decision_impact_artifact_id'],
        evaluated_at=runner.evaluated_at, authorized_requirement_changes={'budget_amount': '9000'})
    assert 'simulate_requirement_change' in authorized.schemas
    assert authorized.schemas['simulate_requirement_change']['authorization']['changes'] == {'budget_amount': '9000'}
    assert authorized.execute(authorized.cases()[0], 'get_task_context', {}).data['authorized_requirement_changes'] == {'budget_amount': '9000'}
    assert authorized.execute(authorized.cases()[0], 'simulate_requirement_change', {'budget_amount': '99999'}).status == 'DENIED'
    assert authorized.execute(authorized.cases()[0], 'simulate_requirement_change', {}).data['hypothetical']
    assert service.get_task(task["task_id"])["current_result_id"] is None


def test_requested_investigation_is_goal_scoped_and_resolves_only_after_required_tools(tmp_path):
    service, _sessions, task, started, runner, _planner = setup_agent(
        tmp_path, [call("request_clarification")]
    )
    runner.run_job(started["job_id"])
    tools = tools_from_waiting(service, task, started, runner)
    quote_id = tools.cases()[0].quote_id
    case = tools.requested_case(
        quote_id=quote_id,
        goal="先分析报价差距，再生成未发送的澄清草稿。",
        required_tools=("analyze_selection_gap", "draft_clarification"),
        allowed_tools=("analyze_selection_gap", "draft_clarification"),
        request_id="requested-gap-and-draft",
    )
    planner = ScriptedPlanner([
        call("analyze_selection_gap"),
        call("draft_clarification"),
    ])
    completed = InvestigationRunner(planner).run((case,), tools, tools.save)[0]
    assert completed.status == CaseStatus.RESOLVED
    assert completed.stop_reason == "REQUEST_COMPLETED"
    assert [item.result.tool_name for item in completed.observations] == [
        "analyze_selection_gap",
        "draft_clarification",
    ]
    assert "request_clarification" not in planner.calls[0][1]
    denied = tools.execute(case, "request_clarification", {})
    assert denied.status == "DENIED" and denied.error_code == "tool_not_allowed"
    replayed = InvestigationRunner(ScriptedPlanner([])).run((completed,), tools, tools.save)[0]
    assert replayed.stop_reason == "REQUEST_COMPLETED"


@pytest.mark.skipif(os.getenv('RUN_AGENT_LIVE_TESTS') != '1', reason='set RUN_AGENT_LIVE_TESTS=1 for paid live Agent acceptance')
@pytest.mark.parametrize('scenario', ['gap_and_draft', 'authorized_simulation', 'policy_recovery', 'policy_missing', 'policy_conflict'])
def test_live_agent_selects_tools_observes_results_and_stops(tmp_path, scenario):
    """Opt-in maintained acceptance: live decisions, synthetic facts/faults, no private PDFs."""
    from supplier_comparison.rag.contracts import RetrievalStatus
    from tests.backend.test_policy_investigation import BINDING, FlakyRetriever

    config = AgentConfig.from_env()
    assert config.model_id and os.getenv(config.api_key_env), 'Configure the existing model and API key.'
    planner = LiveInvestigationPlanner(config)
    agent = InvestigationRunner(planner, limits=AgentLimits(max_model_calls=8, max_tool_calls=10, max_seconds=90))
    if scenario.startswith('policy_'):
        status = {'policy_recovery': RetrievalStatus.ERROR, 'policy_missing': RetrievalStatus.NO_EVIDENCE,
                  'policy_conflict': RetrievalStatus.CONFLICT}[scenario]
        retriever = FlakyRetriever(failures=1 if scenario == 'policy_recovery' else 99, status=status,
                                  error_code='policy_transport_transient' if status == RetrievalStatus.ERROR else None)
        service, _sessions, task, started, runner = _run_impact_quotes(
            tmp_path, policy_retriever=retriever, policy_binding=BINDING)
        runner.investigator = agent
        outcome = runner.run_job(started['job_id'])
        case = next(c for c in service.list_investigations(task['task_id']) if c['kind'] == 'POLICY')
        names = [o['result']['tool_name'] for o in case['observations']]
        if scenario == 'policy_recovery':
            assert 'retry_policy_retrieval' in names
            assert outcome['status'] == 'SUCCEEDED' and case['stop_reason'] == 'EVIDENCE_CONFIRMED'
            assert retriever.rohs_calls == 2
        else:
            assert outcome['status'] == 'WAITING_INPUT' and retriever.rohs_calls == 1
            assert 'retry_policy_retrieval' not in names
            assert case['status'] == 'WAITING_INPUT'
            assert service.get_task(task['task_id'])['current_result_id'] is None
    else:
        service, _sessions, task, started, runner, _ = setup_agent(tmp_path, [])
        state = {'task_id': task['task_id'], 'graph_run_id': started['graph_run_id'], 'task_revision': 3}
        for step in (runner._load_context, runner._extract_documents, runner._review_quotes, runner._analyze_decision_impact):
            state.update(step(state))
        authorization = {'budget_amount': '9000'} if scenario == 'authorized_simulation' else None
        tools = ScopedInvestigationTools(service, task_id=task['task_id'], graph_run_id=started['graph_run_id'],
            task_revision=3, impact_artifact_id=state['decision_impact_artifact_id'], evaluated_at=runner.evaluated_at,
            authorized_requirement_changes=authorization)
        goal = ('请分析该报价与当前可行方案的成本及到货差距，形成供应商沟通草稿；仍缺信息时集中请求我补充，不能猜测运费。'
                if scenario == 'gap_and_draft' else
                '我仅授权假设预算为 SGD 9000 的试算，请使用已授权参数查看假设结果，正式需求不变；仍缺运费时集中请求我补充。')
        initial = tools.cases()[0].model_copy(update={'goal': goal})
        before = service.get_task(task['task_id'])
        result = agent.run((initial,), tools, tools.save)[0]
        case = result.model_dump(mode='json')
        names = [o.result.tool_name for o in result.observations]
        required = {'analyze_selection_gap', 'draft_clarification'} if scenario == 'gap_and_draft' else {'simulate_requirement_change'}
        diagnostic = {'tools': names, 'status': result.status.value, 'stop_reason': result.stop_reason,
                      'error_code': result.error_code, 'provider_calls': planner.telemetry,
                      'tool_errors': [o.result.error_code for o in result.observations]}
        assert required <= set(names), diagnostic
        assert result.status == CaseStatus.WAITING_INPUT and result.clarification, json.dumps(diagnostic, ensure_ascii=True)
        assert result.stop_reason != 'MODEL_UNAVAILABLE' and result.error_code is None
        assert service.get_task(task['task_id']) == before
        assert all(o.result.status not in {'DENIED', 'ERROR', 'STALE'} for o in result.observations), diagnostic
    assert planner.telemetry and all(t['status'] == 'OK' for t in planner.telemetry)
    assert case['model_calls'] <= 8 and len(case['observations']) <= 10
    print(json.dumps({'scenario': scenario, 'model_id': config.model_id, 'status': case['status'],
        'stop_reason': case['stop_reason'], 'tools': names, 'model_calls': case['model_calls'],
        'tokens': sum(t['usage'].get('total_tokens', 0) for t in planner.telemetry),
        'observations': [{'tool': o['result']['tool_name'], 'status': o['result']['status'], 'reason': o['reason']}
                         for o in case['observations']]}, ensure_ascii=False))
