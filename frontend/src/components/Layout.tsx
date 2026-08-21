import {
  Blocks,
  BookOpenCheck,
  Database,
  FileCheck2,
  FilePenLine,
  FileSearch,
  FlaskConical,
  Gauge,
  Layers3,
  ScanSearch,
  Settings2,
  Workflow,
} from 'lucide-react'
import { Link, NavLink, Outlet } from 'react-router-dom'

const nav = [
  { to: '/admin', label: '运行概览', icon: Gauge },
  { to: '/admin/ingestion', label: '入库任务', icon: Database },
  { to: '/admin/documents', label: '文档中心', icon: FileSearch },
  { to: '/admin/drafts', label: '公文撰写', icon: FilePenLine },
  { to: '/admin/document-review', label: '公文审核', icon: FileCheck2 },
  { to: '/admin/retrieval', label: '知识问答', icon: ScanSearch },
  { to: '/admin/qa-evaluation', label: 'Agent 固定评测', icon: FlaskConical },
  { to: '/admin/workflows', label: '工作流运行', icon: Workflow },
  { to: '/admin/publications', label: '索引发布', icon: Layers3 },
  { to: '/admin/reviews', label: '人工审核', icon: BookOpenCheck },
  { to: '/admin/configuration', label: '配置中心', icon: Settings2 },
]

export function Layout() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Blocks size={21} /></span>
          <span><strong>DocFlow</strong><small>AI 公文底座</small></span>
        </div>
        <nav>
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/admin'}>
              <Icon size={18} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Link to="/">返回用户侧</Link>
        </div>
      </aside>
      <main className="main-panel">
        <header className="topbar">
          <div><span className="status-dot" /> M1 知识底座 · M2–M4 Agent MVP</div>
          <span className="mono">localhost</span>
        </header>
        <div className="page"><Outlet /></div>
      </main>
    </div>
  )
}
