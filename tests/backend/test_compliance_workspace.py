from pathlib import Path
from datetime import datetime, timezone
import hashlib
import os
import subprocess
import sys
from io import BytesIO

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base, Task, WorkflowArtifact
from supplier_comparison.backend.service import BackendService, ConflictError
from supplier_comparison.backend.workflow import WorkflowRunner
from .test_workflow import CanonicalCsvProcessor, DICTIONARY_PATH, _requirement
from supplier_comparison.rag.contracts import PolicyCitation, RetrievalResult
from supplier_comparison.rag.models import PolicySet, PolicyIndex, PolicyDocument, PolicyClause


def seed_policy(service):
    params = {'version': 'compliance-rule/1.0', 'reviewed_by': 'policy-reviewer',
        'reviewed_at': '2026-09-01T00:00:00Z', 'date_basis': 'EVALUATED_AT', 'missing_outcome': 'REVIEW_REQUIRED',
        'expired_outcome': 'REVIEW_REQUIRED', 'mismatch_outcome': 'REVIEW_REQUIRED',
        'execution_stage': 'BEFORE_RECOMMENDATION', 'allow_unspecified_validity': False}
    with service.session_factory.begin() as session:
        session.add(PolicySet(policy_set_record_id='set', policy_set_id='demo', policy_set_version='v1',
            schema_version='1', manifest_sha256='a'*64, manifest_path='synthetic', status='PUBLISHED'))
        session.flush()
        session.add(PolicyDocument(policy_document_record_id='doc', policy_set_record_id='set', policy_id='policy',
            document_id='document', document_version='1', title='Synthetic reviewed requirements', source_path='synthetic',
            content_sha256='a'*64, effective_from=datetime(2026,1,1,tzinfo=timezone.utc), categories=['Electronics'], regions=['SG']))
        session.add(PolicyIndex(policy_index_version='idx', policy_set_record_id='set', collection_sha256='a'*64,
            provider='fixed', embedding_model='fixed', embedding_dimension=1024, preprocessing_version='1', status='PUBLISHED'))
        session.flush()
        for code in ('APPROVED_SUPPLIER','ROHS_COMPLIANCE','AMOUNT_APPROVAL'):
            rule = {**params, 'control_code': code, 'matching_fields': ['supplier_id']}
            if code == 'ROHS_COMPLIANCE':
                rule['matching_fields'] += ['manufacturer','manufacturer_part_number']
            if code == 'AMOUNT_APPROVAL':
                rule.update(matching_fields=[], currency='SGD', monetary_basis='TOTAL_COST', threshold='10000.00',
                            operator='GTE', action='Obtain finance approval', execution_stage='AFTER_SELECTION')
            text = 'Synthetic reviewed requirement: ' + code
            session.add(PolicyClause(policy_clause_record_id=code, policy_set_record_id='set', policy_document_record_id='doc',
                clause_id=code, section=code, text=text, normalized_text=text.lower(),
                content_sha256=hashlib.sha256(text.encode()).hexdigest(), control_code=code, rule_parameters=rule))


class CatalogueRetriever:
    def __init__(self, service):
        self.service, self.calls = service, 0
        self.fail = False

    def retrieve(self, request):
        self.calls += 1
        with self.service.session_factory() as session:
            clause = session.get(PolicyClause, request.required_control_codes[0])
            citation = PolicyCitation(citation_id=f'cit-{self.calls}', retrieval_id=f'ret-{self.calls}',
                policy_set_version='v1', policy_id='policy', document_id='document', document_version='1',
                clause_id=clause.clause_id, section=clause.section, text=clause.text, content_sha256=clause.content_sha256,
                control_code=clause.control_code, bm25_rank=1, bm25_score=1, vector_rank=1, vector_score=1,
                fusion_rank=1, fusion_score=1, rerank_rank=1, rerank_score=1)
        return RetrievalResult(retrieval_id=f'ret-{self.calls}', status='ERROR' if self.fail else 'OK',
            policy_set_version='v1', policy_index_version='idx', embedding_model='fixed', rerank_model='fixed',
            filters={}, covered_control_codes=[] if self.fail else request.required_control_codes,
            missing_control_codes=request.required_control_codes if self.fail else [], citations=[] if self.fail else [citation],
            candidates=[], latency_ms={'total':0}, attempts={'embedding':1,'rerank':1}, error_code='fixed_failure' if self.fail else None)


@pytest.fixture
def policy_workspace(tmp_path):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    service = BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path / 'files', actor_id='owner')
    seed_policy(service)
    task = service.create_task(_requirement(), idempotency_key='create', policy_set_version='v1',
        policy_index_version='idx', policy_category='Electronics', policy_region='SG')
    uploaded = service.upload_quote(task['task_id'], expected_task_revision=1, supplier_id='SUP-024',
        original_filename='quote.csv', media_type='text/csv', content=b'placeholder-c', idempotency_key='upload', is_synthetic=True)
    job = service.start_run(task['task_id'], expected_task_revision=uploaded['task_revision'], idempotency_key='start')
    retriever = CatalogueRetriever(service)
    runner = WorkflowRunner(service, processor=CanonicalCsvProcessor(tmp_path), policy_retriever=retriever,
        checkpointer=InMemorySaver(), dictionary_path=DICTIONARY_PATH, evaluated_at=datetime(2026,9,17,tzinfo=timezone.utc))
    runner.run_job(job['job_id'])
    return service, task['task_id'], runner, uploaded['quote_id']


def evidence(quote_id, control_code='APPROVED_SUPPLIER', **overrides):
    return {'quote_id': quote_id, 'control_code': control_code, 'supplier_id':'SUP-024',
        'manufacturer':'QQ Demo Components','manufacturer_part_number':'QW-MCU9-DEMO',
        'material_number':'DEMO-001', 'coverage_confirmed':True, 'outcome':'PASS',
        'effective_from':'2026-01-01','expires_on':'2027-01-01', 'source_refs':['Synthetic record DEMO-001'], **overrides}


def save_and_run(service, task_id, runner, facts, *, previous=None, key='save'):
    response = service.save_compliance_evidence(task_id, expected_task_revision=service.get_task(task_id)['task_revision'],
        facts=facts, previous_evidence_id=previous, idempotency_key=key)
    runner.run_job(response['job_id'])
    return response


def confirm_and_run(service, task_id, runner, key='confirm'):
    view = service.compliance_workspace(task_id)
    response = service.confirm_compliance(task_id, expected_task_revision=view['task_revision'],
        expected_assessment_id=view['assessment']['assessment_id'],
        acknowledged_missing_item_ids=view['assessment']['missing_item_ids'],
        acknowledge_no_policy=not view['assessment']['policy_enabled'], idempotency_key=key)
    runner.run_job(response['job_id'])
    return service.get_result(task_id, service.get_task(task_id)['current_result_id'])


def test_material_correction_qualification_and_frozen_history(policy_workspace):
    service, task_id, runner, quote_id = policy_workspace
    assert service.list_results(task_id) == []
    draft = confirm_and_run(service, task_id, runner)
    assert not draft['result']['final_recommendation_allowed']
    old_assessment = draft['policy_compliance']
    wrong = save_and_run(service, task_id, runner, evidence(quote_id, 'ROHS_COMPLIANCE', manufacturer_part_number='WRONG'), key='wrong')
    view = service.compliance_workspace(task_id)
    assert any('EVIDENCE_SCOPE_MISMATCH' in c['reason_codes'] for c in view['assessment']['assessments'][0]['checks'])
    assert service.get_task(task_id)['current_result_id'] is None
    save_and_run(service, task_id, runner, evidence(quote_id, 'ROHS_COMPLIANCE'), previous=wrong['evidence_id'], key='correct')
    save_and_run(service, task_id, runner, evidence(quote_id), key='approved')
    result = confirm_and_run(service, task_id, runner, key='final')
    assert result['result']['recommended_quote_ids'] == [quote_id]
    assert result['policy_compliance']['assessments'][0]['status'] == 'COMPLIANT'
    assert result['decision_impact']['comparison']['recommended_quote_ids'] == [quote_id]
    assert service.get_result(task_id, draft['result_id'])['policy_compliance'] == old_assessment
    assert len(runner.processor.calls) == 1


def test_retrieval_error_cannot_be_confirmed(policy_workspace):
    service, task_id, runner, quote_id = policy_workspace
    runner.policy_retriever.fail = True
    save_and_run(service, task_id, runner, evidence(quote_id))
    view = service.compliance_workspace(task_id)
    assert view['stage']['status'] == 'BLOCKED'
    with pytest.raises(ConflictError, match='检索|条款'):
        confirm_and_run(service, task_id, runner)


def test_evidence_replay_conflict_authorization_and_file_access(policy_workspace):
    from supplier_comparison.backend.service import NotFoundError, BackendError
    service, task_id, runner, quote_id = policy_workspace
    kwargs = dict(expected_task_revision=service.get_task(task_id)['task_revision'], facts=evidence(quote_id), idempotency_key='file')
    saved = service.save_compliance_evidence(task_id, **kwargs, file=BytesIO(b'confirmed synthetic evidence'), filename='../../source.txt')
    replay = service.save_compliance_evidence(task_id, **kwargs, file=BytesIO(b'confirmed synthetic evidence'), filename='../../source.txt')
    assert saved == replay
    with pytest.raises(ConflictError):
        service.save_compliance_evidence(task_id, **{**kwargs, 'facts':evidence(quote_id,outcome='FAIL')}, file=BytesIO(b'changed'), filename='source.txt')
    view = service.compliance_workspace(task_id)
    file = view['evidence'][0]['files'][0]
    assert 'storage_path' not in str(view)
    assert file['original_filename'] == 'source.txt'
    assert service.compliance_evidence_content(task_id, saved['evidence_id'], file['file_id'])['path'].read_bytes() == b'confirmed synthetic evidence'
    other = BackendService(service.session_factory, service.storage_root, actor_id='other')
    with pytest.raises(NotFoundError):
        other.compliance_evidence_content(task_id, saved['evidence_id'], file['file_id'])
    with pytest.raises(BackendError):
        service.save_compliance_evidence(task_id, **{**kwargs, 'idempotency_key':'oversize'}, file=BytesIO(b'x'*(10*1024*1024+1)), filename='large.txt')


def test_expired_on_new_worker_requires_reconfirmation(policy_workspace):
    service, task_id, runner, quote_id = policy_workspace
    for code in ('APPROVED_SUPPLIER','ROHS_COMPLIANCE'):
        save_and_run(service, task_id, runner, evidence(quote_id,code,expires_on='2026-09-17'), key=code)
    view = service.compliance_workspace(task_id)
    response = service.confirm_compliance(task_id, expected_task_revision=view['task_revision'],
        expected_assessment_id=view['assessment']['assessment_id'], acknowledged_missing_item_ids=[], idempotency_key='confirm')
    runner.evaluated_at = datetime(2026,9,18,tzinfo=timezone.utc)
    runner.run_job(response['job_id'])
    assert service.get_task(task_id)['current_result_id'] is None
    assert service.compliance_workspace(task_id)['stage']['confirmed'] is False
    refreshed = confirm_and_run(service, task_id, runner, key='ack-expired')
    assert not refreshed['result']['final_recommendation_allowed']


def test_api_material_submission_and_cross_task_rejection(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from supplier_comparison.backend.api import create_app
    engine = create_engine('sqlite+pysqlite:///:memory:', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    service = BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path/'files', actor_id='owner')
    http = TestClient(create_app(service, readiness_check=lambda:True))
    task = service.create_task(_requirement(), idempotency_key='create')
    quote = service.upload_quote(task['task_id'], expected_task_revision=1, supplier_id='SUP-024',
        original_filename='quote.csv', media_type='text/csv', content=b'placeholder', idempotency_key='upload')
    import json
    url = f"/api/v1/tasks/{task['task_id']}/compliance/evidence"
    body = {'expected_task_revision':2, 'facts':json.dumps(evidence(quote['quote_id']))}
    response = http.post(url, data=body, headers={'Idempotency-Key':'save'})
    assert response.status_code == 202, response.text
    assert http.post(url, data=body, headers={'Idempotency-Key':'save'}).json() == response.json()
    assert http.post(url, data=body, headers={'Idempotency-Key':'stale'}).status_code == 409
    other = service.create_task(_requirement(), idempotency_key='other')
    response = http.post(f"/api/v1/tasks/{other['task_id']}/compliance/evidence", data={**body,'expected_task_revision':1},
                         headers={'Idempotency-Key':'foreign'})
    assert response.status_code == 404
    bad = {**body,'facts':json.dumps(evidence(quote['quote_id'],coverage_confirmed=False))}
    assert http.post(url, data=bad, headers={'Idempotency-Key':'bad'}).status_code == 422


def test_known_material_does_not_bypass_unsupported_rule_stage(policy_workspace):
    service, task_id, runner, quote_id = policy_workspace
    with service.session_factory.begin() as session:
        clause = session.get(PolicyClause,'APPROVED_SUPPLIER')
        clause.rule_parameters = {'execution_stage':'BOGUS'}
    save_and_run(service, task_id, runner, evidence(quote_id))
    view = service.compliance_workspace(task_id)
    assert view['assessment']['policy_eligibility'][quote_id]['status'] == 'UNVERIFIED'


def test_frozen_summary_exports_and_simulation_use_same_assessment(policy_workspace):
    from supplier_comparison.rules import RequirementChanges
    from supplier_comparison.backend.summary_exports import build_summary_export
    from zipfile import ZipFile
    service, task_id, runner, quote_id = policy_workspace
    for code in ('APPROVED_SUPPLIER','ROHS_COMPLIANCE'):
        save_and_run(service, task_id, runner, evidence(quote_id,code),key=code)
    result = confirm_and_run(service,task_id,runner)
    scenario = service.create_decision_scenario(task_id, expected_task_revision=result['task_revision'],
        changes=RequirementChanges(budget_amount='9000.00'), idempotency_key='preview')
    assert scenario['status'] == 'READY'
    with service.session_factory() as session:
        facts = service._summary_facts(session, session.get(Task,task_id), session.get(WorkflowArtifact,result['result_id']))
    assert facts['policy_compliance'] == result['policy_compliance']
    for format in ('md','docx'):
        exported = build_summary_export(facts=facts,narrative={},summary_id='summary',status='SUCCEEDED',
            updated_at=datetime.now(timezone.utc),export_format=format)['content']
        text = exported.decode() if format == 'md' else ZipFile(BytesIO(exported)).read('word/document.xml').decode()
        assert result['policy_compliance']['assessment_id'] in text
        assert '不鉴定材料真伪' in text


def test_simulation_preserves_frozen_publication_blockers(policy_workspace):
    from supplier_comparison.rules import RequirementChanges
    service, task_id, runner, quote_id = policy_workspace
    with service.session_factory.begin() as session:
        clause = session.get(PolicyClause, 'AMOUNT_APPROVAL')
        clause.rule_parameters = {**clause.rule_parameters, 'execution_stage': 'BEFORE_PUBLICATION', 'threshold': '7000.00'}
    for code in ('APPROVED_SUPPLIER', 'ROHS_COMPLIANCE'):
        save_and_run(service, task_id, runner, evidence(quote_id, code), key=code)
    result = confirm_and_run(service, task_id, runner)
    assert result['result']['final_recommendation_allowed'] is False
    changes = RequirementChanges(budget_amount='9000.00')
    direct = service.requirement_simulation(task_id, expected_task_revision=result['task_revision'],
        changes=changes, user_authorized=True)
    scenario = service.create_decision_scenario(task_id, expected_task_revision=result['task_revision'],
        changes=changes, idempotency_key='blocked-preview')
    for comparison in (direct['result']['comparison'], scenario['simulated']['comparison']):
        assert comparison['final_recommendation_allowed'] is False
        assert comparison['recommended_quote_ids'] == []
        assert comparison['compliance_assessment'] == result['policy_compliance']
    assert service.get_task(task_id)['current_result_id'] == result['result_id']


@pytest.mark.skipif(os.getenv('RUN_POSTGRES_TESTS') != '1', reason='Set RUN_POSTGRES_TESTS=1 for isolated PostgreSQL migration/restart acceptance')
@pytest.mark.parametrize('live', [False, pytest.param(True, marks=pytest.mark.skipif(os.getenv('RUN_COMPLIANCE_LIVE') != '1', reason='Set RUN_COMPLIANCE_LIVE=1 for paid real retrieval acceptance'))])
def test_postgres_migration_and_cross_process_compliance(tmp_path, monkeypatch, live):
    from uuid import uuid4
    from sqlalchemy import text, make_url
    from alembic.config import Config
    from alembic import command
    from supplier_comparison.backend.settings import settings
    database_name = 'compliance_test_' + uuid4().hex
    source_url = make_url(os.getenv('TEST_DATABASE_URL', settings.database_url))
    admin = create_engine(source_url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    database_url = source_url.set(database=database_name).render_as_string(hide_password=False)
    engine = None
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        monkeypatch.setenv('DATABASE_URL', database_url)
        cfg = Config('alembic.ini')
        command.upgrade(cfg, 'head')
        command.downgrade(cfg, '0f4e8b6a9c21')
        command.upgrade(cfg, 'head')
        engine = create_engine(database_url)
        service = BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path/'files', actor_id='owner')
        policy_version, index_version = 'v1', 'idx'
        if live:
            from supplier_comparison.rag.cli import _build_clients
            from supplier_comparison.rag.importer import PolicyImporter
            embedding, _ = _build_clients()
            outcome = PolicyImporter(service.session_factory, embedding, allowed_root=Path('data/policies'), provider=embedding.config.provider).import_manifest(
                Path('data/policies/compliance-closure-demo/v1/manifest.json'), publish=True)
            policy_version, index_version = outcome.policy_set_version, outcome.index_version
        else:
            seed_policy(service)
        task = service.create_task(_requirement(), idempotency_key='pg-task', policy_set_version=policy_version,
            policy_index_version=index_version, policy_category='Electronics', policy_region='SG')
        quote = service.upload_quote(task['task_id'], expected_task_revision=1, supplier_id='SUP-024',
            original_filename='quote.csv',media_type='text/csv',content=b'placeholder-c',idempotency_key='upload',is_synthetic=True)
        job = service.start_run(task['task_id'],expected_task_revision=quote['task_revision'],idempotency_key='start')
        code = '''
import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from langgraph.checkpoint.postgres import PostgresSaver
from supplier_comparison.backend.checkpoints import checkpoint_connection_string
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.workflow import WorkflowRunner
from tests.backend.test_compliance_workspace import CatalogueRetriever
from tests.backend.test_workflow import CanonicalCsvProcessor, DICTIONARY_PATH
engine=create_engine(os.environ['DATABASE_URL'])
service=BackendService(sessionmaker(engine,expire_on_commit=False),Path(os.environ['ACCEPTANCE_ROOT'])/'files',actor_id='owner')
class NoReextract:
    def process(self, **kwargs):
        raise AssertionError('completed document must not be re-extracted')
processor=NoReextract() if os.environ.get('NO_REEXTRACT') else CanonicalCsvProcessor(Path(os.environ['ACCEPTANCE_ROOT']))
with PostgresSaver.from_conn_string(checkpoint_connection_string(os.environ['DATABASE_URL'])) as saver:
    saver.setup()
    retriever = CatalogueRetriever(service)
    if os.environ.get('ACCEPTANCE_LIVE') == '1':
        from supplier_comparison.rag.cli import _build_clients
        from supplier_comparison.rag.retriever import HybridPolicyRetriever
        from supplier_comparison.rag.repository import SQLPolicyRepository
        embedding, rerank = _build_clients()
        retriever = HybridPolicyRetriever(SQLPolicyRepository(service.session_factory), embedding, rerank)
    runner=WorkflowRunner(service,processor=processor,policy_retriever=retriever,checkpointer=saver,
        dictionary_path=DICTIONARY_PATH,evaluated_at=datetime(2026,9,17,tzinfo=timezone.utc))
    result=runner.run_job(os.environ['ACCEPTANCE_JOB'])
    print(result['status'])
engine.dispose()
'''
        def run(job_id, no_extract=False):
            env = {**os.environ, 'ACCEPTANCE_ROOT':str(tmp_path), 'ACCEPTANCE_JOB':job_id, 'ACCEPTANCE_LIVE':'1' if live else '0'}
            if no_extract:
                env['NO_REEXTRACT']='1'
            result = subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=90)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()
        assert run(job['job_id']) == 'WAITING_INPUT'
        for control in ('APPROVED_SUPPLIER','ROHS_COMPLIANCE'):
            saved=service.save_compliance_evidence(task['task_id'],expected_task_revision=service.get_task(task['task_id'])['task_revision'],
                facts=evidence(quote['quote_id'],control),idempotency_key=control)
            assert run(saved['job_id'],True) == 'WAITING_INPUT'
        view=service.compliance_workspace(task['task_id'])
        confirmed=service.confirm_compliance(task['task_id'],expected_task_revision=view['task_revision'],
            expected_assessment_id=view['assessment']['assessment_id'],acknowledged_missing_item_ids=[],idempotency_key='confirm')
        assert run(confirmed['job_id'],True) == 'SUCCEEDED'
        result=service.get_result(task['task_id'],service.get_task(task['task_id'])['current_result_id'])
        assert result['result']['recommended_quote_ids'] == [quote['quote_id']]
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def workspace(tmp_path):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    service = BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path / 'files', actor_id='owner')
    task = service.create_task(_requirement(), idempotency_key='create')
    uploaded = service.upload_quote(task['task_id'], expected_task_revision=1, supplier_id='SUP-024',
        original_filename='quote.csv', media_type='text/csv', content=b'placeholder-c',
        idempotency_key='upload', is_synthetic=True)
    job = service.start_run(task['task_id'], expected_task_revision=uploaded['task_revision'], idempotency_key='start')
    runner = WorkflowRunner(service, processor=CanonicalCsvProcessor(tmp_path), checkpointer=InMemorySaver(),
        dictionary_path=DICTIONARY_PATH, evaluated_at=datetime(2026, 9, 17, tzinfo=timezone.utc))
    runner.run_job(job['job_id'])
    return service, task['task_id'], runner


def test_new_workflow_waits_for_explicit_no_policy_confirmation(workspace):
    service, task_id, runner = workspace
    task = service.get_task(task_id)
    assert task['current_result_id'] is None
    view = service.compliance_workspace(task_id)
    assert view['stage']['status'] == 'AWAITING_CONFIRMATION'
    assert view['assessment']['policy_enabled'] is False
    request = dict(expected_task_revision=task['task_revision'],
        expected_assessment_id=view['assessment']['assessment_id'], acknowledged_missing_item_ids=[],
        acknowledge_no_policy=True, idempotency_key='confirm')
    confirmed = service.confirm_compliance(task_id, **request)
    assert service.confirm_compliance(task_id, **request) == confirmed
    runner.run_job(confirmed['job_id'])
    result = service.get_result(task_id, service.get_task(task_id)['current_result_id'])
    assert result['policy_compliance']['policy_enabled'] is False
    assert result['policy_compliance']['assessment_id']
    assert result['input_snapshot']['workflow_contract_version'] == 'compliance/2.0'
    with pytest.raises(ConflictError):
        service.confirm_compliance(task_id, **{**request, 'idempotency_key': 'stale'})


def test_direct_publish_before_compliance_confirmation_is_rejected(workspace):
    service, task_id, _ = workspace
    task = service.get_task(task_id)
    with service.session_factory() as session:
        artifact = session.scalar(select(WorkflowArtifact).where(WorkflowArtifact.task_id == task_id,
            WorkflowArtifact.artifact_type == 'BASE_COMPARISON'))
    with pytest.raises(ConflictError, match='compliance'):
        service.publish_result(task_id=task_id, graph_run_id=task['current_graph_run_id'],
            task_revision=task['task_revision'], snapshot_id=artifact.parent_artifact_id, result_id=artifact.artifact_id)


@pytest.mark.parametrize('approval_stage, threshold, expected', [
    ('AFTER_SELECTION', '7000.00', True),
    ('BEFORE_PUBLICATION', '7000.00', False),
    ('BEFORE_RECOMMENDATION', '7000.00', False),
    ('BEFORE_PUBLICATION', '7200.00', True),
])
def test_verified_cohort_and_candidate_amount_publication(policy_workspace, approval_stage, threshold, expected):
    service, task_id, runner, quote_id = policy_workspace
    with service.session_factory.begin() as session:
        clause = session.get(PolicyClause, 'AMOUNT_APPROVAL')
        clause.rule_parameters = {**clause.rule_parameters, 'execution_stage': approval_stage, 'threshold': threshold}
    uploaded = service.upload_quote(task_id, expected_task_revision=service.get_task(task_id)['task_revision'],
        supplier_id='SUP-023', original_filename='cheap.csv', media_type='text/csv', content=b'cheap',
        idempotency_key='cheap', is_synthetic=True)
    runner.processor.row_overrides['SUP-023'] = {'shipping_fee_status':'KNOWN_AMOUNT', 'shipping_fee_amount':'200.00', 'fees_complete':'True'}
    for control in ('APPROVED_SUPPLIER', 'ROHS_COMPLIANCE'):
        save_and_run(service, task_id, runner, evidence(quote_id, control), key=control)
    result = confirm_and_run(service, task_id, runner)
    rows = {r['quote_id']:r for r in result['result']['supplier_results']}
    assert rows[uploaded['quote_id']]['total_cost'] == '7000.00'
    assert rows[quote_id]['total_cost'] == '7100.00'
    assert result['policy_compliance']['policy_eligibility'][quote_id]['status'] == 'VERIFIED'
    assert result['policy_compliance']['policy_eligibility'][uploaded['quote_id']]['status'] == 'UNVERIFIED'
    assert result['result']['final_recommendation_allowed'] is expected
    assert result['result']['recommended_quote_ids'] == ([quote_id] if expected else [])
    assert len(rows) == 2
    assert all(item['best_confirmed_cost'] == '7100.00' for item in result['decision_impact']['quote_impacts'])


def test_legacy_reanalysis_enters_new_stage_without_rewriting_history(workspace):
    from supplier_comparison.backend.models import Task
    service, task_id, runner = workspace
    original = confirm_and_run(service, task_id, runner)
    with service.session_factory.begin() as session:
        session.get(Task, task_id).workflow_contract_version = 'legacy/1.0'
    job = service.start_run(task_id, expected_task_revision=service.get_task(task_id)['task_revision'], idempotency_key='legacy-reanalyse')
    runner.run_job(job['job_id'])
    assert service.get_task(task_id)['current_result_id'] is None
    assert service.compliance_workspace(task_id)['stage']['status'] == 'AWAITING_CONFIRMATION'
    assert service.get_result(task_id, original['result_id'])['result'] == original['result']
