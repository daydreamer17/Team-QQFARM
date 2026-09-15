import { NavLink, Outlet } from 'react-router-dom'

function navClass({ isActive }: { isActive: boolean }) {
  return `nav-link${isActive ? ' nav-link-active' : ''}`
}

export function AppShell() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink className="brand" to="/" aria-label="Supplier Compare 首页">
          <span className="brand-mark" aria-hidden="true">Q</span>
          <span>
            <strong>Supplier Compare</strong>
            <small>采购报价比较工作台</small>
          </span>
        </NavLink>
        <span className="environment-badge">LOCAL</span>
      </header>
      <div className="app-frame">
        <nav className="sidebar" aria-label="主导航">
          <p className="nav-label">工作区</p>
          <NavLink to="/" end className={navClass}>工作台</NavLink>
          <NavLink to="/tasks/new" className={navClass}>新建任务</NavLink>
          <div className="sidebar-note">
            <strong>Week 2</strong>
            <span>先打通任务、上传、审核和比较闭环。</span>
          </div>
        </nav>
        <main className="page-content"><Outlet /></main>
      </div>
    </div>
  )
}
