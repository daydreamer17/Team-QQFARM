from __future__ import annotations

import pytest
import json
import urllib.error

from supplier_comparison.rag.contracts import PolicyExplanation
from supplier_comparison.rag.explanation import FixedExplanationClient, PolicyExplanationService

from .test_contracts import _citation
from .test_clients import FakeResponse
from supplier_comparison.rag.explanation import ExplanationConfig, LiveExplanationClient
from supplier_comparison.rag.clients import ModelClientError


def test_explanation_is_limited_to_confirmed_facts_and_current_citations() -> None:
    from supplier_comparison.rag.contracts import RetrievalResult, RetrievalStatus

    result = RetrievalResult(
        retrieval_id="RET-1",
        status=RetrievalStatus.OK,
        policy_set_version="electronics-v1",
        policy_index_version="idx-1",
        embedding_model="BAAI/bge-m3",
        rerank_model="BAAI/bge-reranker-v2-m3",
        filters={},
        covered_control_codes=["QUOTE_COMPLETENESS"],
        missing_control_codes=[],
        citations=[_citation()],
        candidates=[],
        latency_ms={},
        attempts={},
    )
    expected = PolicyExplanation(
        explanation_id="EXP-1",
        retrieval_id="RET-1",
        confirmed_facts={"currency": "SGD"},
        claims=[{"text": "Shipping is required.", "citation_ids": ["CIT-1"]}],
    )
    service = PolicyExplanationService(FixedExplanationClient(expected))
    assert service.explain(result, confirmed_facts={"currency": "SGD"}) == expected

    with pytest.raises(ValueError, match="confirmed facts"):
        service.explain(result, confirmed_facts={"currency": "USD"})


def payload(claim=None, finish="stop"):
    claim = claim or {"text": "报价必须说明币种、单价、数量和运费。", "citation_ids": ["CIT-1"],
                      "evidence_quotes": {"CIT-1": _citation().text}}
    return {"choices": [{"finish_reason": finish,
                         "message": {"content": json.dumps({"claims": [claim]})}}],
            "usage": {"total_tokens": 100, "provider_secret": "not-recorded"}}


def test_live_adapter_sends_bounded_context_and_records_safe_usage(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "test-secret")
    captured = []
    def opener(request, timeout):
        captured.append(json.loads(request.data))
        return FakeResponse(payload())
    client = LiveExplanationClient(ExplanationConfig(model_id="test-model", api_key_env="TEST_EXPLAIN_KEY"), opener=opener)
    facts = {"currency": "SGD", "supplier_rohs_record": None}
    result = client.explain(retrieval_id="RET-1", confirmed_facts=facts, citations=[_citation()])
    assert result.confirmed_facts == facts
    assert result.retrieval_id == "RET-1"
    assert result.claims[0].evidence_quotes["CIT-1"] == _citation().text
    assert captured[0]["temperature"] == 0
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert client.telemetry[0]["usage"] == {"total_tokens": 100}
    assert "test-secret" not in json.dumps(client.telemetry)


@pytest.mark.parametrize("changes", [
    {"citation_ids": ["OTHER"], "evidence_quotes": {"OTHER": _citation().text}},
    {"evidence_quotes": {"CIT-1": "A fabricated claim about RoHS approval"}},
    {"evidence_quotes": {}}, {"evidence_quotes": {"CIT-1": "quote"}},
    {"citation_ids": ["CIT-1", "CIT-1"]}, {"decision": "COMPLIANT"},
    {"text": "Every quote must state currency."},
])
def test_live_adapter_rejects_unsupported_quotes_and_model_decisions(monkeypatch, changes):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "test-secret")
    claim = {"text": "说明", "citation_ids": ["CIT-1"], "evidence_quotes": {"CIT-1": _citation().text}} | changes
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=lambda *a, **k: FakeResponse(payload(claim)))
    with pytest.raises(ModelClientError, match="failed validation"):
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])
    assert client.telemetry[-1]["status"] == "ERROR"


def test_live_adapter_rejects_truncation(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "key")
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=lambda *a, **k: FakeResponse(payload(finish="length")))
    with pytest.raises(ModelClientError):
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])


def test_explanation_transport_retries_are_bounded_and_do_not_leak_provider_body(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "key")
    calls = []
    def opener(*a, **k):
        calls.append(1)
        raise urllib.error.HTTPError("https://example.org", 503, "private body", {}, None)
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=opener, sleeper=lambda _: None)
    with pytest.raises(ModelClientError) as error:
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])
    assert len(calls) == 2 and error.value.attempts == 2
    assert "private body" not in str(error.value)


def test_explanation_missing_key_makes_no_request(monkeypatch):
    monkeypatch.delenv("TEST_EXPLAIN_KEY", raising=False)
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(ModelClientError) as error:
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])
    assert error.value.attempts == 0


def test_failed_retrieval_never_calls_explanation_model():
    from supplier_comparison.rag.contracts import RetrievalResult
    result = RetrievalResult(retrieval_id="RET-1", status="ERROR", policy_set_version="v1",
        policy_index_version="idx", embedding_model="test", rerank_model="test", filters={},
        covered_control_codes=[], missing_control_codes=["TOTAL_COST"], citations=[], candidates=[],
        latency_ms={}, attempts={})
    client = FixedExplanationClient(None)
    with pytest.raises(ValueError, match="successful"):
        PolicyExplanationService(client).explain(result, confirmed_facts={})
    assert not client.calls


def test_bundle_input_is_revalidated_before_model_calls():
    from .test_orchestration import service, context
    from supplier_comparison.rag.cli import validate_explanation_bundle
    orchestrator, _ = service()
    bundle = orchestrator.retrieve(context())
    validate_explanation_bundle(bundle, orchestrator.manifest)
    first = bundle.retrieval_results[0]
    bad = first.citations[0].model_copy(update={"text": "tampered"})
    invalid = bundle.model_copy(update={"retrieval_results": [first.model_copy(update={"citations": [bad]})]})
    with pytest.raises(ValueError, match="authoritative"):
        validate_explanation_bundle(invalid, orchestrator.manifest)


def test_explanation_configuration_has_independent_bounded_settings(monkeypatch):
    monkeypatch.setenv("SUPPLIER_MODEL_MODEL_ID", "configured-chat-model")
    monkeypatch.delenv("SUPPLIER_EXPLANATION_MODEL_ID", raising=False)
    assert ExplanationConfig.from_env().model_id == "configured-chat-model"
    monkeypatch.setenv("SUPPLIER_EXPLANATION_MODEL_ID", "explanation-only-model")
    assert ExplanationConfig.from_env().model_id == "explanation-only-model"
    with pytest.raises(ValueError):
        ExplanationConfig(model_id="test", max_attempts=3)


@pytest.mark.parametrize("url", ["http://external.example/v1", "https://user:secret@example.org/v1"])
def test_explanation_secrets_require_secure_endpoint(url):
    with pytest.raises(ValueError):
        ExplanationConfig(model_id="test", base_url=url)


@pytest.mark.parametrize("fail", [False, True])
def test_explain_plan_cli_records_success_or_fail_closed(tmp_path, monkeypatch, fail):
    from .test_orchestration import service, context
    from supplier_comparison.rag import cli
    orchestrator, _ = service()
    bundle = orchestrator.retrieve(context())
    bundle_file, facts_file, output = tmp_path / "bundle.json", tmp_path / "facts.json", tmp_path / "output.json"
    bundle_file.write_text(bundle.model_dump_json(), encoding="utf-8")
    facts_file.write_text(json.dumps({"currency": "SGD", "total_cost": "7000.00"}), encoding="utf-8")
    monkeypatch.setattr(cli, "load_reviewed_catalog", lambda *a, **k: orchestrator.manifest)
    monkeypatch.setenv("SUPPLIER_EXPLANATION_MODEL_ID", "test-model")
    class Client:
        telemetry = []
        def explain(self, *, retrieval_id, confirmed_facts, citations):
            self.telemetry.append({"status": "ERROR" if fail else "OK"})
            if fail:
                raise ModelClientError("safe error", attempts=1)
            return PolicyExplanation(explanation_id="EXP-test", retrieval_id=retrieval_id,
                confirmed_facts=confirmed_facts, claims=[{"text": "制度说明", "citation_ids": [c.citation_id],
                "evidence_quotes": {c.citation_id: c.text}} for c in citations])
    monkeypatch.setattr(cli, "LiveExplanationClient", lambda _: Client())
    args = ["explain-plan", "--bundle", str(bundle_file), "--facts", str(facts_file),
            "--manifest", "data/policies/electronics-v2/manifest.json", "--output", str(output)]
    assert cli.main(args) == int(fail)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == ("ERROR" if fail else "OK")
    if fail:
        assert report["explanations"] == []
    else:
        assert report["explanations"]
        assert report["snapshot_id"] == bundle.plan.context.snapshot_id
    facts_file.write_text(json.dumps({"currency": "USD"}), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen planning context"):
        cli.main(args)


def test_validation_regeneration_uses_specific_feedback_and_same_facts(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "key")
    requests = []
    bad = {"text": "English only", "citation_ids": ["CIT-1"],
           "evidence_quotes": {"CIT-1": _citation().text}}
    def opener(request, timeout):
        requests.append(json.loads(request.data))
        return FakeResponse(payload(bad) if len(requests) == 1 else payload())
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=opener, sleeper=lambda _: None)
    result = client.explain(retrieval_id="RET-1", confirmed_facts={"currency": "SGD"}, citations=[_citation()])
    assert result.confirmed_facts == {"currency": "SGD"}
    assert len(requests) == 2
    assert "explanation_language_invalid" in requests[1]["messages"][0]["content"]
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert [t["status"] for t in client.telemetry] == ["ERROR", "OK"]


def test_bad_quote_remains_rejected_after_two_attempts(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "key")
    bad = {"text": "说明", "citation_ids": ["CIT-1"],
           "evidence_quotes": {"CIT-1": "private fabricated provider text"}}
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
        opener=lambda *a, **k: FakeResponse(payload(bad)), sleeper=lambda _: None)
    with pytest.raises(ModelClientError) as error:
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])
    assert error.value.error_code == "explanation_evidence_quote_invalid"
    assert error.value.attempts == 2 and len(client.telemetry) == 2
    assert all(t["clause_ids"] == ["QUOTE-001"] for t in client.telemetry)
    assert "private fabricated" not in json.dumps(client.telemetry)


def test_transport_and_validation_share_the_same_two_call_budget(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "key")
    calls = []
    def opener(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://example.org", 503, "private", {}, None)
        return FakeResponse(payload(finish="length"))
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=opener, sleeper=lambda _: None)
    with pytest.raises(ModelClientError) as error:
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])
    assert len(calls) == 2 and error.value.attempts == 2
    assert error.value.error_code == "explanation_output_truncated"


def test_authentication_failure_is_not_retried(monkeypatch):
    monkeypatch.setenv("TEST_EXPLAIN_KEY", "key")
    calls = []
    def opener(*a, **k):
        calls.append(1)
        raise urllib.error.HTTPError("https://example.org", 401, "private", {}, None)
    client = LiveExplanationClient(ExplanationConfig(model_id="test", api_key_env="TEST_EXPLAIN_KEY"),
                                   opener=opener, sleeper=lambda _: None)
    with pytest.raises(ModelClientError):
        client.explain(retrieval_id="RET-1", confirmed_facts={}, citations=[_citation()])
    assert len(calls) == 1
