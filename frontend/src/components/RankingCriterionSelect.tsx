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
      {(allowEmpty || !value) && <option value="">{allowEmpty ? '无' : '请选择排序指标'}</option>}
      {groups.map((group) => (
        <optgroup key={group} label={group}>
          {rankingCriterionOptions.filter((item) => item.group === group).map((item) => (
            <option
              key={item.value}
              value={item.value}
              disabled={item.value === exclude || (item.history && !historyApplicable)}
              title={item.history && !historyApplicable ? '当前任务没有适用的历史数据绑定' : undefined}
            >
              {item.label}{item.history && !historyApplicable ? '（当前范围不可用）' : ''}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  )
}
