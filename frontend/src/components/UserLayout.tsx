import { Blocks, FileCheck2, FilePenLine, ScanSearch, Settings2 } from 'lucide-react'
import { Link, NavLink, Outlet } from 'react-router-dom'

const userNav = [
  { to: '/qa', label: '知识问答', icon: ScanSearch },
  { to: '/review', label: '公文审核', icon: FileCheck2 },
  { to: '/draft', label: '公文撰写', icon: FilePenLine },
]

export function UserLayout() {
  return <div className="user-shell">
    <header className="user-header">
      <Link to="/" className="user-brand">
        <span className="brand-mark"><Blocks size={21} /></span>
        <span><strong>DocFlow AI</strong><small>智能公文工作台</small></span>
      </Link>
      <nav aria-label="用户功能导航">
        {userNav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to}><Icon size={17} />{label}</NavLink>)}
      </nav>
      <Link className="user-admin-link" to="/admin"><Settings2 size={15} />管理中心</Link>
    </header>
    <main className="user-main"><Outlet /></main>
    <footer className="user-footer"><span>DocFlow AI · 智能公文工作台</span></footer>
  </div>
}
