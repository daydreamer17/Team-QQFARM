import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'
import { api } from '../src/api/client'
import { CompliancePage } from '../src/pages/CompliancePage'
import { DecisionPage } from '../src/pages/DecisionPage'
import { ExecutableRuleEditor } from '../src/components/ExecutableRuleEditor'
import { useState } from 'react'
import { ComplianceAssessmentDetails } from '../src/components/ComplianceAssessmentDetails'

const task = {
  task_id: 'task-1', task_name: '采购测试', task_revision: 3, status: 'NEEDS_INPUT',
  workflow_contract_version: 'compliance/2.0', current_result_id: null,
  quotes: [{ quote_id: 'quote-1', supplier_id: 'SUP-1', quote_version: 1 }],
  requirement: { required_quantity: 100, quantity_unit: 'piece', manufacturer: 'Maker', manufacturer_part_number: 'PART', currency: 'SGD' },
  progress: { requirement_completed: true, quote_review_completed: true, decision_completed: false, summary_completed: false,
    compliance: { can_compare: false, confirmed: false, status: 'AWAITING_CONFIRMATION' } },
}
const workspace = {
  task_id: 'task-1', task_revision: 3, policy_binding: {}, plan: { clauses: [], policy_errors: [] }, evidence: [],
  stage: { status: 'AWAITING_CONFIRMATION', assessment_id: 'assessment-1', can_confirm: true, can_compare: false, confirmed: false },
  assessment: { assessment_id: 'assessment-1', policy_enabled: true, missing_item_ids: ['missing-1'], policy_errors: [],
    amount_requirements: [], assessments: [{ quote_id: 'quote-1', supplier_id: 'SUP-1', supplier_name: 'Supplier One',
      quote_version: 1, status: 'REVIEW_REQUIRED', eligibility: 'UNVERIFIED',
      checks: [{ clause_id: 'rohs-1', control_code: 'ROHS_COMPLIANCE', status: 'REVIEW_REQUIRED',
        item_id: 'missing-1', reason_codes: ['EVIDENCE_MISSING'], evidence_ids: [], source_refs: [], citation_ids: [] }] }] },
}
function mount(page = <CompliancePage />, path = 'compliance', hash = '') {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={[`/tasks/task-1/${path}${hash}`]}><Routes>
      <Route path={`/tasks/:taskId/${path}`} element={page} />
      <Route path="/tasks/:taskId/decision" element={<p>正在生成决策</p>} />
  </Routes></MemoryRouter></QueryClientProvider>)
}
async function openFirstSupplierMaterial(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Expand Supplier One Checks and Evidence' }))
  const opener = screen.getByRole('button', { name: 'Upload' })
  await user.click(opener)
  return opener
}
beforeEach(() => {
  vi.spyOn(api, 'getTask').mockResolvedValue(task as never)
  vi.spyOn(api, 'getCompliance').mockResolvedValue(structuredClone(workspace) as never)
})
test('compliance independently loads before results and requires per-item missing acknowledgement', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(api, 'confirmCompliance').mockResolvedValue({ task_revision: 4 } as never)
  const getResult = vi.spyOn(api, 'getResult')
  mount()
  const results = await screen.findByRole('table', { name: 'Supplier checks' })
  expect(within(results).getByText('Supplier One')).toBeInTheDocument()
  expect(getResult).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Compliance: Action required')).not.toHaveClass('task-timeline-complete')
  const button = screen.getByRole('button', { name: 'Confirm' })
  expect(button).toBeDisabled()
  await user.click(screen.getByLabelText('Select all items requiring additional evidence'))
  expect(screen.getByLabelText(/Defer/)).toBeChecked()
  await user.click(button)
  await waitFor(() => expect(confirm).toHaveBeenCalledWith('task-1', expect.objectContaining({
    expected_task_revision: 3, expected_assessment_id: 'assessment-1', acknowledged_missing_item_ids: ['missing-1'], acknowledge_no_policy: false,
  }), expect.any(String)))
})
test('evidence submission requires explicit coverage, a source, and preserves material identity', async () => {
  const user = userEvent.setup()
  const save = vi.spyOn(api, 'saveComplianceEvidence').mockResolvedValue({ task_revision: 4 } as never)
  mount()
  await openFirstSupplierMaterial(user)
  expect(screen.getByRole('button', { name: 'Save evidence and rerun checks' })).toBeDisabled()
  await user.type(screen.getByLabelText('Evidence ID'), 'CERT-1')
  await user.type(screen.getByLabelText('Source references'), 'https://example.test/cert')
  await user.clear(screen.getByLabelText('Evidence manufacturer'))
  await user.type(screen.getByLabelText('Evidence manufacturer'), 'Other Maker')
  await user.click(screen.getByLabelText(/I have reviewed the evidence source document/))
  await user.click(screen.getByRole('button', { name: 'Save evidence and rerun checks' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', expect.objectContaining({
    expectedTaskRevision: 3, facts: expect.objectContaining({ quote_id: 'quote-1', supplier_id: 'SUP-1',
      manufacturer: 'Other Maker', coverage_confirmed: true, expires_on: null, permanent: false }),
  }), expect.any(String)))
})
test('selecting a proof file parses and fills its facts while surfacing identity mismatches', async () => {
  const user = userEvent.setup()
  const parse = vi.spyOn(api, 'parseComplianceEvidence').mockResolvedValue({
    status: 'FOUND', source_filename: 'SUP-022-rohs-valid.md', missing_fields: [],
    parsed_fields: ['effective_from', 'expires_on', 'manufacturer', 'manufacturer_part_number', 'material_number', 'outcome', 'supplier_id'],
    facts: {
      material_number: 'ROHS-SUP-022-V1', supplier_id: 'SUP-022', manufacturer: 'QQ Demo Components',
      manufacturer_part_number: 'QW-MCU9-DEMO', outcome: 'PASS', effective_from: '2026-01-01',
      expires_on: '2027-01-31', permanent: false, source_refs: ['synthetic declaration'],
    },
  })
  mount()
  await openFirstSupplierMaterial(user)
  const file = new File(['Certificate ID: ROHS-SUP-022-V1'], 'SUP-022-rohs-valid.md', { type: 'text/markdown' })
  await user.upload(screen.getByLabelText(/Evidence attachment/), file)
  await waitFor(() => expect(parse).toHaveBeenCalledWith('task-1', 'ROHS_COMPLIANCE', file))
  expect(screen.getByLabelText('Evidence ID')).toHaveValue('ROHS-SUP-022-V1')
  expect(screen.getByLabelText('Evidence supplier ID')).toHaveValue('SUP-022')
  expect(screen.getByLabelText('Evidence manufacturer')).toHaveValue('QQ Demo Components')
  expect(screen.getByLabelText('Evidence manufacturer part number')).toHaveValue('QW-MCU9-DEMO')
  expect(screen.getByLabelText('Effective from')).toHaveValue('2026-01-01')
  expect(screen.getByLabelText('Effective to')).toHaveValue('2027-01-31')
  expect(screen.getByRole('status')).toHaveTextContent('supplier ID SUP-022 in the file does not match SUP-1 in the current quotation')
  expect(screen.getByLabelText(/I have reviewed the evidence source document/)).not.toBeChecked()
})
test('a proof selected from the wrong material entry is not partially filled', async () => {
  const user = userEvent.setup()
  vi.spyOn(api, 'parseComplianceEvidence').mockResolvedValue({
    status: 'TYPE_MISMATCH', source_filename: 'SUP-029-rohs-failed.md', missing_fields: [], parsed_fields: [],
    detected_control_code: 'ROHS_COMPLIANCE', facts: {},
  })
  const data = structuredClone(workspace)
  data.assessment.assessments[0].checks[0].control_code = 'APPROVED_SUPPLIER'
  vi.mocked(api.getCompliance).mockResolvedValue(data as never)
  mount()
  await openFirstSupplierMaterial(user)
  await user.upload(screen.getByLabelText(/Evidence attachment/), new File(['Certificate ID: RH-1'], 'SUP-029-rohs-failed.md', { type: 'text/markdown' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Evidence type mismatch: this appears to be RoHS evidence')
  expect(screen.getByLabelText('Evidence ID')).toHaveValue('')
})
test('no policy requires explicit acknowledgement before continuing', async () => {
  const user = userEvent.setup()
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, policy_binding: null,
    assessment: { ...workspace.assessment, policy_enabled: false, missing_item_ids: [], assessments: [] } } as never)
  vi.spyOn(api, 'confirmCompliance').mockResolvedValue({ task_revision: 4 } as never)
  mount()
  expect(await screen.findByRole('button', { name: 'Confirm' })).toBeDisabled()
  await user.click(screen.getByLabelText(/Confirm that compliance review is disabled for this task/))
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled()
})
test('decision entry cannot bypass an unconfirmed compliance stage', async () => {
  mount(<DecisionPage />, 'decision')
  expect(await screen.findByRole('link', { name: 'Go to compliance review' })).toHaveAttribute('href', '/tasks/task-1/compliance')
})

test('a not-started workspace starts analysis instead of polling forever', async () => {
  const user = userEvent.setup()
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, assessment: null,
    stage: { status: 'NOT_STARTED', confirmed: false, can_confirm: false, can_compare: false } } as never)
  const start = vi.spyOn(api, 'startCompliance').mockResolvedValue({ task_revision: 3, job_id: 'job-1' } as never)
  mount()
  await user.click(await screen.findByRole('button', { name: 'Start' }))
  await waitFor(() => expect(start).toHaveBeenCalledWith('task-1', 3, expect.any(String)))
})

test('existing evidence can be saved before the first compliance check', async () => {
  const user = userEvent.setup()
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, assessment: null,
    stage: { status: 'NOT_STARTED', confirmed: false, can_confirm: false, can_compare: false } } as never)
  const save = vi.spyOn(api, 'saveComplianceEvidence').mockResolvedValue({
    task_id: 'task-1', task_revision: 4, evidence_id: 'evidence-1', analysis_started: false,
  })
  const start = vi.spyOn(api, 'startCompliance')
  mount()

  expect(await screen.findByRole('heading', { name: 'Prepare evidence before checking' })).toBeInTheDocument()
  expect(screen.queryByText('Waiting for quotation processing to complete')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Upload Supplier eligibility record · SUP-1' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Prepare evidence for SUP-1' }))
  expect(screen.getByRole('button', { name: 'Collapse evidence for SUP-1' })).toHaveAttribute('aria-expanded', 'true')
  await user.click(screen.getByRole('button', { name: 'Upload Supplier eligibility record · SUP-1' }))
  await user.type(screen.getByLabelText('Evidence ID'), 'ADM-1')
  await user.type(screen.getByLabelText('Source references'), 'supplier-register/ADM-1')
  await user.click(screen.getByLabelText(/I have reviewed the evidence source document/))
  await user.click(screen.getByRole('button', { name: 'Save evidence' }))

  await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', expect.objectContaining({
    expectedTaskRevision: 3,
    runAfterSave: false,
    facts: expect.objectContaining({ quote_id: 'quote-1', supplier_id: 'SUP-1', control_code: 'APPROVED_SUPPLIER', currency: null }),
  }), expect.any(String)))
  expect(start).not.toHaveBeenCalled()
})

test('executable policy editor requires human review and keeps money as exact text', async () => {
  const user = userEvent.setup()
  function Editor() {
    const [value, setValue] = useState('{}')
    return <><ExecutableRuleEditor controlCode="AMOUNT_APPROVAL" value={value} onChange={setValue} readonly={false} /><output>{value}</output></>
  }
  render(<Editor />)
  await user.click(screen.getByLabelText('Configure an executable check for this clause'))
  await user.type(screen.getByLabelText('Amount threshold'), '10000.01')
  await user.type(screen.getByLabelText('Required Action'), '财务经理审核')
  await user.type(screen.getByLabelText('Reviewer'), '李明')
  await user.click(screen.getByLabelText(/I have reviewed the rule against the clause text/))
  const params = JSON.parse(screen.getByRole('status').textContent!)
  expect(params.threshold).toBe('10000.01')
  expect(params.reviewed_by).toBe('李明')
  expect(params.reviewed_at).toMatch(/^20/)
  await user.selectOptions(screen.getByLabelText('Execution Stage'), 'AFTER_SELECTION')
  expect(JSON.parse(screen.getByRole('status').textContent!).reviewed_at).toBe('')
})

test('historical assessment keeps frozen material downloads without sending readers to current evidence', async () => {
  const user = userEvent.setup()
  const assessment = { ...workspace.assessment, evidence: [{ evidence_id: 'evidence-old', quote_id: 'quote-1',
    facts: { material_number: 'OLD-CERT' }, version: 1, files: [{ file_id: 'file-old', original_filename: 'original.pdf' }] }] }
  render(<MemoryRouter><ComplianceAssessmentDetails assessment={assessment as never} taskId="task-1" resultId="result-old" historical /></MemoryRouter>)
  await user.click(screen.getByText('Compliance'))
  expect(screen.getByRole('link', { name: 'original.pdf' })).toHaveAttribute('href', '/api/v1/tasks/task-1/compliance/evidence/evidence-old/files/file-old/content?download=true')
  expect(screen.queryByRole('link', { name: 'View current compliance review and evidence' })).not.toBeInTheDocument()
})

test('bound policy summary and separate control columns preserve incomplete multi-clause checks', async () => {
  const user = userEvent.setup()
  const data = structuredClone(workspace)
  Object.assign(data.policy_binding, { policy_set_version: '2026.09', category: 'Electronics', region: 'SG' })
  Object.assign(data.plan, { clauses: [{ clause_id: 'rohs-1', title: 'Supplier Assurance Policy', text: 'Policy text' }] })
  data.assessment.assessments[0].checks.push({ ...data.assessment.assessments[0].checks[0], clause_id: 'rohs-2', status: 'PASS' },
    { ...data.assessment.assessments[0].checks[0], clause_id: 'admission-1', control_code: 'APPROVED_SUPPLIER', status: 'PASS' },
    { ...data.assessment.assessments[0].checks[0], clause_id: 'amount-1', control_code: 'AMOUNT_APPROVAL', triggered: true })
  Object.assign(data.assessment, { amount_requirements: [{ quote_id: 'quote-1', supplier_name: 'Supplier One', amount: '7000.00', currency: 'SGD', threshold: '6500.00', execution_stage: 'AFTER_SELECTION', triggered: true, approval_confirmed: false, action: 'Manager review' }] })
  vi.mocked(api.getCompliance).mockResolvedValue(data as never)
  mount()
  expect(await screen.findByText(/Policy revision v2026\.09 · Electronics procurement · Singapore/)).toBeInTheDocument()
  expect(screen.queryByText('SUPPLIER CHECKS')).not.toBeInTheDocument()
  expect(screen.queryByText('AMOUNT RULES')).not.toBeInTheDocument()
  expect(screen.queryByText('FINAL CHECK')).not.toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'Supplier eligibility' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'RoHS' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'Amount approval' })).toBeInTheDocument()
  expect(screen.queryByRole('columnheader', { name: 'Compliance conclusion' })).not.toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'Recommendation eligibility' })).toBeInTheDocument()
  const row = within(screen.getByRole('table', { name: 'Supplier checks' })).getByRole('row', { name: /Supplier One/ })
  expect(within(row).getByRole('cell', { name: 'Unverified' })).toBeInTheDocument()
  expect(within(row).getAllByRole('cell', { name: 'Review' })).toHaveLength(2)
  expect(within(row).getByRole('cell', { name: 'Passed' })).toBeInTheDocument()
  const amountRow = within(screen.getByRole('table', { name: 'Amount conditions and next actions' })).getByRole('row', { name: /Supplier One/ })
  expect(within(amountRow).getByText('After supplier selection')).toBeInTheDocument()
  expect(within(amountRow).getByText('Triggered')).toBeInTheDocument()
  expect(within(amountRow).getByText('Manager review')).toBeInTheDocument()
  expect(within(amountRow).getByRole('button', { name: 'Add' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Expand Supplier One Checks and Evidence' }))
  const amountCard = screen.getByRole('heading', { name: 'Amount approval' }).closest('article')!
  expect(within(amountCard).getByRole('button', { name: 'Upload' })).toBeInTheDocument()
})

test('amount approval editor captures exact approval facts and keeps approval separate from supplier qualification', async () => {
  const user = userEvent.setup()
  const data = structuredClone(workspace)
  Object.assign(data.assessment, { amount_requirements: [{ quote_id: 'quote-1', supplier_name: 'Supplier One',
    amount: '7000.00', currency: 'SGD', threshold: '6500.00', execution_stage: 'BEFORE_PUBLICATION',
    triggered: true, approval_confirmed: false, action: 'Manager review' }] })
  vi.mocked(api.getCompliance).mockResolvedValue(data as never)
  const save = vi.spyOn(api, 'saveComplianceEvidence').mockResolvedValue({ task_revision: 4 } as never)
  mount()
  await user.click(await screen.findByRole('button', { name: 'Add' }))
  expect(screen.queryByLabelText('Evidence manufacturer')).not.toBeInTheDocument()
  await user.type(screen.getByLabelText('Approval record number'), 'APR-1')
  await user.type(screen.getByLabelText('Approved Amount'), '8000.00')
  await user.type(screen.getByLabelText('Source references'), 'approval-register/APR-1')
  await user.type(screen.getByLabelText('Effective from'), '2026-09-24')
  await user.click(screen.getByLabelText(/I have reviewed the approval source document/))
  await user.click(screen.getByRole('button', { name: 'Save approval record and rerun checks' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', expect.objectContaining({ facts: expect.objectContaining({
    control_code: 'AMOUNT_APPROVAL', supplier_id: 'SUP-1', approval_amount: '8000.00', currency: 'SGD',
    material_number: 'APR-1', coverage_confirmed: true,
  }) }), expect.any(String)))
})

test('evidence dialog focuses its first field, traps focus and restores focus after Escape', async () => {
  const user = userEvent.setup()
  mount()
  const opener = await openFirstSupplierMaterial(user)
  const dialog = screen.getByRole('dialog', { name: /Add evidence.*Supplier One/ })
  expect(within(dialog).getByLabelText('Evidence ID')).toHaveFocus()
  const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
  cancel.focus()
  await user.tab()
  expect(within(dialog).getByLabelText('Evidence ID')).toHaveFocus()
  await user.tab({ shift: true })
  expect(cancel).toHaveFocus()
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
})

test('a specific check link chooses the supplier page and expands the requested check', async () => {
  const data = structuredClone(workspace)
  data.assessment.assessments = Array.from({ length: 9 }, (_, index) => ({ ...data.assessment.assessments[0],
    quote_id: `quote-${index + 1}`, supplier_name: `Supplier ${index + 1}`,
    checks: [{ ...data.assessment.assessments[0].checks[0], reason_codes: ['EVIDENCE_EXPIRED'] }] }))
  vi.mocked(api.getCompliance).mockResolvedValue(data as never)
  mount(undefined, 'compliance', '#quote=quote-9&check=rohs-1')
  const results = await screen.findByRole('table', { name: 'Supplier checks' })
  expect(within(results).getByText('Supplier 9')).toBeInTheDocument()
  expect(screen.getByText('The evidence has expired')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Collapse Supplier 9 Checks and Evidence' })).toHaveAttribute('aria-expanded', 'true')
  expect(document.getElementById('compliance-quote-9-rohs-1')).toHaveFocus()
  expect(screen.getByText('2 / 2')).toBeInTheDocument()
})

test('confirmed compliance with missing evidence is completed without being labelled as all passed', async () => {
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, stage: { ...workspace.stage,
    status: 'PROCESSED', confirmed: true, can_compare: true, pending_count: 1 } } as never)
  mount()
  expect(await screen.findByLabelText('Compliance: Processed · follow-up required')).toBeInTheDocument()
  expect(screen.getByText('1 item')).toBeInTheDocument()
  expect(screen.getByLabelText('Compliance: Processed · follow-up required')).toHaveClass('task-timeline-complete')
})

test('processing compliance shows an animated progress indicator', async () => {
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, assessment: null,
    stage: { status: 'PROCESSING', confirmed: false, can_confirm: false, can_compare: false } } as never)
  mount()
  const heading = await screen.findByText('Running compliance review')
  const status = heading.closest('[role="status"]')!
  expect(status.querySelector('.compliance-spinner')).toBeInTheDocument()
})

test('confirmed disabled policy remains explicitly not enabled', async () => {
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, stage: { ...workspace.stage,
    status: 'DISABLED', confirmed: true, can_compare: true, pending_count: 0 } } as never)
  mount()
  expect(await screen.findByLabelText('Compliance: Disabled')).toHaveClass('task-timeline-complete')
})

test('current assessment links identify the exact supplier and check', async () => {
  const user = userEvent.setup()
  render(<MemoryRouter><ComplianceAssessmentDetails assessment={workspace.assessment as never} taskId="task-1" /></MemoryRouter>)
  await user.click(screen.getByText('Compliance'))
  expect(screen.getByRole('link', { name: 'View supplier evidence' })).toHaveAttribute('href', '/tasks/task-1/compliance#quote=quote-1')
  expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/tasks/task-1/compliance#quote=quote-1&check=rohs-1')
})
