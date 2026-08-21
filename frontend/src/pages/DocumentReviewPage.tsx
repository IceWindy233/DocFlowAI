import { Check, Download, FileCheck2, History, WandSparkles, Workflow, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, postJson } from '../api'
import { Badge, Empty, PageHeader } from '../components/UI'
import { WorkflowDetailDrawer } from '../components/WorkflowDetailDrawer'
import type { DocumentReviewFinding, DocumentReviewRun, DocumentSummary, WorkflowRunSummary } from '../types'

const scopes = [
  ['STRUCTURE', '结构'], ['FORMAT', '格式'], ['FACT', '事实'], ['CITATION', '引用'],
  ['VERSION', '版本'], ['LANGUAGE', '语言'], ['SENSITIVE', '敏感信息'],
]

export function DocumentReviewPage({ userMode = false }: { userMode?: boolean }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [history, setHistory] = useState<DocumentReviewRun[]>([])
  const [selected, setSelected] = useState<DocumentReviewRun | null>(null)
  const [documentId, setDocumentId] = useState('')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [activeScopes, setActiveScopes] = useState(scopes.map(([value]) => value))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [workflowRuns, setWorkflowRuns] = useState<WorkflowRunSummary[] | null>(null)

  async function showWorkflow() {
    if (!selected) return
    setWorkflowRuns(await api<WorkflowRunSummary[]>(`/workflows/targets/REVIEW/${selected.id}`))
  }

  const load = useCallback(async () => {
    const [docs, items] = await Promise.all([
      api<DocumentSummary[]>(userMode ? '/document-reviews/source-documents?limit=200' : '/admin/documents?limit=200'),
      api<DocumentReviewRun[]>('/document-reviews?limit=30'),
    ])
    setDocuments(docs); setHistory(items)
  }, [userMode])
  useEffect(() => { void load().catch((reason) => setError(reason.message)) }, [load])

  async function runReview() {
    setLoading(true); setError('')
    try {
      const response = await postJson<DocumentReviewRun>('/document-reviews', { document_id: documentId || null, title, text, scope: activeScopes })
      setSelected(response); await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '审核失败') }
    finally { setLoading(false) }
  }

  async function resolve(finding: DocumentReviewFinding, action: 'ACCEPT' | 'REJECT') {
    if (!selected) return
    const updated = await postJson<DocumentReviewFinding>(`/document-reviews/${selected.id}/findings/${finding.id}/resolve`, { action, feedback: action === 'ACCEPT' ? '采纳该建议' : '当前场景不适用' })
    setSelected({ ...selected, findings: selected.findings.map((item) => item.id === updated.id ? updated : item) })
  }

  async function applyAccepted() {
    if (!selected) return
    const ids = selected.findings.filter((item) => item.status === 'ACCEPTED').map((item) => item.id)
    setSelected(await postJson(`/document-reviews/${selected.id}/apply`, { accepted_finding_ids: ids }))
    await load()
  }

  const pending = useMemo(() => selected?.findings.filter((item) => item.status === 'PENDING').length ?? 0, [selected])
  return <div className={userMode ? 'user-feature-page user-review-page' : ''}>
    <PageHeader title={userMode ? '公文审核' : '公文审核 Agent'} description={userMode ? '选择知识库公文或粘贴正文，逐条检查并确认修改建议。' : '规则引擎与 DeepSeek 协同审核；每条意见可定位、解释、反馈并生成修订稿。'} />
    {error && <div className="alert danger">{error}</div>}
    <div className="agent-workbench review-workbench">
      <aside className="panel agent-intake">
        <div className="section-head"><div><h2>审核输入</h2><p>可选择知识库文档，或直接粘贴待审核正文。</p></div></div>
        <label className="field"><span>知识库文档（可选）</span><select value={documentId} onChange={(event) => setDocumentId(event.target.value)}><option value="">直接输入文本</option>{documents.map((item) => <option key={item.id} value={item.id}>{item.document_number ?? '无文号'} · {item.title}</option>)}</select></label>
        {!documentId && <><label className="field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：关于实施园区消防设施升级改造的请示" /></label><label className="field"><span>待审核正文</span><textarea rows={17} value={text} onChange={(event) => setText(event.target.value)} placeholder="粘贴请示、函或其他公文正文…" /></label></>}
        <div className="scope-grid">{scopes.map(([value, label]) => <label key={value}><input type="checkbox" checked={activeScopes.includes(value)} onChange={(event) => setActiveScopes((items) => event.target.checked ? [...items, value] : items.filter((item) => item !== value))} />{label}</label>)}</div>
        <button className="button primary wide-button" disabled={loading || (!documentId && text.trim().length < 10)} onClick={runReview}><FileCheck2 size={16} />{loading ? '正在执行审核工作流…' : '开始审核'}</button>
        {!!history.length && <div className="agent-history"><b><History size={14} />历史审核</b>{history.slice(0, 8).map((item) => <button key={item.id} onClick={() => api<DocumentReviewRun>(`/document-reviews/${item.id}`).then(setSelected)}><span>{item.title}</span><small>{item.summary.total ?? 0} 条 · {new Date(item.created_at).toLocaleDateString('zh-CN')}</small></button>)}</div>}
      </aside>
      <main className="panel review-source">
        <div className="section-head"><div><h2>原文与修订稿</h2><p>{selected ? userMode ? selected.title : `${selected.title} · ${selected.model_signature}` : '运行审核后在此查看定位结果'}</p></div><div className="editor-actions">{selected && !userMode && <button className="button" onClick={showWorkflow}><Workflow size={15} />运行详情</button>}{selected?.report_url && <a className="button" href={selected.report_url}><Download size={15} />下载审核报告</a>}</div></div>
        {!selected ? <Empty><FileCheck2 size={28} />请在左侧选择公文并开始审核</Empty> : <div className="review-text"><pre>{selected.input_text}</pre>{selected.revised_text && <><h3>应用建议后的修订稿</h3><pre className="revised">{selected.revised_text}</pre></>}</div>}
      </main>
      <aside className="panel finding-panel">
        <div className="section-head"><div><h2>审核意见</h2><p>{selected ? `待处理 ${pending} · 共 ${selected.findings.length} 条` : '按严重程度排序'}</p></div></div>
        {selected && <div className="review-summary"><span>严重 <b>{selected.summary.critical ?? 0}</b></span><span>主要 <b>{selected.summary.major ?? 0}</b></span><span>次要 <b>{selected.summary.minor ?? 0}</b></span><span>建议 <b>{selected.summary.suggestion ?? 0}</b></span></div>}
        <div className="finding-list">{selected?.findings.map((finding) => <article key={finding.id} className={`finding-${finding.severity.toLowerCase()} ${finding.status.toLowerCase()}`}><header><Badge tone={finding.severity === 'CRITICAL' ? 'warning' : finding.severity === 'MAJOR' ? 'info' : 'neutral'}>{finding.severity}</Badge><code>{finding.category}</code><small>{finding.sources?.join(' + ') || 'RULE'} · {Math.round(finding.confidence * 100)}%</small></header><b>{finding.original_text || '结构级问题'}{finding.location.paragraph ? ` · 第 ${finding.location.paragraph} 段` : ''}</b><p>{finding.reason}</p>{finding.suggested_text && <blockquote>{finding.suggested_text}</blockquote>}{finding.evidence.map((item) => item.preview_url && <a key={item.page_id} href={item.preview_url} target="_blank" rel="noreferrer">证据：{item.title} · P{item.page_number}</a>)}<footer>{finding.status === 'PENDING' ? <><button onClick={() => resolve(finding, 'REJECT')}><X size={14} />不采纳</button><button className="accept" onClick={() => resolve(finding, 'ACCEPT')}><Check size={14} />采纳</button></> : <Badge tone={finding.status === 'ACCEPTED' ? 'success' : 'neutral'}>{finding.status}</Badge>}</footer></article>)}</div>
        {selected && <button className="button primary wide-button" disabled={!selected.findings.some((item) => item.status === 'ACCEPTED')} onClick={applyAccepted}><WandSparkles size={15} />生成修订稿与报告</button>}
      </aside>
    </div>
    {workflowRuns && <WorkflowDetailDrawer runs={workflowRuns} onClose={() => setWorkflowRuns(null)} />}
  </div>
}
