import type { RankingCriterion } from '../api/types'

export const rankingCriterionOptions: ReadonlyArray<{
  value: RankingCriterion
  label: string
  group: string
  history: boolean
}> = [
  { value: 'LOWEST_CONFIRMED_TOTAL_COST', label: '最低已确认总成本', group: '报价', history: false },
  { value: 'FASTEST_CONFIRMED_DELIVERY', label: '最快已确认到货', group: '报价', history: false },
  { value: 'LONGEST_CONFIRMED_PAYMENT_TERM', label: '最长已确认账期', group: '报价', history: false },
  { value: 'HIGHEST_SUPPLIER_PERFORMANCE', label: '综合历史等级最高', group: '历史表现', history: true },
  { value: 'HIGHEST_HISTORICAL_ON_TIME_RATE', label: '历史准时率最高', group: '历史表现', history: true },
  { value: 'LOWEST_HISTORICAL_REJECTED_LINE_RATE', label: '历史拒收订单行率最低', group: '历史表现', history: true },
]

export const rankingCriterionLabel = (value: string | null | undefined) =>
  rankingCriterionOptions.find((option) => option.value === value)?.label ?? value ?? '未设置'
