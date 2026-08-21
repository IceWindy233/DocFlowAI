import { FileText, Pencil, Save, Search, Star, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, patchJson } from '../api'
import { Empty, PageHeader, StatusBadge } from '../components/UI'
import type { DocumentSummary } from '../types'

interface DocumentDetail extends DocumentSummary {
  source: { relative_path: string; file_name: string; sha256: string; status: string }
  pages: Array<{ id: string; page_number: number; page_type: string; text: string; image_path: string | null; quality_score: number; visual_status: string; content: Record<string, unknown> }>
  normalized: Record<string, unknown>
}

interface CorrectionDraft {
  title: string
  document_number: string
  document_role: string
  version_role: string
  case_id: string
  authority_score: number
  selected: boolean
  reason: string
}

function correctionDraft(document: DocumentSummary): CorrectionDraft {
  return {
    title: document.title,
    document_number: document.document_number ?? '',
    document_role: document.document_role,
    version_role: document.version_role,
    case_id: document.case_id,
    authority_score: document.authority_score,
    selected: document.selected,
    reason: '人工核对并校正公文元数据',
  }
}

export function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [query, setQuery] = useState('')
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [view, setView] = useState<'preview' | 'text' | 'json' | 'metadata'>('preview')
  const [draft, setDraft] = useState<CorrectionDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const loadDocuments = () => api<DocumentSummary[]>('/admin/documents?limit=500').then(setDocuments)
  useEffect(() => { void loadDocuments() }, [])
  const filtered = documents.filter((item) => `${item.title} ${item.document_number ?? ''} ${item.case_id}`.toLowerCase().includes(query.toLowerCase()))
  async function open(id: string) {
    const next = await api<DocumentDetail>(`/admin/documents/${id}`)
    setDetail(next)
    setDraft(correctionDraft(next))
    setMessage('')
    setView('preview')
  }
  function patchDraft(changes: Partial<CorrectionDraft>) {
    setDraft((current) => current ? { ...current, ...changes } : current)
  }
  async function saveCorrection() {
    if (!detail || !draft || saving) return
    setSaving(true)
    setMessage('')
    try {
      await patchJson<DocumentSummary>(`/admin/documents/${detail.id}`, draft)
      const next = await api<DocumentDetail>(`/admin/documents/${detail.id}`)
      setDetail(next)
      setDraft(correctionDraft(next))
      await loadDocuments()
      setMessage('校正已保存；权威版本选择已按案件和文档角色同步。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }
  return <>
    <PageHeader title="文档中心" description="核对公文角色、案件关系、解析质量和页面视觉索引。" actions={<label className="search"><Search size={16} /><input placeholder="标题、文号或案件…" value={query} onChange={(e) => setQuery(e.target.value)} /></label>} />
    <section className="panel table-panel">
      {filtered.length === 0 ? <Empty>尚无已解析文档</Empty> : <div className="table-wrap"><table><thead><tr><th>文档</th><th>角色 / 版本</th><th>案件</th><th>质量</th><th>解析路线</th></tr></thead><tbody>
        {filtered.map((doc) => <tr key={doc.id} className="clickable" onClick={() => open(doc.id)}><td><div className="doc-cell"><FileText size={18} /><span><strong>{doc.title || '未识别标题'} {doc.selected && <Star className="authority-star" size={13} fill="currentColor" />}</strong><small>{doc.document_number ?? doc.id}</small></span></div></td><td><StatusBadge status={doc.document_role} /> <span className="muted">{doc.version_role}</span></td><td><span className="mono">{doc.case_id}</span></td><td><strong>{(doc.quality_score * 100).toFixed(1)}%</strong></td><td><span className="mono">{doc.parser_route}</span></td></tr>)}
      </tbody></table></div>}
    </section>
    {detail && <div className="drawer-backdrop" onClick={() => setDetail(null)}><aside className="drawer" onClick={(e) => e.stopPropagation()}>
      <div className="drawer-head"><div><small>{detail.source.relative_path}</small><h2>{detail.title} {detail.selected && <Star className="authority-star" size={16} fill="currentColor" />}</h2><span className="mono">{detail.document_number ?? detail.id}</span></div><button onClick={() => setDetail(null)}><X /></button></div>
      <div className="tabs"><button className={view === 'preview' ? 'active' : ''} onClick={() => setView('preview')}>页面预览</button><button className={view === 'text' ? 'active' : ''} onClick={() => setView('text')}>解析文本</button><button className={view === 'json' ? 'active' : ''} onClick={() => setView('json')}>统一 JSON</button><button className={view === 'metadata' ? 'active' : ''} onClick={() => setView('metadata')}><Pencil size={13} /> 元数据校正</button></div>
      <div className="drawer-content">
        {view === 'json' && <pre className="json-view">{JSON.stringify(detail.normalized, null, 2)}</pre>}
        {view === 'text' && detail.pages.map((page) => <article className="page-text" key={page.id}><h3>第 {page.page_number} 页 <StatusBadge status={page.visual_status} /></h3><pre>{page.text}</pre></article>)}
        {view === 'preview' && detail.pages.map((page) => <article className="page-preview" key={page.id}><header><strong>第 {page.page_number} 页 · {page.page_type}</strong><span>质量 {(page.quality_score * 100).toFixed(1)}% · <StatusBadge status={page.visual_status} /></span></header>{page.image_path ? <img src={page.image_path} alt={`第 ${page.page_number} 页`} /> : <div className="text-preview">{page.text.slice(0, 1600) || '无可预览内容'}</div>}</article>)}
        {view === 'metadata' && draft && <section className="metadata-editor panel">
          <div className="metadata-editor-head"><div><Pencil size={18} /><span><strong>发布前元数据校正</strong><small>修改会写入数据库、审计日志和统一 JSON。</small></span></div><StatusBadge status={detail.source.status} /></div>
          {message && <div className={`alert ${message.includes('失败') || message.includes('请求') ? 'danger' : 'success'}`}>{message}</div>}
          <div className="form-grid">
            <label className="field span-two"><span>公文标题</span><input value={draft.title} onChange={(event) => patchDraft({ title: event.target.value })} /></label>
            <label className="field"><span>文号</span><input value={draft.document_number} onChange={(event) => patchDraft({ document_number: event.target.value })} placeholder="例如：示例函〔2027〕12号" /></label>
            <label className="field"><span>案件 ID</span><input value={draft.case_id} onChange={(event) => patchDraft({ case_id: event.target.value })} /></label>
            <label className="field"><span>文档角色</span><select value={draft.document_role} onChange={(event) => patchDraft({ document_role: event.target.value })}><option value="REQUEST">请示</option><option value="LETTER">函</option><option value="REPLY">批复 / 回复</option><option value="NOTICE">通知</option><option value="MEETING">会议材料</option><option value="ATTACHMENT">附件</option><option value="UNKNOWN">未识别</option></select></label>
            <label className="field"><span>版本角色</span><select value={draft.version_role} onChange={(event) => patchDraft({ version_role: event.target.value })}><option value="DRAFT">草稿</option><option value="REVIEW">送审 / 会签</option><option value="FORMAL">正式版</option><option value="REPLY">回复件</option><option value="UNKNOWN">未识别</option></select></label>
            <label className="field"><span>权威评分（0–1）</span><input type="number" min="0" max="1" step="0.05" value={draft.authority_score} onChange={(event) => patchDraft({ authority_score: Number(event.target.value) })} /></label>
            <label className="field"><span>校正原因</span><input value={draft.reason} onChange={(event) => patchDraft({ reason: event.target.value })} /></label>
          </div>
          <label className="authority-toggle"><input type="checkbox" checked={draft.selected} onChange={(event) => patchDraft({ selected: event.target.checked })} /><Star size={17} fill={draft.selected ? 'currentColor' : 'none'} /><span><strong>设为该案件、该角色的权威版本</strong><small>启用后会自动取消同一案件、同一文档角色下的其他权威版本。</small></span></label>
          <div className="metadata-actions"><button className="button" onClick={() => setDraft(correctionDraft(detail))}>恢复当前值</button><button className="button primary" disabled={saving || draft.title.trim().length < 2 || draft.reason.trim().length < 2} onClick={saveCorrection}><Save size={15} />{saving ? '保存中…' : '保存校正'}</button></div>
        </section>}
      </div>
    </aside></div>}
  </>
}
