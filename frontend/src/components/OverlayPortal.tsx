import { type ReactNode, useEffect } from 'react'
import { createPortal } from 'react-dom'

export function OverlayPortal({ children }: { children: ReactNode }) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

  return createPortal(children, document.body)
}
