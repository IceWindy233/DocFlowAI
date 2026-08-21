import { AlertOctagon, CheckCircle2, Eye } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, postJson } from '../api'
import { Empty, PageHeader, StatusBadge } from '../components/UI'
import type { ReviewTask } from '../types'

export function ReviewsPage() {
  const [tasks, setTasks] = useState<ReviewTask[]>([])
  const [selected, setSelected] = useState<ReviewTask | null>(null)
  const [reason, setReason] = useState('人工确认当前结果')
  const [error, setError] = useState('')
  const load = () => api<ReviewTask[]>('/admin/review-tasks?status=OPEN&limit=500').then(setTasks).catch((e) => setError(e.message))
  useEffect(() => { void load() }, [])

  async function resolve(action: 'ACCEPT' | 'REJECT' | 'REPARSE') {
    if (!selected) return
    try {
      await postJson(`/admin/review-tasks/${selected.id}/resolve`, { action, reason, corrections: {} })
      setSelected(null); await load()
    } catch (e) { setError(e instanceof Error ? e.message : '处理失败') }
  }

  return <>
    <PageHeader title="人工审核" description="复杂页面、低质量解析和视觉索引失败必须在这里显式处理。" />
    {error && <div className="alert danger">{error}</div>}
    <section className="panel table-panel">
      {tasks.length === 0 ? <Empty><CheckCircle2 size={24} /> 当前没有待审核任务</Empty> : <div className="table-wrap"><table><thead><tr><th>严重级别</th><th>类别</th><th>问题</th><th>关联对象</th><th></th></tr></thead><tbody>
        {tasks.map((task) => <tr key={task.id}><td><StatusBadge status={task.severity} /></td><td><span className="mono">{task.category}</span></td><td><strong>{task.summary}</strong><small className="block muted">{new Date(task.created_at).toLocaleString('zh-CN')}</small></td><td><small className="mono block">{task.document_id ?? task.source_file_id}</small></td><td><button className="icon-button" onClick={() => setSelected(task)}><Eye size={16} /></button></td></tr>)}
      </tbody></table></div>}
    </section>
    {selected && <div className="modal-backdrop"><div className="modal wide">
      <div className="modal-head"><div><AlertOctagon size={20} /><h2>{selected.summary}</h2></div><button onClick={() => setSelected(null)}>×</button></div>
      <div className="detail-grid"><div><span>类别</span><strong>{selected.category}</strong></div><div><span>文档</span><strong className="mono">{selected.document_id ?? '尚未生成'}</strong></div></div>
      <pre className="json-view">{JSON.stringify(selected.details, null, 2)}</pre>
      <label className="field"><span>处理原因</span><textarea value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="modal-actions split"><button className="button danger" onClick={() => resolve('REJECT')}>拒绝结果</button><span /><button className="button" onClick={() => resolve('REPARSE')}>要求重解析</button><button className="button primary" onClick={() => resolve('ACCEPT')}>确认通过</button></div>
    </div></div>}
  </>
}
