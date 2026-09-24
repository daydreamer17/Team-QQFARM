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
  requirement: { required_quantity: 100, quantity_unit: 'piece', manufacturer: 'Maker', manufacturer_part_number: 'PART' },
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
beforeEach(() => {
  vi.spyOn(api, 'getTask').mockResolvedValue(task as never)
  vi.spyOn(api, 'getCompliance').mockResolvedValue(structuredClone(workspace) as never)
})
test('compliance independently loads before results and requires per-item missing acknowledgement', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(api, 'confirmCompliance').mockResolvedValue({ task_revision: 4 } as never)
  const getResult = vi.spyOn(api, 'getResult')
  mount()
  const results = await screen.findByRole('table', { name: '供应商检查结果' })
  expect(within(results).getByText('Supplier One')).toBeInTheDocument()
  expect(getResult).not.toHaveBeenCalled()
  expect(screen.getByLabelText('制度检查：等待确认')).not.toHaveClass('task-timeline-complete')
  const button = screen.getByRole('button', { name: '确认处理结果并进入决策' })
  expect(button).toBeDisabled()
  await user.click(screen.getByLabelText('全选待补充事项'))
  expect(screen.getByLabelText(/暂不补充/)).toBeChecked()
  await user.click(button)
  await waitFor(() => expect(confirm).toHaveBeenCalledWith('task-1', expect.objectContaining({
    expected_task_revision: 3, expected_assessment_id: 'assessment-1', acknowledged_missing_item_ids: ['missing-1'], acknowledge_no_policy: false,
  }), expect.any(String)))
})
test('evidence submission requires explicit coverage, a source, and preserves material identity', async () => {
  const user = userEvent.setup()
  const save = vi.spyOn(api, 'saveComplianceEvidence').mockResolvedValue({ task_revision: 4 } as never)
  mount()
  await user.click(await screen.findByRole('button', { name: /补充材料/ }))
  expect(screen.getByRole('button', { name: '保存材料并重新检查' })).toBeDisabled()
  await user.type(screen.getByLabelText('材料编号'), 'CERT-1')
  await user.type(screen.getByLabelText('来源引用'), 'https://example.test/cert')
  await user.clear(screen.getByLabelText('材料制造商'))
  await user.type(screen.getByLabelText('材料制造商'), 'Other Maker')
  await user.click(screen.getByLabelText(/已核对材料原文/))
  await user.click(screen.getByRole('button', { name: '保存材料并重新检查' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('task-1', expect.objectContaining({
    expectedTaskRevision: 3, facts: expect.objectContaining({ quote_id: 'quote-1', supplier_id: 'SUP-1',
      manufacturer: 'Other Maker', coverage_confirmed: true, expires_on: null, permanent: false }),
  }), expect.any(String)))
})
test('no policy requires explicit acknowledgement before continuing', async () => {
  const user = userEvent.setup()
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, policy_binding: null,
    assessment: { ...workspace.assessment, policy_enabled: false, missing_item_ids: [], assessments: [] } } as never)
  vi.spyOn(api, 'confirmCompliance').mockResolvedValue({ task_revision: 4 } as never)
  mount()
  expect(await screen.findByRole('button', { name: '确认处理结果并进入决策' })).toBeDisabled()
  await user.click(screen.getByLabelText(/确认本任务不启用制度检查/))
  expect(screen.getByRole('button', { name: '确认处理结果并进入决策' })).toBeEnabled()
})
test('decision entry cannot bypass an unconfirmed compliance stage', async () => {
  mount(<DecisionPage />, 'decision')
  expect(await screen.findByRole('link', { name: '前往制度检查' })).toHaveAttribute('href', '/tasks/task-1/compliance')
})

test('a not-started workspace starts analysis instead of polling forever', async () => {
  const user = userEvent.setup()
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, assessment: null,
    stage: { status: 'NOT_STARTED', confirmed: false, can_confirm: false, can_compare: false } } as never)
  const start = vi.spyOn(api, 'startRun').mockResolvedValue({ task_revision: 3, job_id: 'job-1' } as never)
  mount()
  await user.click(await screen.findByRole('button', { name: '开始制度检查' }))
  await waitFor(() => expect(start).toHaveBeenCalledWith('task-1', 3, expect.any(String)))
})

test('executable policy editor requires human review and keeps money as exact text', async () => {
  const user = userEvent.setup()
  function Editor() {
    const [value, setValue] = useState('{}')
    return <><ExecutableRuleEditor controlCode="AMOUNT_APPROVAL" value={value} onChange={setValue} readonly={false} /><output>{value}</output></>
  }
  render(<Editor />)
  await user.click(screen.getByLabelText('为此条款配置可执行检查'))
  await user.type(screen.getByLabelText('金额门槛'), '10000.01')
  await user.type(screen.getByLabelText('触发后动作'), '财务经理审核')
  await user.type(screen.getByLabelText('审核人'), '李明')
  await user.click(screen.getByLabelText(/已逐项核对规则/))
  const params = JSON.parse(screen.getByRole('status').textContent!)
  expect(params.threshold).toBe('10000.01')
  expect(params.reviewed_by).toBe('李明')
  expect(params.reviewed_at).toMatch(/^20/)
  await user.selectOptions(screen.getByLabelText('执行阶段'), 'AFTER_SELECTION')
  expect(JSON.parse(screen.getByRole('status').textContent!).reviewed_at).toBe('')
})

test('historical assessment keeps frozen material downloads without sending readers to current evidence', async () => {
  const user = userEvent.setup()
  const assessment = { ...workspace.assessment, evidence: [{ evidence_id: 'evidence-old', quote_id: 'quote-1',
    facts: { material_number: 'OLD-CERT' }, version: 1, files: [{ file_id: 'file-old', original_filename: 'original.pdf' }] }] }
  render(<MemoryRouter><ComplianceAssessmentDetails assessment={assessment as never} taskId="task-1" resultId="result-old" historical /></MemoryRouter>)
  await user.click(screen.getByText('本结果的制度核验记录'))
  expect(screen.getByRole('link', { name: 'original.pdf' })).toHaveAttribute('href', '/api/v1/tasks/task-1/compliance/evidence/evidence-old/files/file-old/content?download=true')
  expect(screen.queryByRole('link', { name: '查看当前制度检查与材料' })).not.toBeInTheDocument()
})

test('bound policy summary and separate control columns preserve incomplete multi-clause checks', async () => {
  const data = structuredClone(workspace)
  Object.assign(data.policy_binding, { policy_set_version: '2026.09', category: 'Electronics', region: 'SG' })
  Object.assign(data.plan, { clauses: [{ clause_id: 'rohs-1', title: 'Supplier Assurance Policy', text: 'Policy text' }] })
  data.assessment.assessments[0].checks.push({ ...data.assessment.assessments[0].checks[0], clause_id: 'rohs-2', status: 'PASS' },
    { ...data.assessment.assessments[0].checks[0], clause_id: 'admission-1', control_code: 'APPROVED_SUPPLIER', status: 'PASS' })
  Object.assign(data.assessment, { amount_requirements: [{ quote_id: 'quote-1', execution_stage: 'AFTER_SELECTION', triggered: true, action: 'Manager review' }] })
  vi.mocked(api.getCompliance).mockResolvedValue(data as never)
  mount()
  expect(await screen.findByText(/Supplier Assurance Policy · v2026\.09 · 电子产品采购 · 新加坡/)).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '供应商准入' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'RoHS' })).toBeInTheDocument()
  const row = within(screen.getByRole('table', { name: '供应商检查结果' })).getByRole('row', { name: /Supplier One/ })
  expect(within(row).getByRole('cell', { name: '待复核' })).toBeInTheDocument()
  expect(within(row).getByRole('cell', { name: '通过' })).toBeInTheDocument()
  const amountRow = within(screen.getByRole('table', { name: '金额条件与后续动作' })).getByRole('row', { name: /Supplier One/ })
  expect(within(amountRow).getByText('选择供应商后')).toBeInTheDocument()
  expect(within(amountRow).getByText('选择后将触发')).toBeInTheDocument()
  expect(within(amountRow).getByText('Manager review')).toBeInTheDocument()
})

test('evidence dialog focuses its first field, traps focus and restores focus after Escape', async () => {
  const user = userEvent.setup()
  mount()
  const opener = await screen.findByRole('button', { name: /补充材料/ })
  await user.click(opener)
  const dialog = screen.getByRole('dialog', { name: /补充材料.*Supplier One/ })
  expect(within(dialog).getByLabelText('材料编号')).toHaveFocus()
  const cancel = within(dialog).getByRole('button', { name: '取消' })
  cancel.focus()
  await user.tab()
  expect(within(dialog).getByLabelText('材料编号')).toHaveFocus()
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
  const results = await screen.findByRole('table', { name: '供应商检查结果' })
  expect(within(results).getByText('Supplier 9')).toBeInTheDocument()
  expect(screen.getByText('材料已过期')).toBeVisible()
  expect(screen.getByRole('button', { name: '收起 Supplier 9 检查与材料' })).toHaveAttribute('aria-expanded', 'true')
  expect(document.getElementById('compliance-quote-9-rohs-1')).toHaveFocus()
  expect(screen.getByText('2 / 2')).toBeInTheDocument()
})

test('processed with missing evidence and disabled policy are never labelled as all passed', async () => {
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, stage: { ...workspace.stage,
    status: 'PROCESSED', confirmed: true, can_compare: true, pending_count: 1 } } as never)
  mount()
  expect(await screen.findByLabelText('制度检查：已处理·有待补充')).toBeInTheDocument()
  expect(screen.getByText('1 个检查项')).toBeInTheDocument()
  expect(screen.getByLabelText('制度检查：已处理·有待补充')).toHaveClass('task-timeline-processed')
})

test('processing compliance shows an animated progress indicator', async () => {
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, assessment: null,
    stage: { status: 'PROCESSING', confirmed: false, can_confirm: false, can_compare: false } } as never)
  mount()
  const heading = await screen.findByText('正在进行制度检查')
  const status = heading.closest('[role="status"]')!
  expect(status.querySelector('.compliance-spinner')).toBeInTheDocument()
})

test('confirmed disabled policy remains explicitly not enabled', async () => {
  vi.mocked(api.getCompliance).mockResolvedValue({ ...workspace, stage: { ...workspace.stage,
    status: 'DISABLED', confirmed: true, can_compare: true, pending_count: 0 } } as never)
  mount()
  expect(await screen.findByLabelText('制度检查：未启用')).toHaveClass('task-timeline-processed')
})

test('current assessment links identify the exact supplier and check', async () => {
  const user = userEvent.setup()
  render(<MemoryRouter><ComplianceAssessmentDetails assessment={workspace.assessment as never} taskId="task-1" /></MemoryRouter>)
  await user.click(screen.getByText('本结果的制度核验记录'))
  expect(screen.getByRole('link', { name: '查看此供应商材料' })).toHaveAttribute('href', '/tasks/task-1/compliance#quote=quote-1')
  expect(screen.getByRole('link', { name: '定位此检查' })).toHaveAttribute('href', '/tasks/task-1/compliance#quote=quote-1&check=rohs-1')
})
