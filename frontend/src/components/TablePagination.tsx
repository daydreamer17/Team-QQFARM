interface TablePaginationProps {
  page: number
  pageSize: number
  pageCount: number
  total: number
  onPageChange: (page: number) => void
}

export function TablePagination({ page, pageSize, pageCount, total, onPageChange }: TablePaginationProps) {
  if (total === 0) return null
  const start = page * pageSize + 1
  const end = Math.min(start + pageSize - 1, total)
  return (
    <nav className="table-pagination" aria-label="Table pagination">
      <span>{start}–{end} of {total}</span>
      <div>
        <button className="button button-secondary" type="button" disabled={page === 0} onClick={() => onPageChange(page - 1)}>Previous</button>
        <span>{page + 1} / {pageCount}</span>
        <button className="button button-secondary" type="button" disabled={page + 1 >= pageCount} onClick={() => onPageChange(page + 1)}>Next</button>
      </div>
    </nav>
  )
}
