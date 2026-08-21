import {
  ArchiveRestore,
  CheckCircle2,
  CircleAlert,
  DatabaseZap,
  FileStack,
  RefreshCcw,
  Rocket,
  ScanLine,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, postJson } from '../api'
import { Empty, Loading, PageHeader, StatusBadge } from '../components/UI'
import type { Job, Publication, PublicationValidation } from '../types'

function shortId(value: string) {
  return value.length > 28 ? `${value.slice(0, 16)}…${value.slice(-8)}` : value
}

function displayTime(value: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function ValidationDetails({ validation }: { validation: PublicationValidation }) {
  const { checks } = validation
  return <div className="publication-checks">
    <article><FileStack size={17} /><span>可发布文件</span><strong>{checks.counts.published_ready_sources} / {checks.counts.supported_sources}</strong><small>要求 ≥ {(checks.publish_rate.required * 100).toFixed(0)}%</small></article>
    <article><ScanLine size={17} /><span>视觉索引缺失</span><strong>{checks.visual_ready.missing}</strong><small>{checks.visual_ready.passed ? '页面视觉向量完整' : '禁止发布'}</small></article>
    <article><DatabaseZap size={17} /><span>文本向量未就绪</span><strong>{checks.embedding_ready.missing ?? checks.embedding_ready.failed}</strong><small>失败 {checks.embedding_ready.failed} · 共 {checks.counts.chunks} 个 Chunk</small></article>
    <article><CheckCircle2 size={17} /><span>页面对齐缺失</span><strong>{checks.page_alignment.missing}</strong><small>{checks.counts.documents} 个文档</small></article>
  </div>
}

export function PublicationsPage() {
  const [publications, setPublications] = useState<Publication[] | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [validation, setValidation] = useState<PublicationValidation | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [publicationData, jobData] = await Promise.all([
        api<Publication[]>('/admin/publications'),
        api<Job[]>('/admin/ingestion/jobs?limit=100'),
      ])
      setPublications(publicationData)
      setJobs(jobData)
      setError('')
    } catch (reason) {
      setPublications([])
      setError(reason instanceof Error ? reason.message : '发布状态读取失败')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const active = publications?.find((item) => item.active) ?? null
  const publicationByGeneration = useMemo(
    () => new Map((publications ?? []).map((item) => [item.index_generation_id, item])),
    [publications],
  )
  const candidates = jobs.filter((job) =>
    job.status === 'SUCCEEDED'
    && job.options.inventory_only !== true
    && (job.progress.total ?? 0) > 0
    && (job.progress.failed ?? 0) === 0
    && (job.progress.waiting_review ?? 0) === 0,
  )

  async function validate(job: Job) {
    setBusy(`validate:${job.id}`); setError(''); setMessage(''); setSelectedJobId(job.id)
    try {
      const result = await postJson<PublicationValidation>(`/admin/publications/validate/${job.id}`)
      setValidation(result)
      setMessage(result.passed ? '完整性校验通过，可以安全切换。' : '完整性校验未通过。')
    } catch (reason) {
      setValidation(null)
      setError(reason instanceof Error ? reason.message : '校验失败')
    } finally { setBusy(null) }
  }

  async function publish(job: Job) {
    if (!window.confirm(`确定将索引 ${job.index_generation_id} 切换为当前检索版本吗？`)) return
    setBusy(`publish:${job.id}`); setError(''); setMessage('')
    try {
      const result = await postJson<Publication>(`/admin/publications/publish/${job.id}`)
      setValidation(result.validation)
      setSelectedJobId(job.id)
      setMessage('发布成功，检索请求已切换到新的索引代际。')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '发布失败')
    } finally { setBusy(null) }
  }

  async function activate(item: Publication) {
    if (!window.confirm(`确定重新校验并回切到 ${item.index_generation_id} 吗？`)) return
    setBusy(`activate:${item.id}`); setError(''); setMessage('')
    try {
      const result = await postJson<Publication>(`/admin/publications/${item.id}/activate`)
      setValidation(result.validation)
      setSelectedJobId(null)
      setMessage('历史索引重新校验通过，已完成原子回切。')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '回切失败')
    } finally { setBusy(null) }
  }

  if (publications === null) return <Loading />

  return <>
    <PageHeader title="索引发布" description="校验影子索引并原子切换当前检索版本；历史版本保留，可随时重新校验后回切。" actions={
      <button className="icon-button" title="刷新" aria-label="刷新" onClick={() => void load()}><RefreshCcw size={16} /></button>
    } />
    {error && <div className="alert danger">{error}</div>}
    {message && <div className="alert success">{message}</div>}

    {active ? <section className="active-publication panel">
      <div className="active-publication-head">
        <span className="active-publication-icon"><Rocket size={22} /></span>
        <div><span>当前在线检索版本</span><h2>{shortId(active.index_generation_id)}</h2><code title={active.index_generation_id}>{active.index_generation_id}</code></div>
        <div className="active-publication-meta"><StatusBadge status="PUBLISHED" /><small>切换时间 {displayTime(active.published_at)}</small></div>
      </div>
      <ValidationDetails validation={active.validation} />
    </section> : <section className="panel"><Empty>尚未发布索引，检索会暂时使用最近一次入库结果。</Empty></section>}

    {validation && <section className={`validation-result panel ${validation.passed ? 'passed' : 'failed'}`}>
      <div className="panel-title"><div><h2>{validation.passed ? '校验通过' : '校验未通过'}</h2><p>{selectedJobId ? `任务 ${selectedJobId}` : '历史 Publication'}</p></div>{validation.passed ? <CheckCircle2 /> : <CircleAlert />}</div>
      <ValidationDetails validation={validation} />
    </section>}

    <section className="panel publication-section">
      <div className="panel-title"><div><h2>可发布任务</h2><p>仅显示已成功结束、无失败和无待审核项的 M1 任务</p></div></div>
      {candidates.length === 0 ? <Empty>暂无可发布的入库任务。</Empty> : <div className="table-wrap"><table><thead><tr><th>任务与索引代际</th><th>处理结果</th><th>现有状态</th><th>完成时间</th><th>操作</th></tr></thead><tbody>
        {candidates.map((job) => {
          const linked = publicationByGeneration.get(job.index_generation_id)
          return <tr key={job.id}>
            <td><strong>{job.job_type}</strong><small className="mono block">{job.id}</small><small className="mono muted block" title={job.index_generation_id}>{shortId(job.index_generation_id)}</small></td>
            <td><strong>{job.progress.completed ?? 0} / {job.progress.total ?? 0}</strong><small className="block muted">失败 {job.progress.failed ?? 0} · 审核 {job.progress.waiting_review ?? 0}</small></td>
            <td>{linked?.active ? <StatusBadge status="PUBLISHED" /> : linked ? <span className="badge">历史版本</span> : <span className="badge">尚未发布</span>}</td>
            <td>{displayTime(job.finished_at)}</td>
            <td><div className="publication-actions"><button className="button small" disabled={busy !== null} onClick={() => void validate(job)}><CheckCircle2 size={14} />校验</button><button className="button primary small" disabled={busy !== null || linked?.active} onClick={() => void publish(job)}><Rocket size={14} />{linked ? '重新发布' : '发布'}</button></div></td>
          </tr>
        })}
      </tbody></table></div>}
    </section>

    <section className="panel publication-section">
      <div className="panel-title"><div><h2>Publication 历史</h2><p>每次切换均保留旧索引，不混合不同模型或维度的向量空间</p></div></div>
      {publications.length === 0 ? <Empty>尚无发布记录。</Empty> : <div className="table-wrap"><table><thead><tr><th>Publication</th><th>索引代际</th><th>配置版本</th><th>校验摘要</th><th>发布时间</th><th>操作</th></tr></thead><tbody>
        {publications.map((item) => <tr key={item.id}>
          <td><strong className="mono">{shortId(item.id)}</strong><small className="block">{item.active ? <StatusBadge status="PUBLISHED" /> : <span className="badge">历史版本</span>}</small></td>
          <td><code title={item.index_generation_id}>{shortId(item.index_generation_id)}</code></td>
          <td><code title={item.config_version_id}>{shortId(item.config_version_id)}</code></td>
          <td>{item.validation.passed ? '通过' : '未通过'}<small className="block muted">{item.validation.checks.counts.documents} 文档 · {item.validation.checks.counts.chunks} Chunk</small></td>
          <td>{displayTime(item.published_at)}</td>
          <td>{!item.active && <button className="button small" disabled={busy !== null} onClick={() => void activate(item)}><ArchiveRestore size={14} />重新校验并回切</button>}</td>
        </tr>)}
      </tbody></table></div>}
    </section>
  </>
}
