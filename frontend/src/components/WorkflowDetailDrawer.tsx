import { Clock3, Workflow, X } from 'lucide-react'
import type { WorkflowRunSummary } from '../types'
import { Badge, Empty } from './UI'

function duration(run: WorkflowRunSummary) {
  if (!run.finished_at) return '执行中'
  const value = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  return `${(value / 1000).toFixed(2)} 秒`
}

export function WorkflowDetailDrawer({ runs, onClose }: { runs: WorkflowRunSummary[]; onClose: () => void }) {
  return <div className="drawer-backdrop" onClick={onClose}>
    <aside className="drawer workflow-drawer" onClick={(event) => event.stopPropagation()}>
      <header className="drawer-head"><div><h2><Workflow size={19} />Agent 运行详情</h2><small>配置、节点、模型、Token 与费用均来自持久化运行记录</small></div><button onClick={onClose}><X /></button></header>
      <div className="drawer-content">{!runs.length ? <Empty>暂无关联工作流记录</Empty> : runs.map((run) => <section key={run.id} className="workflow-run-card">
        <header><div><b>{run.workflow_type}</b><code>{run.id}</code></div><Badge tone={run.status === 'SUCCEEDED' ? 'success' : 'warning'}>{run.status}</Badge></header>
        <div className="run-facts"><div><span>总耗时</span><strong>{duration(run)}</strong></div><div><span>节点数</span><strong>{run.trace.length}</strong></div><div><span>配置版本</span><code>{run.config_version_id ?? '-'}</code></div><div><span>模型</span><code>{run.model_signature ?? '-'}</code></div><div><span>云调用</span><strong>{run.cloud_usage?.calls ?? 0} 次</strong></div><div><span>估算费用</span><strong>{run.cloud_usage?.pricing_configured ? `¥ ${Number(run.cloud_usage?.estimated_cost_cny ?? 0).toFixed(6)}` : '单价未配置'}</strong></div><div><span>输入 Token</span><strong>{run.cloud_usage?.input_tokens ?? 0}</strong></div><div><span>输出 Token</span><strong>{run.cloud_usage?.output_tokens ?? 0}</strong></div></div>
        <div className="run-timeline">{run.trace.map((step) => <article key={`${step.sequence}-${step.node}`}><span className="timeline-marker"><Workflow size={14} /></span><div><small>NODE {step.sequence}</small><h3>{step.label}</h3><p>{step.summary}</p></div><span className="timeline-duration"><Clock3 size={11} />{step.duration_ms} ms</span></article>)}</div>
        {run.error_message && <div className="alert danger">{run.error_message}</div>}
      </section>)}</div>
    </aside>
  </div>
}
