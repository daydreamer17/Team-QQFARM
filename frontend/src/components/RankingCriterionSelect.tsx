import { rankingCriterionOptions } from '../lib/rankingCriteria'

interface Props {
  value: string
  onChange: (value: string) => void
  exclude?: string
  required?: boolean
  allowEmpty?: boolean
  historyApplicable?: boolean
  id?: string
  invalid?: boolean
}

export function RankingCriterionSelect({
  value,
  onChange,
  exclude,
  required = false,
  allowEmpty = false,
  historyApplicable = true,
  id,
  invalid,
}: Props) {
  const groups = [...new Set(rankingCriterionOptions.map((item) => item.group))]
  return (
    <select id={id} required={required} aria-invalid={invalid} value={value} onChange={(event) => onChange(event.target.value)}>
      {(allowEmpty || !value) && <option value="">{allowEmpty ? 'None' : 'Select a ranking criterion'}</option>}
      {groups.map((group) => (
        <optgroup key={group} label={group}>
          {rankingCriterionOptions.filter((item) => item.group === group).map((item) => (
            <option
              key={item.value}
              value={item.value}
              disabled={item.value === exclude || (item.history && !historyApplicable)}
              title={item.history && !historyApplicable ? 'No applicable historical dataset is bound to this task' : undefined}
            >
              {item.label}{item.history && !historyApplicable ? ' (unavailable for the current scope)' : ''}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  )
}
