import { Database, FilePlus2, Folder, FolderOpen, Play, Plus, RefreshCcw, X, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, postJson } from '../api'
import { Empty, PageHeader, SelectField, StatusBadge, Toggle } from '../components/UI'
import type { Job } from '../types'

interface NativeDirectorySelection {
  paths: string[]
  cancelled: boolean
}

export function IngestionPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [directoryLoading, setDirectoryLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)
  const [form, setForm] = useState({
    job_type: 'FULL_SCAN', source_roots: [] as string[], inventory_only: true,
    cloud_processing_allowed: false, full_cloud_run_confirmed: false,
  })

  const load = useCallback(async (silent = false) => {
    try {
      setJobs(await api<Job[]>('/admin/ingestion/jobs'))
      setLastUpdatedAt(new Date())
      if (!silent) setError('')
    } catch (e) {
      if (!silent) setError(e instanceof Error ? e.message : '任务状态刷新失败')
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => { void load(true) }, 3000)
    return () => window.clearInterval(timer)
  }, [load])

  function mergeSourceRoots(paths: string[]) {
    setForm((current) => ({
      ...current,
      source_roots: [...new Set([...current.source_roots, ...paths])],
    }))
  }

  async function pickDirectories() {
    setDirectoryLoading(true)
    setError('')
    try {
      const selection = await postJson<NativeDirectorySelection>('/admin/ingestion/source-directories/pick')
      if (!selection.cancelled) mergeSourceRoots(selection.paths)
    } catch (e) {
      setError(e instanceof Error ? e.message : '系统目录选择器打开失败')
    } finally {
      setDirectoryLoading(false)
    }
  }

  function removeSourceRoot(path: string) {
    setForm((current) => ({
      ...current,
      source_roots: current.source_roots.filter((item) => item !== path),
    }))
  }

  async function createJob() {
    setError('')
    try {
      const job = await postJson<Job>('/admin/ingestion/jobs', {
        job_type: form.job_type,
        source_roots: form.source_roots,
        options: {
          inventory_only: form.inventory_only,
          benchmark_only: false,
          cloud_processing_allowed: form.cloud_processing_allowed,
          full_cloud_run_confirmed: form.full_cloud_run_confirmed,
          publish_on_success: false,
          force_reparse: false,
        },
      })
      setJobs((items) => [job, ...items]); setShowCreate(false)
    } catch (e) { setError(e instanceof Error ? e.message : '创建失败') }
  }

  async function action(job: Job, name: 'run' | 'retry' | 'cancel') {
    setBusy(job.id); setError('')
    try {
      await postJson(`/admin/ingestion/jobs/${job.id}/${name}`)
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败') }
    finally { setBusy(null) }
  }

  return (
    <>
      <PageHeader title="入库任务" description="文件盘点、解析、增强和索引任务均固定配置快照并支持断点重试。" actions={<>
        <span className="auto-refresh"><span />每 3 秒自动刷新{lastUpdatedAt && ` · ${lastUpdatedAt.toLocaleTimeString('zh-CN', { hour12: false })}`}</span>
        <button className="icon-button" title="立即刷新" aria-label="立即刷新" onClick={() => void load()}><RefreshCcw size={16} /></button>
        <button className="button primary" onClick={() => setShowCreate(true)}><FilePlus2 size={17} /> 新建任务</button>
      </>} />
      {error && <div className="alert danger">{error}</div>}
      <section className="panel table-panel">
        {jobs.length === 0 ? <Empty>尚未创建任务。建议先执行不调用云端模型的 M0 全量盘点。</Empty> : (
          <div className="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>配置/索引代际</th><th>云端用量</th><th>操作</th></tr></thead><tbody>
            {jobs.map((job) => <tr key={job.id}>
              <td><strong>{job.job_type}</strong><small className="mono block">{job.id}</small><small className="muted block job-source-roots" title={job.source_roots.join('\n')}><Folder size={11} /> {job.source_roots[0]}{job.source_roots.length > 1 && <b> +{job.source_roots.length - 1}</b>}</small></td>
              <td><StatusBadge status={job.status} /></td>
              <td><strong>{job.progress.completed ?? 0}</strong> / {job.progress.total ?? 0}<small className="block muted">失败 {job.progress.failed ?? 0} · 审核 {job.progress.waiting_review ?? 0}</small></td>
              <td><span className="mono block">{job.config_version_id}</span><small className="mono muted">{job.index_generation_id}</small></td>
              <td>{job.cloud_usage.calls ?? 0} 次<small className="block muted">¥ {Number(job.cloud_usage.estimated_cost_cny ?? 0).toFixed(4)}</small></td>
              <td><div className="row-actions">
                {job.status === 'QUEUED' && <button title="启动" disabled={busy === job.id} onClick={() => action(job, 'run')}><Play size={16} /></button>}
                {['FAILED', 'WAITING_REVIEW', 'WAITING_COST_CONFIRMATION'].includes(job.status) && <button title="重试" disabled={busy === job.id} onClick={() => action(job, 'retry')}><RefreshCcw size={16} /></button>}
                {['QUEUED', 'RUNNING', 'WAITING_REVIEW', 'WAITING_COST_CONFIRMATION'].includes(job.status) && <button title="取消" disabled={busy === job.id} onClick={() => action(job, 'cancel')}><XCircle size={16} /></button>}
              </div></td>
            </tr>)}
          </tbody></table></div>
        )}
      </section>
      {showCreate && <div className="modal-backdrop"><div className="modal">
        <div className="modal-head"><div><Database size={20} /><h2>新建入库任务</h2></div><button onClick={() => setShowCreate(false)}>×</button></div>
        <div className="form-grid">
          <SelectField label="任务类型" value={form.job_type} onChange={(e) => setForm({...form, job_type: e.target.value})}>
            <option value="FULL_SCAN">全量扫描</option><option value="INCREMENTAL_SCAN">增量扫描</option>
          </SelectField>
        </div>
        <section className="source-root-field">
          <div className="source-root-head">
            <div><span>数据源目录</span><small>可多次添加；在 Finder 中按住 Command 可一次选择多个目录</small></div>
            <button type="button" className="button" disabled={directoryLoading} onClick={() => void pickDirectories()}><FolderOpen size={15} />{directoryLoading ? '等待选择…' : '从 Finder 选择'}</button>
          </div>
          {form.source_roots.length === 0 ? (
            <button type="button" className="source-root-empty" disabled={directoryLoading} onClick={() => void pickDirectories()}>
              <Plus size={20} /><strong>添加一个或多个数据源目录</strong><small>系统只会保存所选目录路径，不会由浏览器上传文件</small>
            </button>
          ) : (
            <div className="source-root-list">
              {form.source_roots.map((path) => <div key={path}><Folder size={16} /><code title={path}>{path}</code><button type="button" title="移除此目录" aria-label={`移除 ${path}`} onClick={() => removeSourceRoot(path)}><X size={15} /></button></div>)}
            </div>
          )}
        </section>
        <div className="toggle-list">
          <Toggle label="仅执行 M0 盘点" checked={form.inventory_only} onChange={(value) => setForm({...form, inventory_only: value})} hint="不解析正文，也不会调用任何模型" />
          <Toggle label="允许云端处理" checked={form.cloud_processing_allowed} onChange={(value) => setForm({...form, cloud_processing_allowed: value})} hint="仍受配置中心预算限制" />
          {form.job_type === 'FULL_SCAN' && form.cloud_processing_allowed && <Toggle label="已确认全量云端费用" checked={form.full_cloud_run_confirmed} onChange={(value) => setForm({...form, full_cloud_run_confirmed: value})} hint="费用检查点之前请勿勾选" />}
        </div>
        <div className="modal-actions"><button className="button" onClick={() => setShowCreate(false)}>取消</button><button className="button primary" disabled={form.source_roots.length === 0 || directoryLoading} onClick={createJob}>创建排队任务</button></div>
      </div></div>}
    </>
  )
}
