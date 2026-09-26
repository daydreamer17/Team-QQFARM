import { afterEach, describe, expect, it, vi } from 'vitest'

import { createIdempotencyKey } from '../src/api/client'

describe('API client identifiers', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses randomUUID when the browser provides it', () => {
    const expected = '11111111-2222-4333-8444-555555555555'
    vi.stubGlobal('crypto', {
      randomUUID: vi.fn(() => expected),
      getRandomValues: vi.fn(),
    })

    expect(createIdempotencyKey()).toBe(expected)
  })

  it('creates an RFC 4122 v4 UUID on an insecure HTTP origin', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: vi.fn((values: Uint8Array) => {
        values.fill(0)
        return values
      }),
    })

    expect(createIdempotencyKey()).toBe('00000000-0000-4000-8000-000000000000')
  })
})
