import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <section className="empty-state">
      <span>404</span>
      <h1>Page not found</h1>
      <p>This address is not configured or the page has moved.</p>
      <Link className="button button-primary" to="/">Back to workspace</Link>
    </section>
  )
}
