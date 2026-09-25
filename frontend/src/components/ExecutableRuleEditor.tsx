const supported = ['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL']
const fields = ['supplier_id', 'manufacturer', 'manufacturer_part_number']
const labels: Record<string, string> = { supplier_id: 'Supplier ID', manufacturer: 'Manufacturer', manufacturer_part_number: 'Manufacturer part number' }
const stages = { BEFORE_RECOMMENDATION: 'Before recommendation', BEFORE_PUBLICATION: 'Before publication', AFTER_SELECTION: 'After supplier selection' }

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
  if (!supported.includes(controlCode)) return <p>This check type cannot be executed automatically. The clause text will be retained for reference only.</p>
  return <fieldset className="compliance-rule-editor" disabled={readonly}>
    <legend>Human-reviewed executable rule</legend>
    <label className="compliance-checkbox"><input type="checkbox" checked={enabled} onChange={(e) => e.target.checked ? configure() : onChange('{}')} />Configure an executable check for this clause</label>
    {!enabled ? <p>Existing parameters are stored as clause metadata and will not be inferred as an executable rule.</p> : <div className="compliance-form-grid">
      {params.control_code !== controlCode && <p className="form-error compliance-wide">The check type has changed. Disable and reconfigure the executable rule.</p>}
      <label className="field">Execution Stage<select value={String(params.execution_stage ?? '')} onChange={(e) => change('execution_stage', e.target.value)}>{Object.entries(stages).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <p>Date basis: current evaluation date (Singapore time)</p>
      {!amount && <div className="compliance-wide"><strong>Evidence Matching Scope</strong>{fields.map((field) => <label className="compliance-checkbox" key={field}><input type="checkbox" checked={Array.isArray(params.matching_fields) && params.matching_fields.includes(field)} disabled={field === 'supplier_id' || controlCode === 'ROHS_COMPLIANCE'} onChange={(e) => change('matching_fields', e.target.checked ? [...(params.matching_fields as string[]), field] : (params.matching_fields as string[]).filter((item) => item !== field))} />{labels[field]}</label>)}</div>}
      {(['missing_outcome', 'expired_outcome', 'mismatch_outcome'] as const).map((key) => <label className="field" key={key}>{({ missing_outcome: 'When evidence is missing', expired_outcome: 'When evidence is expired', mismatch_outcome: 'When evidence scope does not match' })[key]}<select value={String(params[key] ?? '')} onChange={(e) => change(key, e.target.value)}><option value="REVIEW_REQUIRED">Require human review</option><option value="FAIL">Fail the check</option></select></label>)}
      {!amount && <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={params.allow_unspecified_validity === true} onChange={(e) => change('allow_unspecified_validity', e.target.checked)} />The clause explicitly allows evidence without an expiry date; otherwise keep it under review</label>}
      {amount && <>
        <label className="field">Currency<input maxLength={3} value={String(params.currency ?? '')} onChange={(e) => change('currency', e.target.value.toUpperCase())} /></label>
        <label className="field">Monetary basis<input value="Confirmed Total Cost" readOnly /></label>
        <label className="field">Amount threshold<input inputMode="decimal" value={String(params.threshold ?? '')} onChange={(e) => change('threshold', e.target.value)} /></label>
        <label className="field">Trigger Condition<select value={String(params.operator ?? '')} onChange={(e) => change('operator', e.target.value)}><option value="GTE">Greater than or equal to</option><option value="GT">Greater than</option><option value="LTE">Less than or equal to</option><option value="LT">Less than</option></select></label>
        <label className="field compliance-wide">Required Action<input value={String(params.action ?? '')} onChange={(e) => change('action', e.target.value)} /></label>
        <p className="compliance-wide">A triggered action identifies a required step; it does not mean that procurement approval has been granted.</p>
      </>}
      <label className="field">Reviewer<input value={String(params.reviewed_by ?? '')} onChange={(e) => change('reviewed_by', e.target.value)} /></label>
      <label className="compliance-checkbox compliance-wide"><input type="checkbox" checked={Boolean(params.reviewed_at)} onChange={(e) => onChange(JSON.stringify({ ...params, reviewed_at: e.target.checked ? new Date().toISOString() : '' }, null, 2))} />I have reviewed the rule against the clause text and confirm these execution conditions</label>
    </div>}
  </fieldset>
}
