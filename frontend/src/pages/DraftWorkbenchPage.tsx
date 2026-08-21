import {
  BookOpen,
  CheckCircle2,
  ClipboardList,
  Download,
  FileOutput,
  History,
  MessageCircle,
  RotateCcw,
  SearchCheck,
  Send,
  Sparkles,
  SquarePen,
  UserRound,
  Workflow,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, postJson, putJson } from '../api'
import { Badge, Empty, PageHeader } from '../components/UI'
import { WorkflowDetailDrawer } from '../components/WorkflowDetailDrawer'
import type { DraftInterpretation, DraftRevision, DraftTask, WorkflowRunSummary } from '../types'

type DraftRequirements = DraftTask['requirements']
type ChatRole = 'assistant' | 'user'

interface ChatMessage {
  id: string
  role: ChatRole
  text: string
  meta?: string
}

const emptyRequirements: DraftRequirements = {
  document_type: 'REQUEST', subject: '', recipient: '', background: '', facts: '',
  requested_action: '', sender: '', date: new Date().toISOString().slice(0, 10), reference_query: '',
}

const fieldLabels: Record<keyof DraftRequirements, string> = {
  document_type: '文种', subject: '事项主题', recipient: '主送单位', background: '背景与依据',
  facts: '关键事实', requested_action: '请示或函请事项', sender: '发文单位', date: '日期',
  reference_query: '参考案例检索词',
}

const suggestionPrompts = [
  '参考以往银行借款请示，帮我写一份新的请示。',
  '把当前文稿的语气改得更正式，保留事实和金额。',
  '重写第二节，突出借款用途和还款来源。',
]

function messageId(prefix: string) { return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}` }

function initialMessages(): ChatMessage[] {
  return [{ id: messageId('welcome'), role: 'assistant', text: '你好，我可以参考历史正式公文，帮你提取事实、补齐缺失信息并生成初稿。你可以直接描述事项，也可以在下方事实清单中精确修改。' }]
}

function missingLabels(requirements: DraftRequirements) {
  const required: (keyof DraftRequirements)[] = ['subject', 'recipient', 'background', 'facts', 'requested_action', 'sender']
  return required.filter((key) => !requirements[key].trim()).map((key) => fieldLabels[key])
}

export function DraftWorkbenchPage({ userMode = false }: { userMode?: boolean }) {
  const [requirements, setRequirements] = useState<DraftRequirements>({ ...emptyRequirements })
  const [history, setHistory] = useState<DraftTask[]>([])
  const [task, setTask] = useState<DraftTask | null>(null)
  const [draftText, setDraftText] = useState('')
  const [chatInput, setChatInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [interpretation, setInterpretation] = useState<DraftInterpretation | null>(null)
  const [revisions, setRevisions] = useState<DraftRevision[]>([])
  const [workflowRuns, setWorkflowRuns] = useState<WorkflowRunSummary[] | null>(null)

  const missing = useMemo(() => missingLabels(requirements), [requirements])
  const addMessage = (role: ChatRole, text: string, meta?: string) => setMessages((current) => [...current, { id: messageId(role), role, text, meta }])
  const loadHistory = () => api<DraftTask[]>('/drafts?limit=30').then(setHistory)
  useEffect(() => { void loadHistory().catch((reason) => setError(reason instanceof Error ? reason.message : '历史任务读取失败')) }, [])

  function patch<K extends keyof DraftRequirements>(key: K, value: DraftRequirements[K]) { setRequirements((current) => ({ ...current, [key]: value })) }

  async function plan(candidate = requirements) {
    const missingFields = missingLabels(candidate)
    if (missingFields.length) {
      addMessage('assistant', `我已经记录当前需求。生成前还需要补充：${missingFields.join('、')}。这些字段用于避免把历史范文中的事实误带入新公文。`)
      return
    }
    setWorking('plan'); setError(''); addMessage('assistant', '正在检索同类正式公文，并根据参考案例规划结构…')
    try {
      const response = await postJson<DraftTask>('/drafts', { requirements: candidate })
      setTask(response); setDraftText(response.draft_text); setRevisions([]); await loadHistory()
      const modeLabel = response.outline.every((item) => item.render_heading === false) ? '连续正文' : '分节正文'
      addMessage('assistant', `已找到 ${response.evidence_bundle.length} 个参考页面，并规划 ${response.outline.length} 个内容单元，将采用${modeLabel}。请在中间区域确认结构。`, '检索完成')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '提纲规划失败'
      setError(message); addMessage('assistant', `检索和规划没有完成：${message}`)
    } finally { setWorking('') }
  }

  async function approveAndGenerate() {
    if (!task) return
    setWorking('generate'); setError('')
    try {
      await putJson(`/drafts/${task.id}/outline`, { outline: task.outline })
      const response = await postJson<DraftTask>(`/drafts/${task.id}/generate`)
      setTask(response); setDraftText(response.draft_text); await loadRevisions(response.id); await loadHistory()
      addMessage('assistant', `初稿已生成，共 ${response.draft_text.length} 字。你可以直接编辑正文，或继续告诉我需要怎样调整。`, '初稿完成')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '初稿生成失败'
      setError(message); addMessage('assistant', `初稿生成没有完成：${message}`)
    } finally { setWorking('') }
  }

  async function saveText() {
    if (!task) return
    setWorking('save'); setError('')
    try {
      const response = await putJson<DraftTask>(`/drafts/${task.id}/text`, { draft_text: draftText })
      setTask(response); await loadRevisions(task.id); addMessage('assistant', '已保存为新版本，并重新执行事实与引用校验。', '保存完成')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '保存失败'
      setError(message); addMessage('assistant', `保存没有完成：${message}`)
    } finally { setWorking('') }
  }

  async function exportDocx() {
    if (!task) return
    setWorking('export'); setError('')
    try {
      const response = await postJson<DraftTask>(`/drafts/${task.id}/export`)
      setTask(response); if (response.export_url) window.open(response.export_url, '_self')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '导出失败'
      setError(message); addMessage('assistant', `当前文稿还不能导出：${message}`)
    } finally { setWorking('') }
  }

  async function loadRevisions(draftId: string) { setRevisions(await api<DraftRevision[]>(`/drafts/${draftId}/revisions`)) }

  async function regenerate(mode: 'FULL' | 'SECTION' | 'PRESERVE_MANUAL', sectionId?: string, instruction?: string) {
    if (!task) return
    setWorking('regenerate'); setError('')
    try {
      const response = await postJson<DraftTask>(`/drafts/${task.id}/regenerate`, { mode, section_id: sectionId ?? null, instruction: instruction?.trim() || null })
      setTask(response); setDraftText(response.draft_text); await loadRevisions(task.id); await loadHistory()
      addMessage('assistant', `已完成${mode === 'SECTION' ? '指定章节' : '本轮'}修改，并重新校验当前文稿。${response.verification.passed ? '校验通过。' : '仍有待确认项，请查看右侧校验结果。'}`, '修改完成')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '重新生成失败'
      setError(message); addMessage('assistant', `这次修改没有完成：${message}`)
    } finally { setWorking('') }
  }

  async function sendMessage(rawMessage = chatInput) {
    const message = rawMessage.trim()
    if (!message || !!working) return
    setChatInput(''); addMessage('user', message)
    if (!task) {
      setWorking('interpret'); setError('')
      try {
        const response = await postJson<DraftInterpretation>('/drafts/interpret', {
          message,
          current_requirements: requirements,
          history: messages.slice(-8).map((item) => ({ role: item.role, content: item.text })),
        })
        setInterpretation(response)
        setRequirements(response.requirements)
        if (response.warning) {
          addMessage('assistant', response.follow_up_question, '需求理解降级')
        } else if (response.missing_fields.length) {
          const confidence = response.confidence ? ` · 置信度 ${Math.round(response.confidence * 100)}%` : ''
          addMessage('assistant', response.follow_up_question, `需求理解完成${confidence}`)
        } else {
          addMessage('assistant', response.follow_up_question, '需求理解完成')
          await plan(response.requirements)
        }
      } catch (reason) {
        const messageText = reason instanceof Error ? reason.message : '需求理解失败'
        setError(messageText)
        addMessage('assistant', `需求理解没有完成：${messageText}`)
      } finally { setWorking('') }
      return
    }
    if (/导出|下载/.test(message) && task.draft_text) {
      addMessage('assistant', '我会先检查校验状态，校验通过后生成 DOCX 文件。'); await exportDocx(); return
    }
    const sectionMatch = message.match(/第\s*(\d+)\s*(?:节|部分|段)/)
    if (sectionMatch && task.outline[Number(sectionMatch[1]) - 1]) {
      const section = task.outline[Number(sectionMatch[1]) - 1]
      addMessage('assistant', `正在根据你的要求重写“${section.title}”…`); await regenerate('SECTION', section.id, message); return
    }
    const mode = /完整重写|重新生成全文|从头生成/.test(message) ? 'FULL' : 'PRESERVE_MANUAL'
    addMessage('assistant', '正在保留已确认事实和其他段落，仅按这条要求调整当前文稿…'); await regenerate(mode, undefined, message)
  }

  async function restore(revision: DraftRevision) {
    if (!task) return
    try {
      const response = await postJson<DraftTask>(`/drafts/${task.id}/revisions/${revision.id}/restore`)
      setTask(response); setDraftText(response.draft_text); await loadRevisions(task.id); addMessage('assistant', `已恢复到 V${revision.revision_number}，并保留当前版本记录。`, '版本恢复')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '恢复版本失败') }
  }

  async function showWorkflow() {
    if (!task) return
    setWorkflowRuns(await api<WorkflowRunSummary[]>(`/workflows/targets/DRAFT/${task.id}`))
  }

  function openHistory(item: DraftTask) {
    void api<DraftTask>(`/drafts/${item.id}`).then((value) => {
      setTask(value); setRequirements(value.requirements); setDraftText(value.draft_text); void loadRevisions(value.id)
      setMessages([{ id: messageId('assistant'), role: 'assistant', text: `已打开“${value.title}”。你可以继续提出修改要求，或者编辑右侧正文。`, meta: `V${value.revision_count} · 历史任务` }])
    }).catch((reason) => setError(reason instanceof Error ? reason.message : '历史任务读取失败'))
  }

  function startNewConversation() {
    setRequirements({ ...emptyRequirements, date: new Date().toISOString().slice(0, 10) })
    setTask(null)
    setDraftText('')
    setChatInput('')
    setMessages(initialMessages())
    setInterpretation(null)
    setRevisions([])
    setWorkflowRuns(null)
    setError('')
  }

  const paragraphMode = Boolean(task?.outline.length && task.outline.every((item) => item.render_heading === false))

  return <div className={userMode ? 'user-feature-page user-draft-page' : ''}>
    <PageHeader title={userMode ? '公文撰写' : '公文撰写 Agent'} description={userMode ? '像和同事沟通一样描述事项，系统会参考历史正式公文生成可编辑、可校验的初稿。' : '对话驱动的案例复用、事实约束、局部改写和 DOCX 导出工作台。'} />
    {error && <div className="alert danger">{error}</div>}
    <div className="agent-workbench draft-workbench conversational-draft-workbench">
      <aside className="panel draft-conversation">
        <div className="conversation-head"><div className="conversation-head-row"><div><span className="eyebrow"><MessageCircle size={13} />对话撰写</span><h2>告诉我你要写什么</h2></div><button className="button new-conversation-button" disabled={!!working} onClick={startNewConversation}><SquarePen size={14} />新建对话</button></div><p>先描述事项，再补充事实；缺少的信息我会主动追问。</p></div>
        <div className="chat-scroll">{messages.map((message) => <article key={message.id} className={`chat-message ${message.role}`}><span className="chat-avatar">{message.role === 'assistant' ? <Sparkles size={13} /> : <UserRound size={13} />}</span><div><p>{message.text}</p>{message.meta && <small>{message.meta}</small>}</div></article>)}{working === 'interpret' && <div className="conversation-progress"><span className="progress-spinner" />正在理解需求、合并事实并检查缺失信息…</div>}{interpretation && interpretation.trace.length > 0 && !working && <div className="conversation-trace" aria-label="需求理解阶段">{interpretation.trace.map((step) => <span key={`${step.sequence}-${step.node}`} className={step.status === 'DEGRADED' ? 'degraded' : ''}>{step.sequence}. {step.label}</span>)}</div>}</div>
        <div className="chat-suggestions"><span>试试这样说</span>{suggestionPrompts.map((prompt) => <button key={prompt} disabled={!!working} onClick={() => { setChatInput(prompt); void sendMessage(prompt) }}>{prompt}</button>)}</div>
        <div className="chat-compose"><textarea value={chatInput} onChange={(event) => setChatInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void sendMessage() } }} placeholder={task ? '例如：把第二节改得更正式，保留所有金额和日期。' : '例如：参考以往银行借款请示，写一份申请500万元流动资金贷款的请示。'} rows={3} /><button className="button primary" disabled={!!working || !chatInput.trim()} onClick={() => void sendMessage()}><Send size={15} />发送</button></div>
        <details className="fact-ledger" open><summary><ClipboardList size={14} />需求事实清单 <span>{missing.length ? `待补 ${missing.length} 项` : '已完整'}</span></summary><p className="fact-ledger-hint">这里的内容是当前公文的事实边界，历史范文不会覆盖它。</p>
          <label className="field"><span>文种</span><select value={requirements.document_type} onChange={(event) => patch('document_type', event.target.value as 'REQUEST' | 'LETTER')}><option value="REQUEST">请示</option><option value="LETTER">函</option></select></label>
          <label className="field"><span>事项主题 *</span><input value={requirements.subject} onChange={(event) => patch('subject', event.target.value)} placeholder="例如：申请流动资金贷款" /></label>
          <label className="field"><span>主送单位 *</span><input value={requirements.recipient} onChange={(event) => patch('recipient', event.target.value)} placeholder="例如：某商业银行示例分行" /></label>
          <label className="field"><span>背景与依据 *</span><textarea rows={3} value={requirements.background} onChange={(event) => patch('background', event.target.value)} /></label>
          <label className="field"><span>关键事实 *</span><textarea rows={4} value={requirements.facts} onChange={(event) => patch('facts', event.target.value)} placeholder="金额、期限、日期、用途、单位等明确事实" /></label>
          <label className="field"><span>{requirements.document_type === 'REQUEST' ? '请示事项' : '函请事项'} *</span><textarea rows={3} value={requirements.requested_action} onChange={(event) => patch('requested_action', event.target.value)} /></label>
          <div className="form-grid"><label className="field"><span>发文单位 *</span><input value={requirements.sender} onChange={(event) => patch('sender', event.target.value)} placeholder="例如：某市示例产业运营有限公司" /></label><label className="field"><span>日期</span><input type="date" value={requirements.date} onChange={(event) => patch('date', event.target.value)} /></label></div>
          <label className="field"><span>参考案例检索词</span><input value={requirements.reference_query} onChange={(event) => patch('reference_query', event.target.value)} placeholder="留空则自动使用主题和背景" /></label>
          <button className="button primary wide-button" disabled={!!working || !requirements.subject.trim()} onClick={() => void plan()}><SearchCheck size={15} />{working === 'plan' ? '正在检索…' : task ? '按当前事实重新检索' : '检索案例并开始撰写'}</button>
        </details>
        {!!history.length && <div className="agent-history"><b><History size={14} />历史撰写</b><div className="agent-history-list">{history.slice(0, 6).map((item) => <button key={item.id} onClick={() => openHistory(item)}><span>{item.title}</span><small>{item.status} · V{item.revision_count} · {new Date(item.created_at).toLocaleDateString('zh-CN')}</small></button>)}</div></div>}
      </aside>

      <div className="draft-right-stack">
      <main className="panel draft-editor">
        <div className="section-head"><div><span className="eyebrow"><FileOutput size={13} />文稿工作区</span><h2>当前文稿</h2><p>{task ? `${task.status} · 版本 V${task.revision_count}` : '生成后可以直接编辑正文，也可以通过对话进行二次修改。'}</p></div><div className="editor-actions">{task && !userMode && <button className="button" onClick={showWorkflow}><Workflow size={15} />运行详情</button>}{task?.export_url && <a className="button" href={task.export_url}><Download size={15} />下载 DOCX</a>}</div></div>
        {!task ? <Empty><BookOpen size={28} /><span>先在左侧描述事项或填写事实清单<br /><small>系统会检索同类正式公文作为参考</small></span></Empty> : <>
          <div className="draft-status-strip"><span className="status-dot" />{task.title}<span>·</span><span>{task.evidence_bundle.length} 个参考页面</span><span>·</span><span>{paragraphMode ? '连续正文' : `${task.outline.length} 个章节`}</span></div>
          {!!task.missing_fields.length && <div className="alert warning">还缺少：{task.missing_fields.join('、')}。请回到左侧事实清单补充后重新检索。</div>}
          {!!task.outline.length && <div className="outline-editor"><div className="outline-heading"><div><span className="eyebrow"><ClipboardList size={13} />结构建议</span><h3>提纲（可直接调整）</h3></div><small>{paragraphMode ? '仅规划内容顺序，正文不显示分节标题' : '确认结构后生成分节初稿'}</small></div>{task.outline.map((item, index) => <div key={item.id} className="outline-row"><b>{index + 1}</b><input value={item.title} onChange={(event) => setTask({ ...task, outline: task.outline.map((current) => current.id === item.id ? { ...current, title: event.target.value } : current) })} />{!!task.draft_text && <button className="button" disabled={!!working} onClick={() => void regenerate('SECTION', item.id, `请重写第${index + 1}${paragraphMode ? '个内容段' : '节'}“${item.title}”，保留已有事实。`)}>{paragraphMode ? '重写本段' : '重写本节'}</button>}</div>)}{!task.draft_text && <button className="button primary" disabled={!!working || !!task.missing_fields.length} onClick={() => void approveAndGenerate()}><Sparkles size={15} />{working === 'generate' ? '正在生成初稿…' : '确认提纲并生成初稿'}</button>}</div>}
          {!!task.draft_text && <div className="draft-text-editor"><div className="editor-subhead"><div><span className="eyebrow">可编辑正文</span><strong>初稿 V{task.revision_count}</strong></div><small>支持直接编辑，或在左侧对话中提出局部修改</small></div><textarea value={draftText} onChange={(event) => setDraftText(event.target.value)} /><div className="editor-actions wrap"><button className="button" disabled={!!working} onClick={() => void saveText()}>保存并校验</button><button className="button" disabled={!!working} onClick={() => void regenerate('PRESERVE_MANUAL')}><RotateCcw size={14} />保留人工内容补全</button><button className="button" disabled={!!working} onClick={() => void regenerate('FULL')}><Sparkles size={14} />完整重新生成</button><button className="button primary" disabled={!!working || !task.verification.passed} title={!task.verification.passed ? '请先处理待确认项并重新校验' : undefined} onClick={() => void exportDocx()}><FileOutput size={15} />{working === 'export' ? '导出中…' : '导出 DOCX'}</button></div></div>}
          {!!revisions.length && <div className="revision-history"><h3>版本历史</h3>{revisions.map((revision) => <article key={revision.id}><div><b>V{revision.revision_number} · {revision.source}</b><small>{new Date(revision.created_at).toLocaleString('zh-CN')} · {revision.model_signature || '本地编辑'}</small><p>{revision.note || `${revision.draft_text.length} 字`}</p></div><button className="button" disabled={!!working} onClick={() => void restore(revision)}>恢复此版本</button></article>)}</div>}
        </>}
      </main>

      <aside className="panel evidence-panel">
        <div className="section-head"><div><span className="eyebrow"><CheckCircle2 size={13} />依据与校验</span><h2>参考与校验</h2><p>参考范文只提供结构和表达，事实以左侧清单为准。</p></div></div>
        {task?.verification && Object.keys(task.verification).length > 0 && <div className={`verification-card ${task.verification.passed ? 'passed' : 'warning'}`}><CheckCircle2 size={20} /><div><b>{task.verification.passed ? '事实与引用校验通过' : '存在待确认项，暂不可导出'}</b><p>事实 {task.verification.fact_count ?? 0} 个 · 引用 {task.verification.citation_count ?? 0} 个</p>{!!task.verification.missing_required_facts?.length && <small>初稿缺少：{task.verification.missing_required_facts.join('、')}</small>}{!!task.verification.invalid_citation_ids?.length && <small>无效引用：{task.verification.invalid_citation_ids.join('、')}</small>}{!!task.verification.unverified_facts?.length && <small>待核实：{task.verification.unverified_facts.join('；')}</small>}{task.verification.warning && <small>降级说明：{task.verification.warning}</small>}</div></div>}
        {!task?.evidence_bundle.length ? <Empty><BookOpen size={23} />完成事项描述后，这里会显示参考范文</Empty> : <div className="draft-evidence-list">{task.evidence_bundle.map((item) => <article key={`${item.page_id}-${item.id}`}><header><Badge tone={item.evidence_type === 'FACT_EVIDENCE' ? 'success' : 'info'}>[{item.id}] {item.evidence_type === 'FACT_EVIDENCE' ? '事实参考' : '结构参考'}</Badge><small>相关度 {Math.round(item.relevance_score * 100)}%</small></header><b>{item.title}</b><span>{item.document_number ?? '无文号'} · 第 {item.page_number} 页</span><small>{item.selection_reason}</small><p>{item.snippet}</p>{item.preview_url && <a href={item.preview_url} target="_blank" rel="noreferrer">查看原始页面</a>}</article>)}</div>}
      </aside>
      </div>
    </div>
    {workflowRuns && <WorkflowDetailDrawer runs={workflowRuns} onClose={() => setWorkflowRuns(null)} />}
  </div>
}
