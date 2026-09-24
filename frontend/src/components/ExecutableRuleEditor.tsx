const supported = ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL']
const fields = ['supplier_id', 'manufacturer', 'manufacturer_part_number']
const labels: Record<string, string> = { supplier_id: '供应商编号', manufacturer: '制造商', manufacturer_part_number: '制造商料号' }
const stages = { BEFORE_RECOMMENDATION: '推荐前', BEFORE_PUBLICATION: '发布前', AFTER_SELECTION: '选择供应商后' }

export function ExecutableRuleEditor({ value, controlCode, onChange, readonly }: {
  value: string; controlCode: string; onChange: (value: string) => void; readonly: boolean
}) {
  let params: Record<string, unknown> = {}
  try { params = JSON.parse(value) } catch { /* Raw editor reports malformed JSON on save. */ }
  if (!params || typeof params !== 'object' || Array.isArray(params)) params = {}
  const enabled = params.version === 'compliance-rule/1.0'
  const amount = controlCode === 'AMOUNT_APPROVAL'
  function change(key: string, next: unknown) { onChange(JSON.stringify({ ...params, [key]: next, reviewed_at: '' }, null, 2)) }
  function configure() {
    onChange(JSON.stringify({ version: 'compliance-rule/1.0', control_code: controlCode,
      reviewed_by: '', reviewed_at: '', matching_fields: amount ? [] : controlCode === 'ROHS_COMPLIANCE' ? fields : ['supplier_id'],
      date_basis: 'EVALUATED_AT', missing_outcome: 'REVIEW_REQUIRED', expired_outcome: 'REVIEW_REQUIRED', mismatch_outcome: 'REVIEW_REQUIRED',
      execution_stage: 'BEFORE_RECOMMENDATION', allow_unspecified_validity: false,
      ...(amount ? { currency: 'SGD', monetary_basis: 'TOTAL_COST', threshold: '', operator: 'GTE', action: '' } : {}),
    }, null, 2))
  }
  if (!supported.includes(controlCode)) return <p>此检查类型不支持自动执行，仅保留条款原文。</p>
  return <fieldset className="compliance-rule-editor" disabled={readonly}>
    <legend>人工审核的执行规则</legend>
    <label className="compliance-checkbox"><input type="checkbox" checked={enabled} onChange={(e) => e.target.checked ? configure() : onChange('{}')} />为此条款配置可执行检查</label>
    {!enabled ? <p>现有参数只作为条款资料保存，不会自动推断为可执行规则。</p> : <div className="compliance-form-grid">
      {params.control_code !== controlCode && <p className="form-error compliance-wide">检查类型已变化，请关闭后重新配置执行规则。</p>}
      <label className="field">执行阶段<select value={String(params.execution_stage ?? '')} onChange={(e) => change('execution_stage', e.target.value)}>{Object.entries(stages).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <p>日期基准：本次评估日期（新加坡时间）</p>
      {!amount && <div className="compliance-wide"><strong>材料匹配范围</strong>{fields.map((field) => <label className="compliance-checkbox" key={field}><input type="checkbox" checked={Array.isArray(params.matching_fields) && params.matching_fields.includes(field)} disabled={field === 'supplier_id' || controlCode === 'ROHS_COMPLIANCE'} onChange={(e) => change('matching_fields', e.target.checked ? [...(params.matching_fields as string[]), field] : (params.matching_fields as string[]).filter((item) => item !== field))} />{labels[field]}</label>)}</div>}
      {(['missing_outcome', 'expired_outcome', 'mismatch_outcome'] as const).map((key) => <label className="field" key={key}>{({ missing_outcome: '材料缺失时', expired_outcome: '材料过期时', mismatch_outcome: '范围不符时' })[key]}<select value={String(params[key] ?? '')} onChange={(e) => change(key, e.target.value)}><option value="REVIEW_REQUIRED">待人工复核</option><option value="FAIL">不符合要求</option></select></label>)}
      {!amount && <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={params.allow_unspecified_validity === true} onChange={(e) => change('allow_unspecified_validity', e.target.checked)} />条款明确允许材料不注明有效期（否则保持待复核）</label>}
      {amount && <>
        <label className="field">币种<input maxLength={3} value={String(params.currency ?? '')} onChange={(e) => change('currency', e.target.value.toUpperCase())} /></label>
        <label className="field">金额口径<input value="已确认总成本" readOnly /></label>
        <label className="field">金额门槛<input inputMode="decimal" value={String(params.threshold ?? '')} onChange={(e) => change('threshold', e.target.value)} /></label>
        <label className="field">触发条件<select value={String(params.operator ?? '')} onChange={(e) => change('operator', e.target.value)}><option value="GTE">大于或等于</option><option value="GT">大于</option><option value="LTE">小于或等于</option><option value="LT">小于</option></select></label>
        <label className="field compliance-wide">触发后动作<input value={String(params.action ?? '')} onChange={(e) => change('action', e.target.value)} /></label>
        <p className="compliance-wide">触发动作表示需要办理的事项，不表示已获得采购批准。</p>
      </>}
      <label className="field">审核人<input value={String(params.reviewed_by ?? '')} onChange={(e) => change('reviewed_by', e.target.value)} /></label>
      <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={Boolean(params.reviewed_at)} onChange={(e) => onChange(JSON.stringify({ ...params, reviewed_at: e.target.checked ? new Date().toISOString() : '' }, null, 2))} />我已逐项核对规则与条款原文，确认以上执行条件</label>
    </div>}
  </fieldset>
}
