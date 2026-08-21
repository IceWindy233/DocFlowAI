import { AlertTriangle, CheckCircle2, Clock3, RefreshCw, Workflow } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Empty, PageHeader } from '../components/UI'
import type { WorkflowRunSummary } from '../types'

const workflowLabels: Record<string, string> = {
  RETRIEVAL_QA: '知识问答',
  DOCUMENT_REVIEW: '公文审核',
  DOCUMENT_DRAFT: '公文撰写规划',
  DOCUMENT_DRAFT_GENERATION: '公文初稿生成',
  DOCUMENT_DRAFT_VERIFICATION: '公文稿件校验',
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function workflowLabel(run: WorkflowRunSummary) {
  return workflowLabels[run.workflow_type] ?? run.workflow_type
}

function shortId(value: unknown) {
  const text = typeof value === 'string' ? value : ''
  return text ? text.slice(-10) : ''
}

function inputPresentation(run: WorkflowRunSummary) {
  const input = run.input ?? {}
  if (run.workflow_type === 'RETRIEVAL_QA') {
    return { label: '输入问题', value: String(input.query ?? '未记录问题') }
  }
  if (run.workflow_type === 'DOCUMENT_REVIEW') {
    const suffix = shortId(input.review_id)
    return { label: '审核对象', value: suffix ? `公文审核任务 · ${suffix}` : '公文审核任务' }
  }
  if (run.workflow_type === 'DOCUMENT_DRAFT') {
    const requirements = record(input.requirements)
    return { label: '撰写事项', value: String(requirements.subject ?? '公文撰写需求规划') }
  }
  if (run.workflow_type === 'DOCUMENT_DRAFT_GENERATION') {
    const instruction = typeof input.instruction === 'string' && input.instruction.trim() ? input.instruction : ''
    const mode = input.mode ? `生成模式：${String(input.mode)}` : '生成完整初稿'
    return { label: '生成要求', value: instruction || mode }
  }
  if (run.workflow_type === 'DOCUMENT_DRAFT_VERIFICATION') {
    const suffix = shortId(input.draft_id)
    return { label: '校验对象', value: suffix ? `公文稿件 · ${suffix}` : '人工编辑稿' }
  }
  return { label: '运行输入', value: workflowLabel(run) }
}

function outputPresentation(run: WorkflowRunSummary) {
  const output = run.output ?? {}
  if (run.workflow_type === 'RETRIEVAL_QA' && output.answer) {
    return { label: '最终答案', value: String(output.answer) }
  }
  if (run.workflow_type === 'DOCUMENT_REVIEW') {
    const summary = record(output.summary)
    if (summary.total !== undefined) {
      return { label: '审核结果', value: `共发现 ${String(summary.total)} 条审核意见，其中严重 ${String(summary.critical ?? 0)} 条、主要 ${String(summary.major ?? 0)} 条、次要 ${String(summary.minor ?? 0)} 条。` }
    }
  }
  if (run.workflow_type === 'DOCUMENT_DRAFT') {
    const missing = Array.isArray(output.missing_fields) ? output.missing_fields : []
    if (missing.length) return { label: '规划结果', value: `仍需补充：${missing.join('、')}` }
    const outline = Array.isArray(output.outline) ? output.outline : []
    return { label: '规划结果', value: outline.length ? `已生成 ${outline.length} 个提纲单元。` : '撰写需求已完成检查。' }
  }
  if (run.workflow_type === 'DOCUMENT_DRAFT_GENERATION') {
    const verification = record(output.verification)
    return { label: '生成结果', value: verification.passed === false ? '初稿已生成，仍有事实或引用需要确认。' : '初稿已生成并完成事实与引用校验。' }
  }
  if (run.workflow_type === 'DOCUMENT_DRAFT_VERIFICATION') {
    return { label: '校验结果', value: '人工编辑稿已保存并完成事实与引用校验。' }
  }
  return null
}

function duration(run: WorkflowRunSummary) {
  if (!run.finished_at) return '执行中'
  const milliseconds = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  return `${(milliseconds / 1000).toFixed(2)} 秒`
}

export function WorkflowRunsPage() {
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([])
  const [selected, setSelected] = useState<WorkflowRunSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const values = await api<WorkflowRunSummary[]>('/workflows/runs?limit=50')
      setRuns(values)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取运行记录失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [load])

  async function inspect(run: WorkflowRunSummary) {
    try {
      setSelected(await api<WorkflowRunSummary>(`/workflows/runs/${run.id}`))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取运行详情失败')
    }
  }

  return <>
    <PageHeader
      title="工作流运行"
      description="查看 LangGraph 节点轨迹、执行耗时、索引代际和最终状态。"
      actions={<button className="button" type="button" onClick={() => void load()} disabled={loading}><RefreshCw size={14} />刷新</button>}
    />
    {error && <div className="alert danger">{error}</div>}
    <div className="workflow-layout">
      <section className="panel workflow-run-list">
        <div className="panel-title"><div><h2>Agent 运行记录</h2><p>共显示 {runs.length} 次最近运行</p></div></div>
        {runs.length === 0 ? <Empty>{loading ? '正在读取…' : '尚无运行记录，请先运行知识问答、公文审核或公文撰写任务。'}</Empty> : runs.map((run) => {
          const presentation = inputPresentation(run)
          return <button key={run.id} type="button" className={selected?.id === run.id ? 'active' : ''} onClick={() => void inspect(run)}>
            <span className={`run-icon ${run.status.toLowerCase()}`}>{run.status === 'SUCCEEDED' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}</span>
            <span className="run-main"><b>{presentation.value}</b><code>{workflowLabel(run)} · {run.id}</code></span>
            <span className="run-meta"><b>{run.status}</b><small>{new Date(run.created_at).toLocaleString('zh-CN')}</small></span>
          </button>
        })}
      </section>
      <section className="panel workflow-run-detail">
        {!selected ? <Empty>选择一条运行记录查看节点轨迹。</Empty> : (() => {
          const input = inputPresentation(selected)
          const output = outputPresentation(selected)
          return <>
          <div className="panel-title"><div><h2>{workflowLabel(selected)}</h2><p>{selected.engine} · {selected.engine_version}</p></div><span className={`badge ${selected.status === 'SUCCEEDED' ? 'success' : 'danger'}`}>{selected.status}</span></div>
          <div className="run-facts">
            <div><span>工作流类型</span><strong>{workflowLabel(selected)}</strong><code>{selected.workflow_type}</code></div>
            <div><span>总耗时</span><strong>{duration(selected)}</strong></div>
            <div><span>节点数</span><strong>{selected.trace.length}</strong></div>
            <div><span>配置版本</span><code>{selected.config_version_id ?? '-'}</code></div>
            <div><span>索引代际</span><code>{selected.index_generation_id ?? '-'}</code></div>
          </div>
          <div className="run-question"><span>{input.label}</span><strong>{input.value}</strong></div>
          <div className="run-timeline">
            {selected.trace.map((step) => <article key={`${step.sequence}-${step.node}`}>
              <span className="timeline-marker"><Workflow size={14} /></span>
              <div><small>NODE {step.sequence}</small><h3>{step.label}</h3><p>{step.summary}</p></div>
              <span className="timeline-duration"><Clock3 size={11} />{step.duration_ms} ms</span>
            </article>)}
          </div>
          {output && <div className="run-output"><span>{output.label}</span><p>{output.value}</p></div>}
        </>
        })()}
      </section>
    </div>
  </>
}
