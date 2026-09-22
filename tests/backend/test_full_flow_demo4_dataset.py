"""Independent demo4 oracle + real parsers/rules/persisted workflow.

No live HTTP model is used here. PDF recovery tests explicitly inject candidates;
they do not count as extraction accuracy. Live evaluation is a separate script.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendService, ConflictError
from supplier_comparison.backend.workflow import DefaultQuoteProcessor, DraftReviewRunner, WorkflowRunner
from supplier_comparison.extraction.contracts import CandidateProducer, DocumentContext, SourceCitation, ValidationStatus
from supplier_comparison.extraction.hybrid_csv import RegisteredHybridCsvParser
from supplier_comparison.extraction.csv_parser import FROZEN_CSV_COLUMNS, FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.rag.uploads import PolicyFileImportMetadata, PolicyDraftClauseInput, _draft_clauses
from supplier_comparison.rules import ComparisonRequest, DecisionPreferences, ProcurementRequirement, compare_suppliers
from supplier_comparison.rules.integration import quote_input_from_extraction
from supplier_comparison.rules.selection_gap import RequirementChanges

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/generated/inputs/development/full_flow_demo4"
REF = ROOT / "evaluation/reference/full_flow_demo4"
DICTIONARY = ROOT / "data/contracts/quote_data_field.csv"
ANSWERS = json.loads((REF / "reference_answers.json").read_text())
EVALUATED_AT = datetime.fromisoformat(ANSWERS["evaluated_at"].replace("Z", "+00:00"))


def row_at(path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(FROZEN_CSV_COLUMNS)
        rows = list(reader)
    assert len(rows) == 1
    return rows[0]


def parsed_csv(path):
    row = row_at(path)
    context = DocumentContext(task_id="TEST-FF4", task_revision=1, scenario_id=row["scenario_id"],
        quote_id=row["quote_id"], quote_version=1, document_id=row["document_id"], document_version=1,
        supplier_id=row["supplier_id"])
    return FixedCsvQuoteParser(QuoteDictionary.load(DICTIONARY)).parse_row(path, context, 2)


def rule_request(changes=None, variant=None):
    requirement = json.loads((DATA / "requirement/confirmed_requirement.json").read_text())
    changes = changes or {}
    requirement.update({k: v for k, v in changes.items() if k in {"budget_amount", "delivery_deadline"}})
    preferences = {"primary_criterion": "LOWEST_CONFIRMED_TOTAL_COST"}
    preferences.update({k: v for k, v in changes.items() if k not in {"budget_amount", "delivery_deadline"}})
    paths = list(sorted((DATA / "quotes/csv").glob("*.csv")))
    if variant:
        replacement = next((DATA / "variants" / variant).glob("*.csv"))
        paths = [replacement if p.name == replacement.name else p for p in paths]
    batches = [parsed_csv(p) for p in paths]
    # Explicit simulated HUMAN acceptance for pure rules tests, not parser accuracy.
    quotes = []
    for batch in batches:
        candidates = tuple(c.model_copy(update={"validation_status": ValidationStatus.VERIFIED})
                           if c.normalized_value is not None else c for c in batch.candidates)
        quotes.append(quote_input_from_extraction(batch.model_copy(update={"candidates": candidates})))
    profile = DecisionPreferences.model_validate(preferences)
    requirement.update(ranking_preference=profile.primary_criterion, secondary_preference=profile.secondary_criterion)
    return ComparisonRequest(requirement=ProcurementRequirement.model_validate(requirement), quotes=tuple(quotes),
        evaluated_at=EVALUATED_AT, cost_tolerance_amount=profile.cost_tolerance_amount,
        excluded_quote_ids=tuple(q.quote_id for q in quotes if q.supplier_id in profile.excluded_supplier_ids))


def winner_ids(result, request):
    by_id = {q.quote_id: q.supplier_id for q in request.quotes}
    return sorted(by_id[qid] for qid in result.recommended_quote_ids)


def test_manifest_exact_inventory_and_no_oracle_leak():
    manifest = json.loads((DATA / "manifest.json").read_text())
    assert manifest["dataset_id"] == "full_flow_demo4"
    assert len(manifest["primary_quotes"]) == 4
    assert len(manifest["variants"]) == 13
    assert {entry["path"] for entry in manifest["files"]} == {
        p.relative_to(DATA).as_posix() for p in DATA.rglob("*") if p.is_file() and p.name != "manifest.json"}
    for entry in manifest["files"]:
        path = DATA / entry["path"]
        assert DATA in path.resolve().parents
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert path.stat().st_size == entry["size_bytes"]
        assert path.name not in {"reference_answers.json", "conversation_cases.json", "parsing_expectations.json"}
    assert ANSWERS["runtime_access"] == "FORBIDDEN"


@pytest.mark.parametrize("path", sorted(DATA.rglob("*.pdf")), ids=lambda p: str(p.relative_to(DATA)))
def test_pdf_pages_extract_and_have_real_source_locations(path):
    context = DocumentContext(task_id="PDF-FF4", task_revision=1, quote_id="Q-PDF", quote_version=1,
                              document_id="D-PDF", document_version=1)
    parsed = PdfQuoteParser().parse(path, context)
    assert parsed.sources
    assert len({s.source_id for s in parsed.sources}) == len(parsed.sources)
    assert all(s.document_sha256 == hashlib.sha256(path.read_bytes()).hexdigest() for s in parsed.sources)
    with pdfplumber.open(path) as pdf:
        assert 1 <= len(pdf.pages) <= 5
        assert all(page.extract_text() for page in pdf.pages)
        if path.parent.name == "pdf" and path.name == "schwarzwald_quote.pdf":
            assert len(pdf.pages) == 2
            assert "200.00" in pdf.pages[1].extract_text()


def test_baseline_totals_against_independent_oracle():
    request = rule_request()
    result = compare_suppliers(request)
    qids = {q.quote_id: q.supplier_id for q in request.quotes}
    for item in result.supplier_results:
        expected = ANSWERS["baseline"][qids[item.quote_id]]
        assert item.status.value == "FEASIBLE", item
        assert item.actual_quantity == expected["actual_quantity"]
        assert item.goods_cost == Decimal(expected["goods_cost"])
        assert item.total_cost == Decimal(expected["total_cost"])
        assert item.estimated_arrival_date.isoformat() == expected["delivery_date"]
        assert item.payment_term.net_days == expected["payment_days"]


@pytest.mark.parametrize("case", ANSWERS["decision_cases"], ids=lambda c: c["id"])
def test_decision_boundaries_and_upload_order_invariance(case):
    request = rule_request(case["changes"])
    result = compare_suppliers(request)
    assert winner_ids(result, request) == sorted(case["winner_ids"]), result
    if "feasible_ids" in case:
        ids = {q.quote_id: q.supplier_id for q in request.quotes}
        assert sorted(ids[r.quote_id] for r in result.supplier_results if r.status.value == "FEASIBLE") == sorted(case["feasible_ids"])
    reversed_request = request.model_copy(update={"quotes": tuple(reversed(request.quotes))})
    assert winner_ids(compare_suppliers(reversed_request), reversed_request) == sorted(case["winner_ids"])


@pytest.mark.parametrize("variant", ["pack_moq", "freight_included", "freight_free", "fee_wording", "tie", "wrong_part", "dominated"])
def test_variant_decisions(variant):
    request = rule_request(variant=variant)
    result = compare_suppliers(request)
    expected = ANSWERS["variant_expectations"][variant]
    if "winner_ids" in expected:
        assert winner_ids(result, request) == sorted(expected["winner_ids"])
    if "supplier_id" in expected:
        qid = next(q.quote_id for q in request.quotes if q.supplier_id == expected["supplier_id"])
        item = next(r for r in result.supplier_results if r.quote_id == qid)
        if "total_cost" in expected:
            assert item.total_cost == Decimal(expected["total_cost"])
        if "feasibility" in expected:
            assert item.status.value == expected["feasibility"]
        if "actual_quantity" in expected:
            assert item.actual_quantity == expected["actual_quantity"]
        if "reason" in expected:
            assert expected["reason"] in {r.code for r in item.failed_reasons}


def test_missing_freight_is_unknown_and_answers_flip_choice():
    request = rule_request(variant="missing_freight")
    result = compare_suppliers(request)
    assert not result.final_recommendation_allowed
    quote = next(q for q in request.quotes if q.supplier_id == "SUP-029")
    item = next(r for r in result.supplier_results if r.quote_id == quote.quote_id)
    assert item.total_cost is None
    assert item.known_cost_subtotal == Decimal("6300.00")
    for answer in ANSWERS["variant_expectations"]["missing_freight"]["human_answers"]:
        values = {"shipping_fee_status": "KNOWN_AMOUNT", "shipping_fee_amount": answer["amount"], "fees_complete": True}
        candidates = tuple(c.model_copy(update={"normalized_value": values[c.field_name], "validation_status": ValidationStatus.VERIFIED})
                           if c.field_name in values else c for c in quote.candidates)
        modified = quote.model_copy(update={"candidates": candidates})
        amended = request.model_copy(update={"quotes": tuple(modified if q.quote_id == quote.quote_id else q for q in request.quotes)})
        assert winner_ids(compare_suppliers(amended), amended) == sorted(answer["winner_ids"])


def test_policy_upload_contract_and_scope_isolation():
    for scope in ("electronics_sg", "unrelated_office_eu"):
        directory = DATA / "policy" / scope
        metadata = PolicyFileImportMetadata.model_validate_json((directory / "upload_metadata.json").read_text())
        reviewed = [PolicyDraftClauseInput.model_validate(c) for c in json.loads((directory / "reviewed_clauses.json").read_text())["clauses"]]
        extracted = _draft_clauses((directory / "policy.txt").read_text(), fallback_title=metadata.title)
        assert [(c["clause_id"], c["text"]) for c in extracted] == [(c.clause_id, c.text) for c in reviewed]
        assert set(c.control_code for c in reviewed) <= {"APPROVED_SUPPLIER", "ROHS_COMPLIANCE", "AMOUNT_APPROVAL"}
        assert (metadata.categories, metadata.regions) == ((["Electronics"], ["SG"]) if scope == "electronics_sg" else (["Office Furniture"], ["EU"]))


def test_policy_files_publish_with_fixed_embeddings_and_filter_scope(service, tmp_path):
    from supplier_comparison.rag.clients import FixedEmbeddingClient
    from supplier_comparison.rag.importer import PolicyImporter
    from supplier_comparison.rag.uploads import PolicyFileImportService
    texts = [c["text"] for path in (DATA / "policy").glob("*/reviewed_clauses.json")
             for c in json.loads(path.read_text())["clauses"]]
    embedding = FixedEmbeddingClient({text: [0.2]*1024 for text in texts}, model_id="BAAI/bge-m3", dimension=1024)
    importer = PolicyImporter(service.session_factory, embedding, allowed_root=tmp_path / "policies", provider="fixed")
    files = PolicyFileImportService(service.session_factory, tmp_path / "policy-files", importer, actor_id=service.actor_id)
    published = {}
    for directory in sorted((DATA / "policy").iterdir()):
        metadata = PolicyFileImportMetadata.model_validate_json((directory / "upload_metadata.json").read_text())
        with (directory / "policy.txt").open("rb") as stream:
            draft = files.upload_stream(metadata=metadata, original_filename="policy.txt", media_type="text/plain", stream=stream,
                                        idempotency_key=f"policy-upload-{directory.name}")
        reviewed = files.replace_clauses(draft["policy_import_id"], expected_revision=draft["revision"],
            clauses=[PolicyDraftClauseInput.model_validate(c) for c in json.loads((directory / "reviewed_clauses.json").read_text())["clauses"]],
            idempotency_key=f"policy-review-{directory.name}")
        result = files.publish(draft["policy_import_id"], expected_revision=reviewed["revision"], idempotency_key=f"policy-publish-{directory.name}")
        assert result["status"] == "PUBLISHED"
        published[directory.name] = result
    matching = files.list_policy_sets(category="Electronics", region="SG")
    assert matching["total"] == 1
    assert matching["items"][0]["policy_set_id"] == "full-flow-demo4-electronics_sg"
    unrelated = files.list_policy_sets(category="Office Furniture", region="EU")
    assert unrelated["total"] == 1
    selected = published["electronics_sg"]
    task = service.create_task(ProcurementRequirement.model_validate_json((DATA / "requirement/confirmed_requirement.json").read_text()),
        idempotency_key="policy-bound-task", scenario_id="MCU-TRADEOFF-004", policy_set_version=selected["policy_set_version"],
        policy_index_version=selected["policy_index_version"], policy_category="Electronics", policy_region="SG")
    assert task["policy_binding"]["policy_index_version"] == selected["policy_index_version"]


@pytest.fixture
def service(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    result = BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path / "storage", actor_id="demo4-test", quote_dictionary_path=DICTIONARY)
    yield result
    engine.dispose()


def review_actions(current):
    return [{"field_name": f["field_name"], "expected_field_id": f["field_id"], "expected_field_version": f["field_version"],
             "action": {"MISSING": "CONFIRM_MISSING", "CONFLICT": "CONFIRM_CONFLICT"}.get(f["validation_status"], "CONFIRM_VALUE")}
            for f in current["fields"]]


def upload_and_review(service, task, path, *, processor=None, actions_transform=None):
    row = row_at(path)
    with path.open("rb") as stream:
        draft = service.upload_quote_draft_stream(task["task_id"], expected_task_revision=task["task_revision"],
            supplier_id=row["supplier_id"], original_filename=path.name, media_type="text/csv", stream=stream,
            idempotency_key=f"upload-{path.parent.name}-{path.stem}", is_synthetic=True, provider="fixed",
            model_id="fixed-output", environment="FIXED_TEST", prompt_version="quote-extraction/2.2.0")
    processed = DraftReviewRunner(service, processor=processor or DefaultQuoteProcessor(service.quote_dictionary), dictionary_path=DICTIONARY).run_job(draft["job"]["job_id"])
    assert processed["status"] == "REVIEW_REQUIRED", processed
    current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    actions = review_actions(current)
    if actions_transform:
        actions_transform(actions)
    reviewed = service.review_quote_draft(task["task_id"], draft["quote_draft_id"], expected_draft_revision=current["draft_revision"],
        schema_version=service.quote_field_schema()["schema_version"], actions=actions, idempotency_key=f"review-{path.stem}")
    return draft, reviewed


def completed_task(service):
    task = service.create_task(ProcurementRequirement.model_validate_json((DATA / "requirement/confirmed_requirement.json").read_text()),
                               idempotency_key="create-demo4", scenario_id="MCU-TRADEOFF-004")
    for path in sorted((DATA / "quotes/csv").glob("*.csv")):
        draft, reviewed = upload_and_review(service, task, path)
        assert reviewed["submission_ready"], reviewed["review_errors"]
        service.submit_quote_draft(task["task_id"], draft["quote_draft_id"], expected_task_revision=task["task_revision"],
            expected_draft_revision=reviewed["draft_revision"], idempotency_key=f"submit-{path.stem}")
        task = service.get_task(task["task_id"])
    started = service.start_run(task["task_id"], expected_task_revision=task["task_revision"], idempotency_key="start-demo4",
        provider="fixed", model_id="fixed-output", environment="FIXED_TEST", prompt_version="quote-extraction/2.2.0")
    runner = WorkflowRunner(service, processor=DefaultQuoteProcessor(service.quote_dictionary), checkpointer=InMemorySaver(),
                            dictionary_path=DICTIONARY, evaluated_at=EVALUATED_AT)
    runner.run_job(started["job_id"])
    task = service.get_task(task["task_id"])
    assert task["current_result_id"], task
    return task, runner


def test_real_csv_review_workflow_scenarios_apply_stale_and_history(service):
    task, runner = completed_task(service)
    task_id, revision = task["task_id"], task["task_revision"]
    baseline_id = task["current_result_id"]
    quotes = service.list_quotes(task_id)["items"]
    suppliers = {q["quote_id"]: q["supplier_id"] for q in quotes}
    baseline = service.get_result(task_id, baseline_id)["result"]
    assert [suppliers[q] for q in baseline["recommended_quote_ids"]] == ["SUP-029"]
    # Payment confirmation loss has its own strict known-bug regression below.
    cases = [c for c in ANSWERS["decision_cases"] if c["changes"] and c["id"] != "payment"]
    cases += [{"id": k, "changes": {"primary_criterion": k}, "winner_ids": v}
              for k, v in ANSWERS["history"]["with_binding_winners"].items()]
    scenarios = []
    for case in cases:
        scenario = service.create_decision_scenario(task_id, expected_task_revision=revision,
            changes=RequirementChanges.model_validate(case["changes"]), idempotency_key=case["id"])
        result = scenario["simulated"]["comparison"]
        assert sorted(suppliers[q] for q in result["recommended_quote_ids"]) == sorted(case["winner_ids"]), (case, result)
        scenarios.append(scenario)
    selected = scenarios[0]
    kwargs = dict(expected_task_revision=revision, idempotency_key="apply-delivery", provider="fixed", model_id="fixed-output", environment="FIXED_TEST")
    applied = service.apply_decision_scenario(task_id, selected["decision_scenario_id"], **kwargs)
    assert service.apply_decision_scenario(task_id, selected["decision_scenario_id"], **kwargs) == applied
    assert applied["task_revision"] == revision + 1
    assert service.get_task(task_id)["current_result_id"] is None
    with pytest.raises(ConflictError):
        service.apply_decision_scenario(task_id, scenarios[1]["decision_scenario_id"], expected_task_revision=revision + 1, idempotency_key="stale-apply")
    runner.run_job(applied["job_id"])
    current = service.get_task(task_id)
    result = service.get_result(task_id, current["current_result_id"])["result"]
    assert [suppliers[q] for q in result["recommended_quote_ids"]] == ["SUP-023"]
    assert service.get_result(task_id, baseline_id)["result"] == baseline


def test_confirmed_payment_remains_comparable_in_persisted_scenario(service):
    task, runner = completed_task(service)
    rerun = service.start_run(task["task_id"], expected_task_revision=task["task_revision"],
        idempotency_key="payment-rerun", provider="fixed", model_id="fixed-output",
        environment="FIXED_TEST", prompt_version="quote-extraction/2.2.0")
    runner.run_job(rerun["job_id"])
    task = service.get_task(task["task_id"])
    scenario = service.create_decision_scenario(task["task_id"], expected_task_revision=task["task_revision"],
        changes=RequirementChanges(primary_criterion="LONGEST_CONFIRMED_PAYMENT_TERM"), idempotency_key="payment")
    quotes = service.list_quotes(task["task_id"])["items"]
    suppliers = {q["quote_id"]: q["supplier_id"] for q in quotes}
    comparison = scenario["simulated"]["comparison"]
    assert [suppliers[q] for q in comparison["recommended_quote_ids"]] == ["SUP-022"]
    assert all(r["payment_term"]["parse_status"] == "COMPARABLE" for r in comparison["supplier_results"])


@pytest.mark.parametrize("value", [None, "700.00"])
def test_pdf_human_same_or_changed_value_unblocks_semantic_doubts(service, value):
    """Real PDF + fixed candidates, followed by real review/save/reload/submit."""
    class FixedCandidates:
        def process(self, *, path, media_type, context, budget):
            batch = RegisteredHybridCsvParser(service.quote_dictionary).parse_row(
                DATA / "quotes/csv/sterling_quote.csv", context, validate_authority=False).batch
            parsed = PdfQuoteParser().parse(path, context)
            candidates = []
            for c in batch.candidates:
                refs = ()
                if c.source_refs:
                    source = next((s for s in parsed.sources if str(c.normalized_value) in s.raw_text), parsed.sources[0])
                    refs = (SourceCitation(source_id=source.source_id, quoted_text=source.raw_text),)
                candidates.append(c.model_copy(update={"source_refs": refs, "producer": CandidateProducer.MODEL_ADAPTER,
                                                       "adapter_version": "demo4-fixed-recovery/1", "prompt_version": "demo4-fixed-recovery/1"}))
            return batch.model_copy(update={"schema_version": "1.1", "parsed_input": parsed, "candidates": tuple(candidates)})
    task = service.create_task(ProcurementRequirement.model_validate_json((DATA / "requirement/confirmed_requirement.json").read_text()),
                              idempotency_key="conflict-task", scenario_id="MCU-TRADEOFF-004")
    path = DATA / "variants/conflicting_prices/sterling_quote.pdf"
    with path.open("rb") as stream:
        draft = service.upload_quote_draft_stream(task["task_id"], expected_task_revision=1, supplier_id="SUP-024",
            original_filename=path.name, media_type="application/pdf", stream=stream, idempotency_key="conflict-upload",
            is_synthetic=True, provider="fixed", model_id="fixed-output", environment="FIXED_TEST", prompt_version="demo4-fixed-recovery/1")
    DraftReviewRunner(service, processor=FixedCandidates(), dictionary_path=DICTIONARY).run_job(draft["job"]["job_id"])
    current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    # Every field needs human review; this injected model picked one current rate.
    # Do not pretend the deterministic detector recognises every PDF ambiguity.
    assert "unit_price" in current["unconfirmed_fields"]
    assert current["submission_blocking_fields"]
    actions = review_actions(current)
    if value:
        action = next(a for a in actions if a["field_name"] == "unit_price")
        action.update(action="SET_VALUE", raw_value=value, normalized_value=value, unit="SGD", reason="Buyer obtained confirmation of the current rate per 100 pieces.")
    kwargs = dict(expected_draft_revision=current["draft_revision"], schema_version=service.quote_field_schema()["schema_version"],
                  actions=actions, idempotency_key="conflict-confirm")
    saved = service.review_quote_draft(task["task_id"], draft["quote_draft_id"], **kwargs)
    assert saved["submission_ready"], saved["review_errors"]
    assert service.review_quote_draft(task["task_id"], draft["quote_draft_id"], **kwargs) == saved
    reloaded = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert reloaded["submission_ready"]
    assert next(f for f in reloaded["fields"] if f["field_name"] == "unit_price")["normalized_value"] == (value or "680.00")
    assert any(f["resolved"] for f in reloaded["review_findings"])
    service.submit_quote_draft(task["task_id"], draft["quote_draft_id"], expected_task_revision=1,
        expected_draft_revision=reloaded["draft_revision"], idempotency_key="conflict-submit")
    assert service.get_quote_draft(task["task_id"], draft["quote_draft_id"])["status"] == "SUBMITTED"


@pytest.mark.parametrize("variant,field,updates", [
    ("missing_freight", "shipping_fee_status", {"shipping_fee_status": "KNOWN_AMOUNT", "shipping_fee_amount": "200.00", "fees_complete": True}),
    ("invalid_money", "unit_price", {"unit_price": "6.20"}),
])
def test_incomplete_or_invalid_draft_saves_and_human_repairs(service, variant, field, updates):
    task = service.create_task(ProcurementRequirement.model_validate_json((DATA / "requirement/confirmed_requirement.json").read_text()),
                              idempotency_key="repair-task", scenario_id="MCU-TRADEOFF-004")
    draft, saved = upload_and_review(service, task, DATA / f"variants/{variant}/great_wall_quote.csv")
    if variant == "missing_freight":
        assert saved["submission_ready"]
        assert not saved["calculation_ready"]
    else:
        assert not saved["submission_ready"]
        assert field in saved["submission_blocking_fields"]
        assert saved["review_errors"]
    actions = review_actions(saved)
    for action in actions:
        name = action["field_name"]
        if name in updates:
            action.update(action="SET_VALUE", raw_value=str(updates[name]), normalized_value=updates[name],
                          unit="SGD" if name.endswith("amount") or name == "unit_price" else None,
                          reason="Buyer checked the source and confirmed the final value.")
    repaired = service.review_quote_draft(task["task_id"], draft["quote_draft_id"], expected_draft_revision=saved["draft_revision"],
        schema_version=service.quote_field_schema()["schema_version"], actions=actions, idempotency_key="repair-values")
    assert repaired["submission_ready"], repaired["review_errors"]
    service.submit_quote_draft(task["task_id"], draft["quote_draft_id"], expected_task_revision=1,
        expected_draft_revision=repaired["draft_revision"], idempotency_key="submit-repair")


def test_history_absence_does_not_invent_a_winner():
    for criterion in ANSWERS["history"]["with_binding_winners"]:
        request = rule_request({"primary_criterion": criterion})
        result = compare_suppliers(request)
        assert not result.final_recommendation_allowed
        assert not result.recommended_quote_ids
    request = rule_request()
    assert winner_ids(compare_suppliers(request), request) == ["SUP-029"]


def test_conversation_proposal_apply_preserves_old_messages_and_new_facts(service):
    task, runner = completed_task(service)
    tid, revision = task["task_id"], task["task_revision"]
    conversation = service.create_decision_conversation(tid, expected_task_revision=revision, title="demo4", idempotency_key="chat")
    sent = service.send_decision_conversation_message(tid, conversation["conversation_id"], expected_task_revision=revision,
        content="成本优先，最多比最低价贵200新币，再选尽量早点到的。", idempotency_key="message")
    job_id = sent["job"]["job_id"]
    context = service.conversation_job_context(job_id)
    assert all(not r.startswith("REQUEST:") for r in context["allowed_reference_ids"])
    result_ref = f"RESULT:{context['result_id']}"
    completed = service.complete_conversation_job(job_id, turn={"assistant_text": f"当前成本优先的推荐来自冻结结果（{result_ref}）。",
        "reference_ids": [result_ref], "changes": {"primary_criterion": "LOWEST_CONFIRMED_TOTAL_COST",
        "secondary_criterion": "FASTEST_CONFIRMED_DELIVERY", "cost_tolerance_amount": "200.00"}},
        attempts=1, provider="fixed-test", model_id="demo4-fixed-conversation")
    assert completed["job_status"] == "SUCCEEDED"
    assert service.get_task(tid)["task_revision"] == revision
    scenario = service.create_decision_scenario(tid, expected_task_revision=revision,
        changes=RequirementChanges(primary_criterion="LOWEST_CONFIRMED_TOTAL_COST", secondary_criterion="FASTEST_CONFIRMED_DELIVERY", cost_tolerance_amount="200.00"),
        idempotency_key="confirmed-scenario")
    applied = service.apply_decision_scenario(tid, scenario["decision_scenario_id"], expected_task_revision=revision,
        idempotency_key="chat-apply", provider="fixed", model_id="fixed-output", environment="FIXED_TEST")
    runner.run_job(applied["job_id"])
    new_task = service.get_task(tid)
    assert new_task["current_result_id"] != context["result_id"]
    historical = service.get_decision_conversation(tid, conversation["conversation_id"])
    assert any("200" in m["content"] for m in historical["messages"] if m["role"] == "USER")
    assert historical["base_result_id"] == context["result_id"]
    assert any(c["conversation_id"] == conversation["conversation_id"] for c in service.list_decision_conversations(tid)["items"])


def test_generator_is_reproducible_and_keeps_reference_separate(tmp_path):
    output, holdout = tmp_path / "development", tmp_path / "holdout"
    subprocess.run([sys.executable, str(ROOT / "data/generate_full_flow_demo4.py"),
                    "--output-dir", str(output), "--holdout-dir", str(holdout)], check=True, cwd=ROOT)
    expected = {str(p.relative_to(DATA)): p.read_bytes() for p in DATA.rglob("*") if p.is_file()}
    actual = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert actual == expected
    assert not list(output.rglob("reference_answers.json"))
    # Private holdout files are deliberately absent on a clean checkout.
    # Reproduce them in pytest's temporary directory, never require local data.
    frozen = tmp_path / "repeat-holdout"
    subprocess.run([sys.executable, str(ROOT / "data/generate_full_flow_demo4.py"),
                    "--output-dir", str(tmp_path / "repeat-development"), "--holdout-dir", str(frozen)], check=True, cwd=ROOT)
    assert {str(p.relative_to(holdout)): p.read_bytes() for p in holdout.rglob("*") if p.is_file()} == {
        str(p.relative_to(frozen)): p.read_bytes() for p in frozen.rglob("*") if p.is_file()}


def test_dominated_quote_is_not_in_price_delivery_frontier():
    from supplier_comparison.backend.conversations import decision_fact_catalog
    request = rule_request(variant="dominated")
    result = compare_suppliers(request)
    facts = decision_fact_catalog(result.model_dump(mode="json"))
    suppliers = {q.quote_id: q.supplier_id for q in request.quotes}
    sterling = next(c for c in facts["candidates"] if suppliers[c["quote_id"]] == "SUP-024")
    assert not sterling["pareto_optimal"]
    assert sorted(suppliers[q] for q in sterling["dominated_by"]) == ["SUP-022", "SUP-023"]


@pytest.mark.skipif(os.getenv("RUN_DEMO4_LIVE") != "1", reason="Set RUN_DEMO4_LIVE=1 for paid live demo4 dialogue checks")
def test_live_baseline_conversation_scripts(service):
    """Paid opt-in: REAL narration on deterministic CSV + simulated human review.

    Reference expectations are used only after the model returns. Context passed
    to process_conversation_turn comes exclusively from the service's frozen DB.
    """
    from supplier_comparison.backend.conversations import ConversationModelConfig, process_conversation_turn
    from datetime import timezone
    from uuid import uuid4
    config = ConversationModelConfig.from_env()
    assert config is not None
    task, _ = completed_task(service)
    cases = json.loads((REF / "conversation_cases.json").read_text())["cases"]
    directory = ROOT / "evaluation/results/local/full_flow_demo4" / f"dialogue-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:6]}"
    directory.mkdir(parents=True)
    conversations, records = {}, []
    for case in cases:
        if "variant" in case:
            continue
        parent = conversations.get(case.get("after"))
        conversation = parent or service.create_decision_conversation(task["task_id"], expected_task_revision=task["task_revision"],
            title=case["id"], idempotency_key=f"live-create-{case['id']}")
        conversations[case["id"]] = conversation
        sent = service.send_decision_conversation_message(task["task_id"], conversation["conversation_id"], expected_task_revision=task["task_revision"],
            content=case["prompt"], idempotency_key=f"live-message-{case['id']}")
        job_id = sent["job"]["job_id"]
        context = service.conversation_job_context(job_id)
        record = {"case_id": case["id"], "mode": "REAL_MODEL", "fixture_input": "DETERMINISTIC_CSV_WITH_SIMULATED_HUMAN_REVIEW",
                  "model_received_reference_answers": False, "prompt": case["prompt"], "model_id": config.model_id,
                  "provider": config.provider, "job_id": job_id}
        try:
            turn, attempts = process_conversation_turn(context, config)
            record.update(turn=turn, attempts=attempts)
            complete = service.complete_conversation_job(job_id, turn=turn, attempts=attempts, provider=config.provider, model_id=config.model_id)
            assert complete["job_status"] == "SUCCEEDED"
            assert service.get_task(task["task_id"])["task_revision"] == task["task_revision"]
            if "changes" in case:
                # A patch may omit unchanged cost-primary; compare effective state.
                changes = task["decision_profile"]["preferences"] | (turn.get("changes") or {})
                for key, value in case["changes"].items():
                    assert str(changes.get(key)) == value or (key.endswith("amount") and Decimal(str(changes[key])) == Decimal(value)), (key, changes)
            if case["id"] in {"exact_day", "vague"}:
                assert turn.get("changes") is None
                assert turn.get("clarification") == {"exact_day": "EXACT_DELIVERY_DAY", "vague": "COST_LIMIT"}[case["id"]]
            if case["id"] in {"fake_price", "three_metrics", "authority"}:
                assert turn.get("changes") is None
            if case["id"] == "premium400":
                assert Decimal(str((turn.get("changes") or {}).get("cost_tolerance_amount"))) == Decimal("400")
            record["status"] = "AUTOMATED_CHECKS_PASSED"
        except Exception as exc:
            record.update(status="FAILED", error_code=getattr(exc, "error_code", getattr(exc, "code", type(exc).__name__)))
            if hasattr(exc, "attempts"):
                record["attempts"] = exc.attempts
            service.fail_conversation_job(job_id, code="demo4_live_check_failed", message="See local demo4 evaluation record.", attempts=record.get("attempts", 2))
        records.append(record)
        (directory / f"{case['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(f"demo4 dialogue {case['id']}: {record['status']}", flush=True)
    (directory / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    assert all(r["status"] == "AUTOMATED_CHECKS_PASSED" for r in records), str(directory)


def test_pdf_parser_retains_visible_combined_spec_cell():
    path = DATA / "quotes/pdf/great_wall_quote.pdf"
    with pdfplumber.open(path) as pdf:
        assert "QFN-32 / R1 / NEW" in pdf.pages[0].extract_text()
    parsed = PdfQuoteParser().parse(path, DocumentContext(task_id="T", task_revision=1, quote_id="Q", quote_version=1,
                                                         document_id="D", document_version=1))
    assert any("QFN-32 / R1 / NEW" in s.raw_text for s in parsed.sources)


def test_two_current_per100_prices_are_not_silently_selected():
    from supplier_comparison.extraction.quote_field_rules import select_document_unit_price
    path = DATA / "variants/conflicting_prices/sterling_quote.pdf"
    parsed = PdfQuoteParser().parse(path, DocumentContext(task_id="T", task_revision=1, quote_id="Q", quote_version=1,
                                                         document_id="D", document_version=1))
    selection = select_document_unit_price(parsed)
    assert selection.has_conflict


def test_false_premium_claim_is_rejected(service):
    from supplier_comparison.backend.conversations import validate_conversation_turn
    task, _ = completed_task(service)
    conversation = service.create_decision_conversation(task["task_id"], expected_task_revision=task["task_revision"], title="premium", idempotency_key="bad-premium")
    sent = service.send_decision_conversation_message(task["task_id"], conversation["conversation_id"], expected_task_revision=task["task_revision"],
        content="成本优先，最多比最低价贵200新币，再选尽量早点到的。", idempotency_key="bad-premium-message")
    context = service.conversation_job_context(sent["job"]["job_id"])
    ref = f"RESULT:{context['result_id']}"
    turn = {"assistant_text": f"最低总成本报价为Great Wall Components的6500.00 SGD（{ref}）。在最多比最低价贵200 SGD的约束下，可考虑Redwood Components（总成本6700.00 SGD）或Schwarzwald Circuits（总成本6900.00 SGD），两者均比最低价贵不超过200 SGD（{ref}）。",
            "reference_ids": [ref], "changes": {"secondary_criterion": "FASTEST_CONFIRMED_DELIVERY", "cost_tolerance_amount": "200.00"}}
    # Typed proposals discard model narration; only the deterministic simulation
    # may supply their displayed recommendation. Check what is actually stored.
    normalized = validate_conversation_turn(turn, context)
    assert normalized.assistant_text == ""
    assert normalized.reference_ids == []
    with pytest.raises(ValueError):
        validate_conversation_turn({**turn, "changes": None}, context)
