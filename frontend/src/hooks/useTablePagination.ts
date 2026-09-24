import { useState } from 'react'

export const DEFAULT_TABLE_PAGE_SIZE = 8

export function useTablePagination<T>(items: readonly T[], pageSize = DEFAULT_TABLE_PAGE_SIZE, controlledPage?: number) {
  const [requestedPage, setRequestedPage] = useState(0)
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize))
  const page = Math.min(controlledPage ?? requestedPage, pageCount - 1)
  const start = page * pageSize

  return {
    page,
    pageSize,
    pageCount,
    pageItems: items.slice(start, start + pageSize),
    setPage: setRequestedPage,
  }
}
