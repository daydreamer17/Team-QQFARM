import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'
import { TablePagination } from '../src/components/TablePagination'
import { useTablePagination } from '../src/hooks/useTablePagination'

function PaginatedFixture() {
  const pagination = useTablePagination(Array.from({ length: 10 }, (_, index) => `记录 ${index + 1}`), 4)

  return (
    <>
      <ul>{pagination.pageItems.map((item) => <li key={item}>{item}</li>)}</ul>
      <TablePagination
        page={pagination.page}
        pageSize={pagination.pageSize}
        pageCount={pagination.pageCount}
        total={10}
        onPageChange={pagination.setPage}
      />
    </>
  )
}

describe('TablePagination', () => {
  test('只展示当前页，并可翻到最后一页', async () => {
    const user = userEvent.setup()
    render(<PaginatedFixture />)

    expect(screen.getByText('记录 1')).toBeInTheDocument()
    expect(screen.queryByText('记录 5')).not.toBeInTheDocument()
    expect(screen.getByText('第 1–4 条，共 10 条')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('记录 5')).toBeInTheDocument()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('记录 10')).toBeInTheDocument()
    expect(screen.getByText('第 9–10 条，共 10 条')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  })
})
