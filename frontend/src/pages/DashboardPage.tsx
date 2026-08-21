import { AlertTriangle, ArrowRight, Boxes, Cloud, FileStack, ScanLine } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, PageHeader, StatusBadge } from '../components/UI'
import type { ConfigVersion, Job, ReviewTask } from '../types'

export function DashboardPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [reviews, setReviews] = useState<ReviewTask[]>([])
  const [config, setConfig] = useState<ConfigVersion | null>(null)

  useEffect(() => {
    Promise.all([
      api<Job[]>('/admin/ingestion/jobs?limit=5'),
      api<ReviewTask[]>('/admin/review-tasks?status=OPEN&limit=5'),
      api<ConfigVersion>('/admin/configurations/current'),
    ]).then(([jobData, reviewData, configData]) => {
      setJobs(jobData); setReviews(reviewData); setConfig(configData)
    }).catch(() => setJobs([]))
  }, [])

  if (!jobs) return <Loading />
  const latest = jobs[0]
  const progress = latest?.progress ?? {}
  return (
    <>
      <PageHeader title="运行概览" description="从源文件盘点到多模态索引的全链路状态。" />
      <section className="stats-grid">
        <article className="stat-card"><FileStack /><span>已发现文件</span><strong>{progress.total ?? 0}</strong><small>最近一次任务</small></article>
        <article className="stat-card"><ScanLine /><span>完成处理</span><strong>{progress.completed ?? 0}</strong><small>含明确跳过项</small></article>
        <article className="stat-card"><AlertTriangle /><span>待人工审核</span><strong>{reviews.length}</strong><small>解析与索引异常</small></article>
        <article className="stat-card"><Cloud /><span>云端调用</span><strong>{latest?.cloud_usage.calls ?? 0}</strong><small>当前任务累计</small></article>
      </section>
      <div className="two-column">
        <section className="panel">
          <div className="panel-title"><div><h2>最近任务</h2><p>任务固定使用创建时的配置版本</p></div><Link to="/ingestion">查看全部 <ArrowRight size={15} /></Link></div>
          <div className="list-stack">
            {jobs.length === 0 && <div className="empty-state">尚未创建入库任务</div>}
            {jobs.map((job) => (
              <div className="list-row" key={job.id}>
                <span className="icon-box"><Boxes size={17} /></span>
                <div className="grow"><strong>{job.job_type}</strong><small className="mono">{job.id}</small></div>
                <span className="muted">{job.progress.completed ?? 0}/{job.progress.total ?? 0}</span>
                <StatusBadge status={job.status} />
              </div>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-title"><div><h2>当前运行配置</h2><p>保存后对新任务立即生效</p></div><Link to="/configuration">进入配置中心 <ArrowRight size={15} /></Link></div>
          {config && <div className="config-summary">
            <div><span>版本</span><strong>v{config.version}</strong></div>
            <div><span>模型档案</span><strong>{config.config.models.length}</strong></div>
            <div><span>云端处理</span><strong>{config.config.budget.cloud_processing_allowed ? '已允许' : '已关闭'}</strong></div>
            <div><span>视觉索引</span><strong>{config.config.indexes.visual_enabled ? '强制校验' : '已关闭'}</strong></div>
            <p className="hash mono">{config.content_hash.slice(0, 20)}…</p>
          </div>}
        </section>
      </div>
    </>
  )
}
