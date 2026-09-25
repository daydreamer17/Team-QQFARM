import type { RankingCriterion } from '../api/types'

export const rankingCriterionOptions: ReadonlyArray<{
  value: RankingCriterion
  label: string
  group: string
  history: boolean
}> = [
  { value: 'LOWEST_CONFIRMED_TOTAL_COST', label: 'Lowest Confirmed Total Cost', group: 'Quotation', history: false },
  { value: 'FASTEST_CONFIRMED_DELIVERY', label: 'Earliest Confirmed Delivery', group: 'Quotation', history: false },
  { value: 'LONGEST_CONFIRMED_PAYMENT_TERM', label: 'Longest Confirmed Payment Term', group: 'Quotation', history: false },
  { value: 'HIGHEST_SUPPLIER_PERFORMANCE', label: 'Highest Supplier Performance', group: 'Historical Performance', history: true },
  { value: 'HIGHEST_HISTORICAL_ON_TIME_RATE', label: 'Highest Historical On-time Rate', group: 'Historical Performance', history: true },
  { value: 'LOWEST_HISTORICAL_REJECTED_LINE_RATE', label: 'Lowest Historical Rejected Order-line Rate', group: 'Historical Performance', history: true },
]

export const rankingCriterionLabel = (value: string | null | undefined) =>
  rankingCriterionOptions.find((option) => option.value === value)?.label ?? value ?? 'Not set'
