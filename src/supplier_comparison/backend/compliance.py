"""Task-scoped, versioned compliance workspace. No approval or certificate authenticity claims."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy import select

from .models import (ComplianceEvidenceFile, ComplianceEvidenceRecord, Document, DocumentExecution,
                     GraphRun, Issue, Job, Quote, Task, TaskRevision, WorkflowArtifact)

WORKFLOW_VERSION = 'compliance/2.0'
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    quote_id: str = Field(min_length=1, max_length=64)
    control_code: Literal['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL']
    supplier_id: str = Field(min_length=1, max_length=128)
    manufacturer: str | None = Field(default=None, max_length=256)
    manufacturer_part_number: str | None = Field(default=None, max_length=256)
    approval_amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    material_number: str = Field(min_length=1, max_length=256)
    coverage_confirmed: StrictBool
    outcome: Literal['PASS', 'FAIL']
    effective_from: date | None = None
    expires_on: date | None = None
    permanent: StrictBool = False
    source_refs: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default='', max_length=2000)

    @field_validator('approval_amount', mode='before')
    @classmethod
    def exact_approval_amount(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError('Approval amount requires exact decimal money.')
        return value

    @model_validator(mode='after')
    def facts_consistent(self):
        if not self.coverage_confirmed:
            raise ValueError('Please explicitly confirm the material coverage after checking the source.')
        if self.permanent and self.expires_on:
            raise ValueError('Permanent validity cannot have an expiry date.')
        if self.effective_from and self.expires_on and self.effective_from >= self.expires_on:
            raise ValueError('Expiry must be after the effective date.')
        if any(not ref.strip() or len(ref) > 1000 for ref in self.source_refs):
            raise ValueError('Source references must be non-empty and at most 1000 characters.')
        if self.control_code == 'ROHS_COMPLIANCE' and (not self.manufacturer or not self.manufacturer_part_number):
            raise ValueError('RoHS evidence requires manufacturer and part number as stated in the material.')
        if self.control_code == 'AMOUNT_APPROVAL':
            if self.approval_amount is None or self.currency is None:
                raise ValueError('Amount approval evidence requires the approved amount and currency.')
        elif self.approval_amount is not None or self.currency is not None:
            raise ValueError('Supplier qualification evidence cannot carry amount approval facts.')
        return self


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def bind_assessment_comparison(result, assessment):
    from ..rules.contracts import ComparisonDisposition, RuleIssue
    if any(assessment.get('publication_blockers', {}).get(qid) for qid in result.recommended_quote_ids) and result.final_recommendation_allowed:
        result = result.model_copy(update={'final_recommendation_allowed': False, 'recommended_quote_ids': (),
            'disposition': ComparisonDisposition.PENDING_INPUT,
            'comparison_reasons': (RuleIssue(code='APPROVAL_REQUIRED_BEFORE_PUBLICATION',
                message='Policy requires verification or approval before publication; only a draft and action items are currently available.'),)})
    return result.model_copy(update={'compliance_assessment': assessment})


def latest_artifact(session, task, kind):
    return session.scalar(select(WorkflowArtifact).where(
        WorkflowArtifact.task_id == task.task_id, WorkflowArtifact.task_revision == task.current_revision,
        WorkflowArtifact.artifact_type == kind).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))


def stage_payload(session, task):
    assessment = latest_artifact(session, task, 'COMPLIANCE_ASSESSMENT')
    confirmation = latest_artifact(session, task, 'COMPLIANCE_CONFIRMATION')
    payload = assessment.payload if assessment else {}
    confirmed = bool(confirmation and assessment and confirmation.payload.get('assessment_id') == assessment.artifact_id)
    blocked = payload.get('policy_errors', [])
    if confirmed:
        status = 'PROCESSED' if payload.get('policy_enabled') else 'DISABLED'
    elif blocked:
        status = 'BLOCKED'
    elif assessment:
        status = 'AWAITING_CONFIRMATION'
    elif task.status in ('QUEUED', 'PROCESSING', 'RUNNING'):
        status = 'PROCESSING'
    else:
        status = 'NOT_STARTED'
    return {'status': status, 'task_revision': task.current_revision,
            'assessment_id': assessment.artifact_id if assessment else None,
            'pending_count': len(payload.get('missing_item_ids', [])) + len(blocked),
            'confirmed': confirmed, 'can_confirm': bool(assessment and not blocked and not confirmed),
            'can_compare': confirmed, 'workflow_contract_version': task.workflow_contract_version}


class ComplianceMixin:
    def require_compliance_stage(self, task_id):
        from .service import ConflictError
        with self.session_factory() as session:
            task = self._compliance_task(session, task_id)
            if task.workflow_contract_version == WORKFLOW_VERSION and not stage_payload(session, task)['confirmed']:
                raise ConflictError('compliance_confirmation_required', 'Process and confirm the compliance review before entering decision comparison.')

    def _compliance_task(self, session, task_id, *, lock=False):
        from .service import NotFoundError
        query = select(Task).where(Task.task_id == task_id, Task.owner_id == self.actor_id)
        task = session.scalar(query.with_for_update() if lock else query)
        if task is None:
            raise NotFoundError('task_not_found', 'Task was not found.')
        return task

    @staticmethod
    def _compliance_mutable(task):
        from .service import ConflictError
        if task.status == 'ABANDONED':
            raise ConflictError('task_abandoned', 'Abandoned tasks are read-only.')

    @staticmethod
    def _evidence_payload(session, record):
        files = session.scalars(select(ComplianceEvidenceFile).where(ComplianceEvidenceFile.evidence_id == record.evidence_id)).all()
        return {'evidence_id': record.evidence_id, 'task_id': record.task_id, 'task_revision': record.task_revision,
                'quote_id': record.quote_id, 'control_code': record.control_code, 'version': record.version,
                'previous_evidence_id': record.previous_evidence_id, 'facts': record.facts,
                'content_sha256': record.content_sha256, 'confirmed_by': record.confirmed_by,
                'confirmed_at': _utc(record.confirmed_at).isoformat(),
                'files': [{'file_id': f.file_id, 'original_filename': f.original_filename,
                           'media_type': f.media_type, 'size_bytes': f.size_bytes, 'sha256': f.sha256} for f in files]}

    def compliance_workspace(self, task_id):
        with self.session_factory() as session:
            task = self._compliance_task(session, task_id)
            assessment = latest_artifact(session, task, 'COMPLIANCE_ASSESSMENT')
            plan = latest_artifact(session, task, 'COMPLIANCE_PLAN')
            records = session.scalars(select(ComplianceEvidenceRecord).where(ComplianceEvidenceRecord.task_id == task_id)
                                      .order_by(ComplianceEvidenceRecord.confirmed_at, ComplianceEvidenceRecord.evidence_id)).all()
            superseded = {r.previous_evidence_id for r in records if r.previous_evidence_id}
            return {'task_id': task_id, 'task_revision': task.current_revision,
                    'stage': stage_payload(session, task), 'policy_binding': self._policy_binding_response(task),
                    'plan': plan.payload if plan else None, 'assessment': {**assessment.payload, 'assessment_id': assessment.artifact_id} if assessment else None,
                    'evidence': [{**self._evidence_payload(session, r), 'superseded': r.evidence_id in superseded} for r in records],
                    'legacy_result': task.workflow_contract_version != WORKFLOW_VERSION}

    def plan_compliance(self, context, *, evaluated_at):
        """The published catalogue defines coverage; retrieval Top-3 never defines scope."""
        from .service import content_hash
        from ..rag.models import PolicyClause, PolicyDocument, PolicyIndex, PolicySet
        clauses, errors = [], []
        if context['policy_set_version']:
            with self.session_factory() as session:
                pair = session.execute(select(PolicyIndex, PolicySet).join(PolicySet,
                    PolicyIndex.policy_set_record_id == PolicySet.policy_set_record_id).where(
                    PolicyIndex.policy_index_version == context['policy_index_version'],
                    PolicyIndex.status == 'PUBLISHED', PolicySet.status.in_(('PUBLISHED', 'INACTIVE')),
                    PolicySet.policy_set_version == context['policy_set_version'])).one_or_none()
                if pair is None:
                    errors.append({'code': 'POLICY_CATALOGUE_UNAVAILABLE', 'message': 'The frozen policy catalogue is unavailable. Repair the policy binding.'})
                else:
                    rows = session.execute(select(PolicyClause, PolicyDocument).join(PolicyDocument,
                        PolicyClause.policy_document_record_id == PolicyDocument.policy_document_record_id).where(
                        PolicyClause.policy_set_record_id == pair[0].policy_set_record_id,
                        PolicyDocument.effective_from <= evaluated_at,
                        (PolicyDocument.effective_to.is_(None) | (PolicyDocument.effective_to > evaluated_at)))
                        .order_by(PolicyClause.clause_id)).all()
                    for clause, doc in rows:
                        if context['policy_category'] not in doc.categories or context['policy_region'] not in doc.regions:
                            continue
                        clauses.append({'clause_id': clause.clause_id, 'control_code': clause.control_code,
                            'document_id': doc.document_id, 'document_version': doc.document_version,
                            'policy_id': doc.policy_id, 'title': doc.title, 'section': clause.section,
                            'text': clause.text, 'content_sha256': clause.content_sha256,
                            'rule_parameters': clause.rule_parameters, 'policy_set_version': context['policy_set_version']})
                    if not clauses:
                        errors.append({'code': 'NO_APPLICABLE_POLICY', 'message': 'No clauses apply to the current category, region, and date. This cannot be treated as verified.'})
        return {'schema_version': WORKFLOW_VERSION, 'task_revision': context['task_revision'],
                'policy_set_version': context['policy_set_version'], 'policy_index_version': context['policy_index_version'],
                'category': context['policy_category'], 'region': context['policy_region'],
                'evaluated_at': evaluated_at.isoformat(), 'clauses': clauses, 'policy_errors': errors,
                'catalogue_hash': content_hash(clauses)}

    def assess_compliance(self, state, plan):
        from .service import content_hash
        from ..rules.compliance import (
            ComplianceEvidence,
            ExecutableRuleParameters,
            evaluate_compliance_rule,
        )
        context = self.workflow_context(state['graph_run_id'])
        comparison = self.artifact_payload(state['comparison_result_id'])
        snapshot = self.artifact_payload(state['snapshot_id'])
        requirement = context['requirement']
        documents = {d['quote_id']: d for d in context['documents']}
        retrievals = [self.artifact_payload(aid) for aid in state.get('policy_retrieval_artifact_ids', {}).values()]
        citations = {c['clause_id']: c for r in retrievals if r['status'] == 'OK' for c in r.get('citations', [])}
        errors = list(plan['policy_errors'])
        for retrieval in retrievals:
            if retrieval.get('status') != 'OK':
                errors.append({'code': 'POLICY_RETRIEVAL_' + retrieval.get('status', 'ERROR'),
                    'retrieval_id': retrieval.get('retrieval_id'), 'message': 'Policy retrieval did not succeed. Repair or retry it first; evidence confirmation cannot bypass this requirement.'})
        for clause in plan['clauses']:
            citation = citations.get(clause['clause_id'])
            if not citation or any(citation.get(k) != clause[k] for k in ('text', 'content_sha256', 'document_id', 'document_version', 'policy_set_version', 'policy_id', 'control_code', 'section')):
                errors.append({'code': 'POLICY_COVERAGE_MISSING', 'clause_id': clause['clause_id'],
                               'message': 'This applicable clause has no reviewed retrieval citation. Repair retrieval and try again.'})
        executable_rules = {}
        invalid_rule_ids = []
        for clause in plan['clauses']:
            try:
                rule = ExecutableRuleParameters.model_validate(clause.get('rule_parameters'))
                if rule.control_code != clause['control_code']:
                    raise ValueError('control code mismatch')
                executable_rules[clause['clause_id']] = rule
            except (TypeError, ValueError):
                invalid_rule_ids.append(clause['clause_id'])
        if invalid_rule_ids:
            errors.append({
                'code': 'POLICY_EXECUTABLE_RULES_INVALID',
                'clause_ids': sorted(invalid_rule_ids),
                'message': (
                    f'The current policy revision has {len(invalid_rule_ids)} clauses without reviewed executable-rule parameters. '
                    'Ask the policy administrator to publish a new revision; supplier evidence cannot resolve this issue.'
                ),
            })
        with self.session_factory() as session:
            records = session.scalars(select(ComplianceEvidenceRecord).where(
                ComplianceEvidenceRecord.task_id == state['task_id'],
                ComplianceEvidenceRecord.task_revision <= state['task_revision'])).all()
            replaced = {r.previous_evidence_id for r in records if r.previous_evidence_id}
            records = [r for r in records if r.evidence_id not in replaced]
            evidence_payloads = [self._evidence_payload(session, r) for r in records]
        assessments, missing_ids, eligibility, amounts = [], [], {}, []
        enabled = bool(context['policy_set_version'])
        for row in comparison['supplier_results']:
            quote_id = row['quote_id']
            doc = documents[quote_id]
            checks = []
            evidence = [ComplianceEvidence.model_validate({**{k: v for k, v in r['facts'].items()
                        if k in ComplianceEvidence.model_fields}, 'evidence_id': r['evidence_id']})
                        for r in evidence_payloads if r['quote_id'] == quote_id]
            for clause in plan['clauses']:
                rule = executable_rules.get(clause['clause_id'])
                params = rule.model_dump(mode='json') if rule is not None else None
                check = evaluate_compliance_rule(clause_id=clause['clause_id'], parameters=params,
                    supplier_id=doc['supplier_id'], manufacturer=requirement['manufacturer'],
                    manufacturer_part_number=requirement['manufacturer_part_number'],
                    evaluated_at=datetime.fromisoformat(plan['evaluated_at']), evidence=evidence,
                    amount=Decimal(row['total_cost']) if row.get('total_cost') is not None else None,
                    currency=requirement['currency'], execution_stage=(params or {}).get('execution_stage', 'BEFORE_RECOMMENDATION'))
                data = check.model_dump(mode='json')
                data.update({'control_code': clause['control_code'], 'citation_ids': [citations[clause['clause_id']]['citation_id']]
                    if clause['clause_id'] in citations else [], 'item_id': f"{quote_id}:{clause['clause_id']}",
                    'execution_stage': data.get('execution_stage') or 'BEFORE_RECOMMENDATION'})
                if row['status'] != 'FEASIBLE':
                    data.update(status='NOT_EVALUATED', reason_codes=['QUOTE_NOT_FEASIBLE'])
                checks.append(data)
                if rule is not None and clause['control_code'] == 'AMOUNT_APPROVAL':
                    amounts.append({'quote_id': quote_id, 'supplier_name': row.get('supplier_name'), **data,
                                    'amount': row.get('total_cost'),
                                    'currency': requirement['currency'], 'threshold': params.get('threshold')})
                if rule is not None and data['status'] == 'REVIEW_REQUIRED' and row['status'] == 'FEASIBLE':
                    missing_ids.append(data['item_id'])
            before = [c for c in checks if c['execution_stage'] == 'BEFORE_RECOMMENDATION' and c['control_code'] != 'AMOUNT_APPROVAL']
            status = ('EXCLUDED' if any(c['status'] == 'FAIL' for c in before) else
                      'UNVERIFIED' if any(c['status'] not in ('PASS', 'NOT_APPLICABLE') for c in before) or errors else 'VERIFIED')
            if not enabled:
                status = 'UNVERIFIED'
            eligibility[quote_id] = {'status': status, 'reasons': tuple(code for c in before for code in c.get('reason_codes', []))}
            overall = 'NOT_EVALUATED' if row['status'] != 'FEASIBLE' else {
                'VERIFIED': 'COMPLIANT', 'UNVERIFIED': 'REVIEW_REQUIRED', 'EXCLUDED': 'NON_COMPLIANT'}[status]
            assessments.append({'quote_id': quote_id, 'quote_version': row['quote_version'], 'supplier_id': doc['supplier_id'],
                                'supplier_name': row.get('supplier_name'), 'status': overall, 'eligibility': status, 'checks': checks})
        counts = {s: sum(r['status'] == s for r in assessments) for s in ('COMPLIANT','NON_COMPLIANT','REVIEW_REQUIRED','NOT_EVALUATED')}
        def blocks_publication(check):
            if 'EXECUTABLE_PARAMETERS_REQUIRED' in check.get('reason_codes', []):
                return False
            if check['control_code'] == 'AMOUNT_APPROVAL':
                return (check['execution_stage'] in ('BEFORE_RECOMMENDATION', 'BEFORE_PUBLICATION')
                        and (check['status'] not in ('PASS', 'NOT_APPLICABLE')
                             or check.get('triggered') is True and not check.get('approval_confirmed')))
            return (check['execution_stage'] == 'BEFORE_PUBLICATION'
                    and check['status'] not in ('PASS', 'NOT_APPLICABLE'))

        publication_blockers = {row['quote_id']: [check['item_id'] for check in row['checks']
            if blocks_publication(check)]
            for row in assessments if row['status'] != 'NOT_EVALUATED'}
        payload = {'schema_version': WORKFLOW_VERSION,
            'task_id': state['task_id'], 'task_revision': state['task_revision'], 'plan_id': state['compliance_plan_id'],
            'base_snapshot_id': state['snapshot_id'], 'base_comparison_id': state['comparison_result_id'],
            'policy_enabled': enabled, 'policy_set_version': context['policy_set_version'],
            'policy_index_version': context['policy_index_version'], 'evaluated_at': plan['evaluated_at'],
            'strategy': 'VERIFIED_FIRST', 'assessments': assessments, 'counts': counts,
            'evidence': evidence_payloads, 'policy_errors': errors, 'missing_item_ids': sorted(missing_ids),
            'policy_eligibility': eligibility, 'amount_requirements': amounts,
            'publication_blockers': publication_blockers, 'publication_blocked': any(publication_blockers.values()),
            'retrieval_artifact_ids': state.get('policy_retrieval_artifact_ids', {}),
            'requires_human_review': bool(missing_ids or errors), 'citations': list(citations.values()),
            'retrievals': retrievals, 'disposition': 'COMPLIANT_SUPPLIERS_AVAILABLE' if counts['COMPLIANT'] else 'NO_CONFIRMED_COMPLIANT_SUPPLIER',
            'recommendation_scope': 'COMPLIANCE_VERIFIED' if enabled and counts['COMPLIANT'] else 'PROCUREMENT_COMPARISON_ONLY',
            'input_hash': content_hash({k: snapshot.get(k) for k in ('requirement','decision_profile','documents','policy_set_version','policy_index_version')})}
        artifact = self.append_artifact(task_id=state['task_id'], task_revision=state['task_revision'],
            artifact_type='COMPLIANCE_ASSESSMENT', schema_version=WORKFLOW_VERSION, payload=payload,
            parent_artifact_id=state['snapshot_id'], graph_run_id=state['graph_run_id'])
        # Public ID is the immutable artifact ID, not a separately addressable record.
        return artifact['artifact_id']

    def _compliance_new_run(self, session, task, previous):
        from .service import new_id, content_hash
        graph_id, job_id = new_id('graph'), new_id('job')
        graph = GraphRun(graph_run_id=graph_id, task_id=task.task_id, thread_id=graph_id,
            started_revision=task.current_revision, effective_revision=task.current_revision, status='PENDING',
            history_binding_id=previous.history_binding_id if previous else None,
            provider=previous.provider if previous else None, model_id=previous.model_id if previous else None,
            environment=previous.environment if previous else None, prompt_version=previous.prompt_version if previous else None)
        session.add(graph)
        session.flush()
        if previous:
            carry = session.scalar(select(WorkflowArtifact).where(WorkflowArtifact.graph_run_id == previous.graph_run_id,
                WorkflowArtifact.artifact_type == 'CARRIED_PAYMENT_SUPPLEMENTS').order_by(WorkflowArtifact.created_at.desc()))
            supplements = dict(carry.payload) if carry else {}
            for issue in session.scalars(select(Issue).where(Issue.graph_run_id == previous.graph_run_id,
                Issue.issue_type == 'PAYMENT_INFORMATION', Issue.status == 'RESOLVED')).all():
                if issue.quote_id and issue.answer_payload:
                    supplements[issue.quote_id] = {**issue.answer_payload, 'evidence_ref': f'ISSUE:{issue.issue_id}'}
            if supplements:
                session.add(WorkflowArtifact(artifact_id=new_id('artifact'), task_id=task.task_id, task_revision=task.current_revision,
                    artifact_type='CARRIED_PAYMENT_SUPPLEMENTS', payload=supplements, content_sha256=content_hash(supplements),
                    graph_run_id=graph_id))
            for old in session.scalars(select(DocumentExecution).where(DocumentExecution.graph_run_id == previous.graph_run_id)).all():
                session.add(DocumentExecution(document_execution_id=new_id('docexec'), graph_run_id=graph_id,
                    document_id=old.document_id, max_calls=old.max_calls, calls_used=old.calls_used,
                    batch_artifact_id=old.batch_artifact_id, review_artifact_id=old.review_artifact_id,
                    status=old.status))
        session.add(Job(job_id=job_id, task_id=task.task_id, graph_run_id=graph_id, job_type='COMPLIANCE_START',
            status='PENDING', task_revision=task.current_revision, history_binding_id=graph.history_binding_id))
        task.current_graph_run_id, task.status = graph_id, 'QUEUED'
        task.workflow_contract_version = WORKFLOW_VERSION
        return {'task_id': task.task_id, 'task_revision': task.current_revision, 'graph_run_id': graph_id,
                'job_id': job_id, 'job_type': 'COMPLIANCE_START', 'job_status': 'PENDING'}

    def _compliance_preparation_graph(self, session, task, previous):
        """Keep reviewed quote executions current while evidence is staged without a run."""
        from .service import new_id
        graph_id = new_id('graph')
        graph = GraphRun(graph_run_id=graph_id, task_id=task.task_id, thread_id=graph_id,
            started_revision=task.current_revision, effective_revision=task.current_revision, status='INTERRUPTED',
            history_binding_id=previous.history_binding_id if previous else None,
            provider=previous.provider if previous else None, model_id=previous.model_id if previous else None,
            environment=previous.environment if previous else None, prompt_version=previous.prompt_version if previous else None)
        session.add(graph)
        session.flush()
        if previous:
            for old in session.scalars(select(DocumentExecution).where(
                    DocumentExecution.graph_run_id == previous.graph_run_id)).all():
                session.add(DocumentExecution(document_execution_id=new_id('docexec'), graph_run_id=graph_id,
                    document_id=old.document_id, max_calls=old.max_calls, calls_used=old.calls_used,
                    batch_artifact_id=old.batch_artifact_id, review_artifact_id=old.review_artifact_id,
                    status=old.status))
        task.current_graph_run_id = graph_id
        task.workflow_contract_version = WORKFLOW_VERSION
        return graph

    def start_compliance(self, task_id, *, expected_task_revision, idempotency_key):
        """Start the explicit compliance stage while carrying reviewed quote facts."""
        from .service import ConflictError, content_hash
        request = {'task_id': task_id, 'revision': expected_task_revision}
        request_sha = content_hash(request)
        operation = f'compliance_start:{task_id}'
        with self.session_factory.begin() as session:
            task = self._compliance_task(session, task_id, lock=True)
            replay = self._existing_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha,
            )
            if replay is not None:
                return replay
            self._compliance_mutable(task)
            self._require_revision(task, expected_task_revision)
            current = session.get(GraphRun, task.current_graph_run_id) if task.current_graph_run_id else None
            if current is not None and current.status in {'PENDING', 'RUNNING'}:
                raise ConflictError('graph_run_active', 'The task already has an active graph run.')
            previous = self._supersede_current_graph(session, task)
            response = self._compliance_new_run(session, task, previous)
            self._save_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha, response_status=202, response=response,
            )
            return response

    def revalidate_compliance_confirmation(self, task_id, *, old_assessment_id, new_assessment_id, graph_run_id, task_revision):
        from .service import ConflictError, content_hash, new_id
        with self.session_factory.begin() as session:
            task = self._compliance_task(session, task_id, lock=True)
            if task.current_revision != task_revision or task.current_graph_run_id != graph_run_id:
                raise ConflictError('compliance_input_changed', 'Compliance revalidation is stale.')
            confirmation = latest_artifact(session, task, 'COMPLIANCE_CONFIRMATION')
            old, new = session.get(WorkflowArtifact, old_assessment_id), session.get(WorkflowArtifact, new_assessment_id)
            if not confirmation or confirmation.payload['assessment_id'] != old_assessment_id or not old or not new:
                return False
            old_plan = session.get(WorkflowArtifact, old.payload['plan_id'])
            new_plan = session.get(WorkflowArtifact, new.payload['plan_id'])
            keys = ('input_hash', 'assessments', 'amount_requirements', 'policy_errors', 'missing_item_ids', 'evidence')
            old_comparison = session.get(WorkflowArtifact, old.payload['base_comparison_id'])
            new_comparison = session.get(WorkflowArtifact, new.payload['base_comparison_id'])
            if (not old_plan or not new_plan or old_plan.payload['catalogue_hash'] != new_plan.payload['catalogue_hash']
                or any(old.payload[k] != new.payload[k] for k in keys)
                or not old_comparison or not new_comparison
                or old_comparison.payload['supplier_results'] != new_comparison.payload['supplier_results']):
                return False
            payload = {**confirmation.payload, 'assessment_id': new_assessment_id,
                       'source_confirmation_id': confirmation.artifact_id, 'revalidated_at': datetime.now(timezone.utc).isoformat()}
            session.add(WorkflowArtifact(artifact_id=new_id('artifact'), task_id=task_id, task_revision=task_revision,
                artifact_type='COMPLIANCE_CONFIRMATION', schema_version=WORKFLOW_VERSION, payload=payload,
                parent_artifact_id=confirmation.artifact_id, content_sha256=content_hash(payload), graph_run_id=graph_run_id))
            return True

    def save_compliance_evidence(self, task_id, *, expected_task_revision, facts, idempotency_key,
                                 previous_evidence_id=None, file=None, filename=None, run_after_save=True):
        from .service import BackendError, ConflictError, NotFoundError, content_hash, new_id
        facts = EvidenceInput.model_validate(facts)
        payload = facts.model_dump(mode='json')
        file_bytes, media_type, sha = None, None, None
        if file is not None:
            suffix = Path(filename or '').suffix.lower()
            media_type = {'.pdf': 'application/pdf', '.txt': 'text/plain', '.md': 'text/markdown'}.get(suffix)
            if not media_type:
                raise BackendError('unsupported_evidence_type', 'Evidence supports only PDF, TXT, or MD files.')
            file_bytes = file.read(MAX_EVIDENCE_BYTES + 1)
            if not file_bytes or len(file_bytes) > MAX_EVIDENCE_BYTES:
                raise BackendError('evidence_size_invalid', 'Evidence cannot be empty or exceed 10 MiB.')
            if suffix == '.pdf' and not file_bytes.startswith(b'%PDF-'):
                raise BackendError('evidence_content_invalid', 'The file content is not a PDF.')
            if suffix in ('.txt', '.md'):
                try:
                    text = file_bytes.decode('utf-8-sig')
                    if '\x00' in text:
                        raise ValueError()
                except (UnicodeError, ValueError):
                    raise BackendError('evidence_content_invalid', 'Text evidence must use UTF-8 encoding.')
            sha = hashlib.sha256(file_bytes).hexdigest()
        if not facts.source_refs and file_bytes is None:
            raise BackendError('evidence_source_required', 'Attach the source file or enter a traceable source record.')
        request = {'revision': expected_task_revision, 'facts': payload, 'previous': previous_evidence_id,
                   'filename': filename, 'file_sha256': sha, 'run_after_save': run_after_save}
        request_sha = content_hash(request)
        operation = f'compliance_evidence:{task_id}'
        created_path = None
        try:
            with self.session_factory.begin() as session:
                task = self._compliance_task(session, task_id, lock=True)
                replay = self._existing_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha)
                if replay is not None:
                    return replay
                self._compliance_mutable(task)
                self._require_revision(task, expected_task_revision)
                quote = session.get(Quote, facts.quote_id)
                if not quote or quote.task_id != task_id or not quote.active:
                    raise NotFoundError('quote_not_found', 'Quote was not found in this task.')
                old = session.get(ComplianceEvidenceRecord, previous_evidence_id) if previous_evidence_id else None
                if previous_evidence_id and (old is None or old.task_id != task_id or old.quote_id != facts.quote_id or old.control_code != facts.control_code):
                    raise NotFoundError('evidence_not_found', 'Evidence was not found for this task and check.')
                if old and session.scalar(select(ComplianceEvidenceRecord.evidence_id).where(ComplianceEvidenceRecord.previous_evidence_id == old.evidence_id)):
                    raise ConflictError('evidence_superseded', 'This material version has already been replaced.')
                evidence_id = new_id('evidence')
                if file_bytes is not None:
                    file_id = new_id('evfile')
                    created_path = self.storage_root / 'compliance' / task_id / evidence_id / file_id
                    created_path.parent.mkdir(parents=True, exist_ok=True)
                    with created_path.open('xb') as stream:
                        stream.write(file_bytes)
                    payload['source_refs'] = [*payload['source_refs'], f'FILE:{file_id}']
                previous = self._supersede_current_graph(session, task)
                task.current_revision += 1
                record = ComplianceEvidenceRecord(evidence_id=evidence_id, task_id=task_id,
                    task_revision=task.current_revision, quote_id=facts.quote_id, control_code=facts.control_code,
                    version=old.version + 1 if old else 1, previous_evidence_id=previous_evidence_id,
                    facts=payload, content_sha256=content_hash(payload), confirmed_by=self.actor_id)
                session.add(record)
                session.flush()
                if file_bytes is not None:
                    session.add(ComplianceEvidenceFile(file_id=file_id, evidence_id=evidence_id,
                        original_filename=Path(filename.replace('\\','/')).name, media_type=media_type,
                        size_bytes=len(file_bytes), sha256=sha, storage_path=str(created_path)))
                session.add(TaskRevision(revision_id=new_id('revision'), task_id=task_id, revision=task.current_revision,
                    change_type='COMPLIANCE_EVIDENCE', actor_id=self.actor_id, request_sha256=request_sha,
                    details={'evidence_id': evidence_id, 'previous_evidence_id': previous_evidence_id}))
                if run_after_save:
                    response = {**self._compliance_new_run(session, task, previous), 'evidence_id': evidence_id,
                                'analysis_started': True}
                else:
                    self._compliance_preparation_graph(session, task, previous)
                    task.status = 'NEEDS_INPUT'
                    response = {'task_id': task.task_id, 'task_revision': task.current_revision,
                                'status': task.status, 'evidence_id': evidence_id, 'analysis_started': False}
                self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha,
                                      response_status=202, response=response)
                return response
        except Exception:
            if created_path is not None:
                created_path.unlink(missing_ok=True)
            raise

    def confirm_compliance(self, task_id, *, expected_task_revision, expected_assessment_id,
                           acknowledged_missing_item_ids, acknowledge_no_policy=False, idempotency_key):
        from .service import ConflictError, content_hash, new_id
        request = {'revision': expected_task_revision, 'assessment_id': expected_assessment_id,
                   'missing': sorted(acknowledged_missing_item_ids), 'no_policy': acknowledge_no_policy}
        request_sha, operation = content_hash(request), f'compliance_confirm:{task_id}'
        with self.session_factory.begin() as session:
            task = self._compliance_task(session, task_id, lock=True)
            replay = self._existing_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha)
            if replay is not None:
                return replay
            self._compliance_mutable(task)
            self._require_revision(task, expected_task_revision)
            assessment = latest_artifact(session, task, 'COMPLIANCE_ASSESSMENT')
            if not assessment or assessment.artifact_id != expected_assessment_id:
                raise ConflictError('compliance_assessment_stale', 'The verification result has changed. Refresh and confirm it again.')
            if assessment.payload['policy_errors']:
                raise ConflictError('compliance_policy_blocked', 'Resolve policy retrieval or clause-coverage issues first.')
            if set(acknowledged_missing_item_ids) != set(assessment.payload['missing_item_ids']):
                raise ConflictError('compliance_missing_acknowledgement', 'Explicitly acknowledge the evidence items being deferred.')
            if not assessment.payload['policy_enabled'] and not acknowledge_no_policy:
                raise ConflictError('compliance_no_policy_acknowledgement', 'Confirm that policy verification is not enabled for this task.')
            prior_confirmation = latest_artifact(session, task, 'COMPLIANCE_CONFIRMATION')
            if prior_confirmation and prior_confirmation.payload.get('assessment_id') == assessment.artifact_id:
                raise ConflictError('compliance_already_confirmed', 'This revision has already been confirmed.')
            previous = self._supersede_current_graph(session, task)
            task.current_revision += 1
            copied_id = new_id('artifact')
            copied = {**assessment.payload, 'assessment_id': copied_id, 'task_revision': task.current_revision,
                      'source_assessment_id': assessment.artifact_id}
            session.add(WorkflowArtifact(artifact_id=copied_id, task_id=task_id, task_revision=task.current_revision,
                artifact_type='COMPLIANCE_ASSESSMENT', schema_version=WORKFLOW_VERSION, payload=copied,
                parent_artifact_id=assessment.artifact_id, content_sha256=content_hash(copied)))
            confirmation = {'assessment_id': copied_id, 'actor_id': self.actor_id, 'confirmed_at': datetime.now(timezone.utc).isoformat(),
                            'task_revision': task.current_revision, 'acknowledged_missing_item_ids': sorted(acknowledged_missing_item_ids),
                            'acknowledge_no_policy': acknowledge_no_policy}
            session.add(WorkflowArtifact(artifact_id=new_id('artifact'), task_id=task_id, task_revision=task.current_revision,
                artifact_type='COMPLIANCE_CONFIRMATION', schema_version=WORKFLOW_VERSION, payload=confirmation,
                parent_artifact_id=copied_id, content_sha256=content_hash(confirmation)))
            plan = session.get(WorkflowArtifact, assessment.payload['plan_id'])
            if plan:
                plan_payload = {**plan.payload, 'task_revision': task.current_revision}
                session.add(WorkflowArtifact(artifact_id=new_id('artifact'), task_id=task_id, task_revision=task.current_revision,
                    artifact_type='COMPLIANCE_PLAN', schema_version=WORKFLOW_VERSION, payload=plan_payload,
                    parent_artifact_id=plan.artifact_id, content_sha256=content_hash(plan_payload)))
            session.add(TaskRevision(revision_id=new_id('revision'), task_id=task_id, revision=task.current_revision,
                change_type='COMPLIANCE_CONFIRMATION', actor_id=self.actor_id, request_sha256=request_sha, details=confirmation))
            response = {**self._compliance_new_run(session, task, previous), 'assessment_id': copied_id}
            self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha,
                                  response_status=202, response=response)
            return response

    def compliance_evidence_content(self, task_id, evidence_id, file_id):
        from .service import NotFoundError, content_hash, new_id
        with self.session_factory.begin() as session:
            task = self._compliance_task(session, task_id)
            record = session.get(ComplianceEvidenceRecord, evidence_id)
            file = session.get(ComplianceEvidenceFile, file_id)
            if not record or record.task_id != task_id or not file or file.evidence_id != evidence_id:
                raise NotFoundError('evidence_file_not_found', 'Evidence file was not found.')
            path = Path(file.storage_path).resolve()
            root = (self.storage_root / 'compliance' / task_id / evidence_id).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise NotFoundError('evidence_file_not_found', 'Evidence file was not found.')
            audit = {'actor_id': self.actor_id, 'evidence_id': evidence_id, 'file_id': file_id}
            session.add(WorkflowArtifact(artifact_id=new_id('artifact'), task_id=task_id, task_revision=task.current_revision,
                artifact_type='COMPLIANCE_FILE_ACCESS', payload=audit, content_sha256=content_hash(audit)))
            return {'path': path, 'media_type': file.media_type, 'filename': file.original_filename}
