import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <section className="empty-state">
      <span>404</span>
      <h1>没有找到这个页面</h1>
      <p>当前地址尚未配置，或者页面已经移动。</p>
      <Link className="button button-primary" to="/">返回工作台</Link>
    </section>
  )
}
