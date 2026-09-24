"""Quote review lifecycle regression; optional real-model PDF acceptance.

Run RUN_REVIEW_LIVE=1 with backend model environment to exercise native PDFs.
The operator uses canonical upload CSVs to check extracted values. These are
never passed to the PDF model, and extraction output is saved before review.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import csv
import os
import hashlib
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, create_engine, text
from sqlalchemy.orm import sessionmaker
import pytest
from uuid import uuid4

from supplier_comparison.backend.models import Base, DocumentExecution, WorkflowArtifact, QuoteDraft
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.workflow import DefaultQuoteProcessor, DraftReviewRunner
from tests.backend.test_workflow import WorkflowRunner
from supplier_comparison.extraction.adapters import OpenAICompatibleConfig
from supplier_comparison.extraction.contracts import ExtractionBatch
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.rules import ProcurementRequirement, RequirementChanges

from tests.backend.test_quote_drafts import service as sqlite_service, _all_field_actions, DICTIONARY_PATH, ROOT


@pytest.fixture
def service(sqlite_service, tmp_path):
    if os.getenv('RUN_REVIEW_POSTGRES') != '1':
        yield sqlite_service
        return
    from supplier_comparison.backend.settings import settings
    schema = 'review_test_' + uuid4().hex
    bootstrap = create_engine(settings.database_url)
    with bootstrap.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(settings.database_url, connect_args={'options': f'-csearch_path={schema},public'},
                           execution_options={'schema_translate_map': {None: schema}})
    try:
        Base.metadata.create_all(engine)
        yield BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path / 'pg-quotes',
            actor_id='isolated-review-test', quote_dictionary_path=DICTIONARY_PATH)
    finally:
        engine.dispose()
        with bootstrap.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        bootstrap.dispose()


def test_review_scenario_quote_and_requirement_lifecycle(service, tmp_path):
    live = os.getenv('RUN_REVIEW_LIVE') == '1'
    data = ROOT / 'data/generated/inputs/development/full_flow_demo3'
    req = ProcurementRequirement.model_validate_json((data / 'requirement/confirmed_requirement.json').read_text())
    dictionary = QuoteDictionary.load(DICTIONARY_PATH)
    def canonical(path, context):
        with path.open(newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            row = next(reader)
        row.update(scenario_id=context.scenario_id, quote_id=context.quote_id,
                   quote_version=str(context.quote_version), document_id=context.document_id,
                   supplier_id=context.supplier_id)
        normalized = tmp_path / f'{context.document_id}-operator.csv'
        with normalized.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
        return FixedCsvQuoteParser(dictionary).parse_row(normalized, context, 2)
    processor = DefaultQuoteProcessor(dictionary)
    if not live:
        def fixed_process(*, path, context, **_):
            batch = canonical(path, context)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            parsed = batch.parsed_input.model_copy(update={
                'document_sha256': digest,
                'sources': tuple(s.model_copy(update={'document_sha256': digest}) for s in batch.parsed_input.sources),
            })
            # Exercise a real-model-like correction before a later shipping
            # answer, so audit ancestry is tested rather than only clean CSVs.
            candidates = tuple(c.model_copy(update={'normalized_value':'6.10'})
                if context.supplier_id == 'SUP-022' and context.quote_version == 1 and c.field_name == 'unit_price' else c
                for c in batch.candidates)
            return batch.model_copy(update={'parsed_input': parsed, 'candidates': candidates})
        processor.process = fixed_process
    config = OpenAICompatibleConfig.from_env() if live else None
    opts = dict(provider=config.provider if live else 'fixed',
                model_id=config.model_id if live else 'canonical-csv',
                environment=config.environment.value if live else 'FIXED_TEST',
                prompt_version='quote-extraction/1.0.0')
    task = service.create_task(req, idempotency_key='lifecycle-create', scenario_id='REVIEW-LIFECYCLE')
    task_id = task['task_id']
    trace = []
    evidence_dir = Path(os.environ.get('REVIEW_EVIDENCE_DIR', str(tmp_path / 'evidence')))
    evidence_dir.mkdir(parents=True, exist_ok=True)

    def current():
        return service.get_task(task_id)

    def submit(draft, overrides=None):
        draft = service.get_quote_draft(task_id, draft['quote_draft_id'])
        reviewed = service.review_quote_draft(task_id, draft['quote_draft_id'],
            expected_draft_revision=draft['draft_revision'],
            schema_version=service.quote_field_schema()['schema_version'],
            actions=_all_field_actions(draft, overrides=overrides),
            idempotency_key='review-' + draft['quote_draft_id'])
        assert reviewed['submission_ready'], reviewed['review_errors']
        return service.submit_quote_draft(task_id, draft['quote_draft_id'],
            expected_task_revision=current()['task_revision'],
            expected_draft_revision=reviewed['draft_revision'],
            idempotency_key='submit-' + draft['quote_draft_id'])

    quotes = {}
    for name, supplier in [('sterling', 'SUP-024'), ('redwood', 'SUP-022'),
                           ('schwarzwald', 'SUP-023'), ('great_wall', 'SUP-029'),
                           ('sterling_semitech', 'SUP-030')]:
        extension = 'pdf' if live and name != 'schwarzwald' else 'csv'
        path = data / f'quotes/{name}_quote.{extension}'
        with path.open('rb') as stream:
            draft = service.upload_quote_draft_stream(task_id,
                expected_task_revision=current()['task_revision'], supplier_id=supplier,
                original_filename=path.name, media_type='application/pdf' if extension == 'pdf' else 'text/csv',
                stream=stream, idempotency_key='upload-' + name, is_synthetic=True, **opts)
        try:
            DraftReviewRunner(service, processor=processor, dictionary_path=DICTIONARY_PATH).run_job(draft['job']['job_id'])
        except Exception as exc:
            (evidence_dir / 'failure.json').write_text(json.dumps({
                'stage': 'parse', 'supplier': supplier, 'error_type': type(exc).__name__,
                'code': getattr(exc, 'code', None), 'message': str(exc),
            }, ensure_ascii=False, indent=2))
            raise
        draft = service.get_quote_draft(task_id, draft['quote_draft_id'])
        with service.session_factory() as session:
            record = session.get(QuoteDraft, draft['quote_draft_id'])
            artifact = session.get(WorkflowArtifact, record.batch_artifact_id)
            batch = ExtractionBatch.model_validate(artifact.payload)
        (evidence_dir / f'{name}-pre-human.json').write_text(batch.model_dump_json(indent=2))
        # Simulated operator checks the equivalent canonical quote, not a model oracle.
        operator = canonical(data / f'quotes/{name}_quote.csv', batch.parsed_input.context)
        expected = {c.field_name: c for c in operator.candidates}
        overrides = {}
        for field in draft['fields']:
            value = expected[field['field_name']]
            # Reel is outside the existing tray/piece normalization contract.
            # Retain it in the original; do not invent a supported packaging.
            if field['field_name'] == 'packaging_type' and 'reel' in str(value.normalized_value):
                if field['validation_status'] != 'MISSING':
                    overrides[field['field_name']] = dict(action='MARK_MISSING', reason='Reel has no supported normalized packaging; quantities are confirmed separately.')
                continue
            if field['field_name'] == 'packaging_type' and value.normalized_value == 'anti-static tray':
                value = value.model_copy(update={'normalized_value':'tray'})
            if value.normalized_value is not None and (field['normalized_value'] != value.normalized_value
                                                       or field['validation_status'] == 'CONFLICT'):
                overrides[field['field_name']] = dict(action='SET_VALUE', raw_value=str(value.raw_value),
                    normalized_value=value.normalized_value, unit=value.unit,
                    reason='Operator checked canonical synthetic quote against the source.')
            elif value.normalized_value is None and field['normalized_value'] is not None:
                overrides[field['field_name']] = dict(action='MARK_MISSING', reason='Source does not give this value.')
        quotes[supplier] = submit(draft, overrides)
        trace.append({'stage': 'quote-reviewed', 'supplier': supplier, 'operator_changes': sorted(overrides)})

    def no_extract(**kwargs):
        raise AssertionError('Confirmed quote was unexpectedly sent for extraction again')

    original_process = processor.process
    processor.process = no_extract
    runner = WorkflowRunner(service, processor=processor, checkpointer=InMemorySaver(),
        dictionary_path=DICTIONARY_PATH, evaluated_at=datetime(2026, 10, 12, 1, tzinfo=timezone.utc))
    start = service.start_run(task_id, expected_task_revision=current()['task_revision'],
                            idempotency_key='baseline', **opts)
    first = runner.run_job(start['job_id'])
    assert first['status'] == 'WAITING_INPUT', first
    assert first['issue']['issue_type'] == 'CONFIRM_MISSING'
    def answer(outcome, payload, key):
        queued = service.answer_issue(task_id, outcome['issue']['issue_id'],
            expected_task_revision=current()['task_revision'], answer=payload, idempotency_key=key)
        return runner.run_job(queued['job_id'])
    second = answer(first, {'answer_type': 'CONFIRM_MISSING'}, 'missing-confirmed')
    assert second['issue']['issue_type'] == 'SHIPPING_AMOUNT'
    answer(second, {'answer_type': 'SHIPPING_AMOUNT', 'amount': '320.00', 'currency': 'SGD'}, 'shipping-320')

    def check(stage, expected_fee='320.00'):
        task = current()
        assert task['current_result_id'], task
        context = service.workflow_context(task['current_graph_run_id'])
        assert len(context['documents']) == 5
        assert len({d['quote_id'] for d in context['documents']}) == 5
        with service.session_factory() as session:
            runs = session.scalars(select(DocumentExecution).where(DocumentExecution.graph_run_id==task['current_graph_run_id'])).all()
            assert len(runs) == 5
            for execution in runs:
                batch = ExtractionBatch.model_validate(session.get(WorkflowArtifact, execution.batch_artifact_id).payload)
                if batch.parsed_input.context.supplier_id == 'SUP-022':
                    values = {c.field_name: c.normalized_value for c in batch.candidates}
                    assert values['shipping_fee_status'] == 'KNOWN_AMOUNT'
                    assert Decimal(values['shipping_fee_amount']) == Decimal(expected_fee)
        result = service.get_result(task_id, task['current_result_id'])['result']
        for row in result['supplier_results']:
            term = row['payment_term']
            if row['quote_id'] != quotes['SUP-029']['quote_id']:
                assert term['parse_status'] == 'COMPARABLE', (row['quote_id'], term)
        trace.append({'stage':stage, 'revision':task['task_revision'], 'result':result})

    check('baseline-after-shipping')
    scenario = service.create_decision_scenario(task_id, expected_task_revision=current()['task_revision'],
        changes=RequirementChanges(primary_criterion='FASTEST_CONFIRMED_DELIVERY', secondary_criterion='LOWEST_CONFIRMED_TOTAL_COST'), idempotency_key='scenario')
    applied = service.apply_decision_scenario(task_id, scenario['decision_scenario_id'],
        expected_task_revision=current()['task_revision'], idempotency_key='apply', **opts)
    runner.run_job(applied['job_id'])
    check('scenario-applied')

    # Same-file edit must inherit the later 320 answer, rather than original UNKNOWN.
    draft = service.create_quote_revision_draft(task_id, quotes['SUP-022']['quote_id'],
        expected_task_revision=current()['task_revision'], idempotency_key='edit-redwood', **opts)
    assert draft['status'] == 'READY_TO_SUBMIT', draft
    assert next(f for f in draft['fields'] if f['field_name']=='shipping_fee_amount')['normalized_value'] == '320.00'
    submit(draft, {'shipping_fee_amount':dict(action='SET_VALUE', raw_value='SGD 325.00',
        normalized_value='325.00', unit='SGD', reason='Operator confirmed updated freight.')})
    rerun = service.start_run(task_id, expected_task_revision=current()['task_revision'], idempotency_key='after-edit', **opts)
    runner.run_job(rerun['job_id'])
    check('quote-v2', '325.00')

    updated = service.update_requirement(task_id, req.model_copy(update={'budget_amount':Decimal('11000.00')}),
        expected_task_revision=current()['task_revision'], idempotency_key='new-requirement', **opts)
    runner.run_job(updated['job_id'])
    check('requirement-updated', '325.00')
    # Central review submits the fee status and amount atomically, including
    # fields whose original PDF never contained an amount.
    fields = {f['field_name']: f for f in service.list_quote_fields(task_id, quotes['SUP-022']['quote_id'])['fields']}
    corrections = [dict(quote_id=quotes['SUP-022']['quote_id'], field_name=name,
        expected_field_version=fields[name]['field_version'], raw_value=value,
        normalized_value=value, unit='SGD' if name.endswith('amount') else None,
        reason='Supplier explicitly confirmed the revised freight.')
        for name, value in [('shipping_fee_status', 'KNOWN_AMOUNT'), ('shipping_fee_amount', '330.00')]]
    corrected = service.correct_fields(task_id=task_id, expected_task_revision=current()['task_revision'],
        corrections=corrections, idempotency_key='central-review')
    runner.run_job(corrected['job_id'])
    check('central-review', '330.00')
    # A newly uploaded source is independent: old human freight must not leak
    # into it. The operator explicitly verifies this version before submission.
    with (data / 'quotes/redwood_quote.csv').open(newline='') as handle:
        reader = csv.DictReader(handle)
        columns, row = reader.fieldnames, next(reader)
    row['quote_date'] = '2026-10-12'
    path = tmp_path / 'redwood-reissued.csv'
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(row)
    with path.open('rb') as stream:
        replacement = service.upload_quote_draft_stream(task_id,
            expected_task_revision=current()['task_revision'], supplier_id='SUP-022',
            replacement_quote_id=quotes['SUP-022']['quote_id'], original_filename=path.name,
            media_type='text/csv', stream=stream, idempotency_key='new-source', is_synthetic=True, **opts)
    processor.process = original_process
    DraftReviewRunner(service, processor=processor, dictionary_path=DICTIONARY_PATH).run_job(replacement['job']['job_id'])
    processor.process = no_extract
    replacement = service.get_quote_draft(task_id, replacement['quote_draft_id'])
    assert not replacement['human_review_complete']
    assert next(f for f in replacement['fields'] if f['field_name']=='shipping_fee_amount')['normalized_value'] is None
    submit(replacement, {
        'shipping_fee_status':dict(action='SET_VALUE', raw_value='Supplier confirmed separate freight', normalized_value='KNOWN_AMOUNT', reason='Operator confirmed this new quotation.'),
        'shipping_fee_amount':dict(action='SET_VALUE', raw_value='SGD 340.00', normalized_value='340.00', unit='SGD', reason='Operator confirmed this new quotation.'),
        'packaging_type':dict(action='SET_VALUE', raw_value='anti-static tray', normalized_value='tray', reason='Operator normalized the packaging.'),
    })
    run = service.start_run(task_id, expected_task_revision=current()['task_revision'], idempotency_key='after-new-source', **opts)
    runner.run_job(run['job_id'])
    check('uploaded-quote-v3', '340.00')
    # A human clarified the days but not the start event. Ask once, persist the
    # clarification, and never ask again merely because a requirement changed.
    draft = service.create_quote_revision_draft(task_id, quotes['SUP-030']['quote_id'],
        expected_task_revision=current()['task_revision'], idempotency_key='payment-edit', **opts)
    submit(draft, {'payment_terms':dict(action='SET_VALUE', raw_value='Net 90',
        normalized_value='Net 90', reason='Supplier confirmed days; invoice basis still needs clarification.')})
    run = service.start_run(task_id, expected_task_revision=current()['task_revision'], idempotency_key='payment-display-only', **opts)
    runner.run_job(run['job_id'])
    scenario = service.create_decision_scenario(task_id, expected_task_revision=current()['task_revision'],
        changes=RequirementChanges(primary_criterion='LONGEST_CONFIRMED_PAYMENT_TERM', secondary_criterion=None),
        idempotency_key='payment-scenario')
    applied = service.apply_decision_scenario(task_id, scenario['decision_scenario_id'],
        expected_task_revision=current()['task_revision'], idempotency_key='payment-apply', **opts)
    waiting = runner.run_job(applied['job_id'])
    assert waiting['issue']['issue_type'] == 'PAYMENT_INFORMATION'
    answer(waiting, {'answer_type':'PAYMENT_INFORMATION', 'payment_start_event':'INVOICE_DATE',
        'note':'Supplier confirms Net 90 starts on invoice date.', 'source_type':'SUPPLIER_CONFIRMATION'}, 'payment-answer')
    check('payment-confirmed', '340.00')
    updated = service.update_requirement(task_id, req.model_copy(update={'budget_amount':Decimal('20000.00'), 'required_quantity':1600}),
        expected_task_revision=current()['task_revision'], idempotency_key='after-payment-requirement', **opts)
    outcome = runner.run_job(updated['job_id'])
    assert outcome['status'] != 'WAITING_INPUT', outcome
    check('payment-survives-requirement', '340.00')
    (evidence_dir / 'lifecycle.json').write_text(json.dumps({'mode':'REAL_MODEL' if live else 'CANONICAL_CSV',
        'trace':trace}, ensure_ascii=False, indent=2))
