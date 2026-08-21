import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  FileJson2,
  RefreshCcw,
  Replace,
  Save,
  Search,
  Sparkles,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, postJson, putJson } from '../api'
import { Badge, Empty, Loading, PageHeader, SelectField } from '../components/UI'

type GoldenStatus = 'PENDING' | 'ANNOTATED' | 'APPROVED' | 'REJECTED'

interface GoldenExpected {
  text: string
  title: string | null
  document_number: string | null
  document_role: string | null
  version_role: string | null
  page_type: string | null
  numeric_fields: Record<string, string>
  table_data: Record<string, unknown>
  layout_elements: Array<Record<string, unknown>>
  visual_queries: string[]
}

interface GoldenAnnotation {
  status: GoldenStatus
  expected: GoldenExpected
  notes: string
  reviewer: string
  updated_at: string | null
  approved_at: string | null
  rejection_reason: string | null
}

interface GoldenSample {
  id: string
  source_file_id: string
  page_number: number
  category: string
  selection_reason: string
  annotation: GoldenAnnotation
  validation_errors: string[]
  suggested_text?: string
  source: {
    relative_path: string
    file_name: string
    extension: string
    mime_type: string
    sha256: string | null
    page_count: number | null
  }
}

interface GoldenReport {
  total: number
  approved: number
  completion_percent: number
  annotation_status: string
  quality_ready: boolean
  target_counts: Record<string, number>
  actual_counts: Record<string, number>
  status_counts: Partial<Record<GoldenStatus, number>>
  samples: GoldenSample[]
}

interface AnnotationDraft {
  text: string
  title: string
  documentNumber: string
  documentRole: string
  versionRole: string
  pageType: string
  numericFields: string
  tableData: string
  layoutElements: string
  visualQueries: string
  notes: string
  reviewer: string
}

const categoryText: Record<string, string> = {
  NATIVE_DOCX: '原生 DOCX',
  LEGACY_DOC_WPS: 'DOC / WPS',
  SCANNED_DOCUMENT: '扫描件',
  STAMPED_REPLY: '印章 / 批复',
  MEETING_FORM: '会议表单',
  COMPLEX_TABLE: '复杂表格',
  MIXED_CONTENT: '混合图文',
}

const statusText: Record<GoldenStatus, string> = {
  PENDING: '待标注', ANNOTATED: '待审核', APPROVED: '已批准', REJECTED: '已拒绝',
}

function statusTone(status: GoldenStatus) {
  return status === 'APPROVED' ? 'success' : status === 'REJECTED' ? 'danger' : status === 'ANNOTATED' ? 'warning' : 'neutral'
}

function toDraft(sample: GoldenSample): AnnotationDraft {
  const expected = sample.annotation.expected
  return {
    text: expected.text,
    title: expected.title ?? '',
    documentNumber: expected.document_number ?? '',
    documentRole: expected.document_role ?? '',
    versionRole: expected.version_role ?? '',
    pageType: expected.page_type ?? '',
    numericFields: JSON.stringify(expected.numeric_fields, null, 2),
    tableData: JSON.stringify(expected.table_data, null, 2),
    layoutElements: JSON.stringify(expected.layout_elements, null, 2),
    visualQueries: expected.visual_queries.join('\n'),
    notes: sample.annotation.notes,
    reviewer: sample.annotation.reviewer || 'local-admin',
  }
}

function parseJson<T>(value: string, label: string): T {
  try {
    return JSON.parse(value) as T
  } catch {
    throw new Error(`${label}不是合法 JSON`)
  }
}

export function GoldenSetPage() {
  const [report, setReport] = useState<GoldenReport | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<GoldenSample | null>(null)
  const [draft, setDraft] = useState<AnnotationDraft | null>(null)
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: string; text: string } | null>(null)
  const [previewFailed, setPreviewFailed] = useState(false)

  async function loadReport(preferredId?: string) {
    const next = await api<GoldenReport>('/admin/golden-set')
    setReport(next)
    const desired = preferredId || selectedId || next.samples[0]?.id || ''
    if (desired) setSelectedId(desired)
  }

  // The initial report load intentionally runs once; later refreshes are explicit user actions.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void loadReport().catch((e) => setMessage({ tone: 'danger', text: e.message })) }, [])
  useEffect(() => {
    if (!selectedId) return
    setPreviewFailed(false)
    api<GoldenSample>(`/admin/golden-set/${selectedId}`)
      .then((sample) => { setDetail(sample); setDraft(toDraft(sample)) })
      .catch((e) => setMessage({ tone: 'danger', text: e.message }))
  }, [selectedId])

  const filtered = useMemo(() => {
    if (!report) return []
    const keyword = search.trim().toLocaleLowerCase('zh-CN')
    return report.samples.filter((sample) =>
      (!category || sample.category === category)
      && (!status || sample.annotation.status === status)
      && (!keyword || sample.source.relative_path.toLocaleLowerCase('zh-CN').includes(keyword)),
    )
  }, [report, category, status, search])

  const selectedIndex = filtered.findIndex((sample) => sample.id === selectedId)

  function patchDraft(patch: Partial<AnnotationDraft>) {
    setDraft((value) => value ? { ...value, ...patch } : value)
  }

  function annotationPayload() {
    if (!draft) throw new Error('标注表单尚未加载')
    return {
      expected: {
        text: draft.text,
        title: draft.title || null,
        document_number: draft.documentNumber || null,
        document_role: draft.documentRole || null,
        version_role: draft.versionRole || null,
        page_type: draft.pageType || null,
        numeric_fields: parseJson<Record<string, string>>(draft.numericFields, '数字字段'),
        table_data: parseJson<Record<string, unknown>>(draft.tableData, '表格结构'),
        layout_elements: parseJson<Array<Record<string, unknown>>>(draft.layoutElements, '版面元素'),
        visual_queries: draft.visualQueries.split('\n').map((item) => item.trim()).filter(Boolean),
      },
      notes: draft.notes,
      reviewer: draft.reviewer,
    }
  }

  async function save(showSuccess = true) {
    if (!detail) return null
    const saved = await putJson<GoldenSample>(`/admin/golden-set/${detail.id}/annotation`, annotationPayload())
    setDetail(saved); setDraft(toDraft(saved))
    await loadReport(saved.id)
    if (showSuccess) setMessage({ tone: 'success', text: '标注草稿已保存，状态更新为“待审核”。' })
    return saved
  }

  async function approve() {
    if (!detail || !draft) return
    setBusy(true); setMessage(null)
    try {
      await save(false)
      const approved = await postJson<GoldenSample>(`/admin/golden-set/${detail.id}/approve`, { reviewer: draft.reviewer, reason: '' })
      setDetail(approved); setDraft(toDraft(approved)); await loadReport(approved.id)
      setMessage({ tone: 'success', text: '该页面已批准，可计入 Benchmark Gold 数据。' })
    } catch (e) { setMessage({ tone: 'danger', text: e instanceof Error ? e.message : '批准失败' }) }
    finally { setBusy(false) }
  }

  async function rejectOrReplace(action: 'reject' | 'replace') {
    if (!detail || !draft) return
    const reason = window.prompt(action === 'reject' ? '请输入拒绝原因' : '请输入替换原因')
    if (!reason) return
    setBusy(true); setMessage(null)
    try {
      const updated = await postJson<GoldenSample>(`/admin/golden-set/${detail.id}/${action}`, { reviewer: draft.reviewer, reason })
      setDetail(updated); setDraft(toDraft(updated)); await loadReport(updated.id)
      setMessage({ tone: 'success', text: action === 'reject' ? '候选已拒绝。' : '已自动选择同类别的新候选。' })
    } catch (e) { setMessage({ tone: 'danger', text: e instanceof Error ? e.message : '操作失败' }) }
    finally { setBusy(false) }
  }

  async function exportReport() {
    try {
      await postJson('/admin/golden-set/export')
      setMessage({ tone: 'success', text: '数据库标注已同步导出到 data/reports/golden-set.json。' })
    } catch (e) { setMessage({ tone: 'danger', text: e instanceof Error ? e.message : '导出失败' }) }
  }

  if (!report) return <Loading />

  return <>
    <PageHeader title="Golden Set 标注" description="逐页审核候选并建立 Benchmark 的人工标准答案。机器提取文本仅作为参考。" actions={<>
      <span className="version-pill">{report.approved} / {report.total} 已批准</span>
      <button className="button" onClick={() => void exportReport()}><FileJson2 size={16} />同步导出 JSON</button>
    </>} />
    {message && <div className={`alert ${message.tone}`}>{message.text}</div>}
    <div className="golden-stats">
      <article><span>标注进度</span><strong>{report.completion_percent}%</strong><div><i style={{ width: `${report.completion_percent}%` }} /></div></article>
      <article><span>待标注</span><strong>{report.status_counts.PENDING ?? 0}</strong><small>需要填写标准答案</small></article>
      <article><span>待审核</span><strong>{report.status_counts.ANNOTATED ?? 0}</strong><small>草稿已保存</small></article>
      <article><span>已拒绝</span><strong>{report.status_counts.REJECTED ?? 0}</strong><small>可自动替换候选</small></article>
    </div>
    <div className="golden-layout">
      <aside className="panel golden-sidebar">
        <div className="golden-filters">
          <label className="search"><Search size={14} /><input aria-label="搜索候选" placeholder="搜索文件路径" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <SelectField label="类别" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">全部类别</option>{Object.entries(categoryText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectField>
          <SelectField label="状态" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(statusText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectField>
        </div>
        <div className="golden-list-head"><span>{filtered.length} 个候选</span><button title="刷新" onClick={() => void loadReport()}><RefreshCcw size={14} /></button></div>
        <div className="golden-list">
          {filtered.map((sample, index) => <button key={sample.id} className={sample.id === selectedId ? 'active' : ''} onClick={() => setSelectedId(sample.id)}>
            <span className="golden-index">{String(index + 1).padStart(3, '0')}</span>
            <span className="golden-list-main"><strong>{sample.source.file_name}</strong><small>{categoryText[sample.category]} · 第 {sample.page_number} 页</small></span>
            <Badge tone={statusTone(sample.annotation.status)}>{statusText[sample.annotation.status]}</Badge>
          </button>)}
          {filtered.length === 0 && <Empty>没有匹配的候选</Empty>}
        </div>
      </aside>
      <section className="panel golden-workbench">
        {!detail || !draft ? <Loading /> : <>
          <header className="golden-detail-head">
            <div><div className="golden-title-row"><Badge tone="info">{categoryText[detail.category]}</Badge><Badge tone={statusTone(detail.annotation.status)}>{statusText[detail.annotation.status]}</Badge><span>第 {detail.page_number} 页</span></div><h2>{detail.source.file_name}</h2><p>{detail.source.relative_path}</p></div>
            <div className="row-actions"><button title="上一条" disabled={selectedIndex <= 0} onClick={() => setSelectedId(filtered[selectedIndex - 1].id)}><ArrowLeft size={16} /></button><button title="下一条" disabled={selectedIndex < 0 || selectedIndex >= filtered.length - 1} onClick={() => setSelectedId(filtered[selectedIndex + 1].id)}><ArrowRight size={16} /></button></div>
          </header>
          <div className="golden-editor">
            <div className="golden-preview-pane">
              <div className="golden-pane-title"><strong>原始页面</strong><a href={`/api/v1/admin/golden-set/${detail.id}/source`} target="_blank" rel="noreferrer">打开原文件 <ExternalLink size={13} /></a></div>
              <div className="golden-preview-canvas">
                {!previewFailed ? <img src={`/api/v1/admin/golden-set/${detail.id}/preview`} alt={`第 ${detail.page_number} 页预览`} onError={() => setPreviewFailed(true)} /> : <div className="preview-error">页面预览生成失败，可点击“打开原文件”人工查看。</div>}
              </div>
              <div className="machine-hint"><div><Sparkles size={15} /><strong>机器提取参考</strong><span>不能直接作为 Gold，必须逐字核对</span></div><button className="button small" disabled={!detail.suggested_text} onClick={() => patchDraft({ text: detail.suggested_text ?? '' })}>填入标准文本框</button></div>
              <pre className="suggested-text">{detail.suggested_text || '该格式暂无原生文本参考，请根据页面图像人工录入。'}</pre>
            </div>
            <div className="golden-form-pane">
              <div className="golden-pane-title"><strong>人工标准答案</strong><span>带 * 字段用于批准校验</span></div>
              {detail.annotation.rejection_reason && <div className="alert danger">拒绝原因：{detail.annotation.rejection_reason}</div>}
              <label className="field"><span>标准文本 *</span><textarea rows={12} value={draft.text} onChange={(e) => patchDraft({ text: e.target.value })} placeholder="逐字录入并核对正文、数字和标点" /></label>
              <div className="form-grid three golden-fields">
                <label className="field"><span>标题</span><input value={draft.title} onChange={(e) => patchDraft({ title: e.target.value })} /></label>
                <label className="field"><span>文号</span><input value={draft.documentNumber} onChange={(e) => patchDraft({ documentNumber: e.target.value })} /></label>
                <label className="field"><span>文档角色</span><select value={draft.documentRole} onChange={(e) => patchDraft({ documentRole: e.target.value })}><option value="">未标注</option><option value="REQUEST">请示</option><option value="LETTER">函</option><option value="REPLY">批复/回复</option><option value="NOTICE">通知</option><option value="MEETING">会议材料</option><option value="ATTACHMENT">附件</option><option value="UNKNOWN">其他</option></select></label>
                <label className="field"><span>版本角色</span><select value={draft.versionRole} onChange={(e) => patchDraft({ versionRole: e.target.value })}><option value="">未标注</option><option value="DRAFT">草稿</option><option value="REVIEW">送审</option><option value="FORMAL">正式版</option><option value="REPLY">回复件</option><option value="UNKNOWN">未知</option></select></label>
                <label className="field"><span>页面类型</span><select value={draft.pageType} onChange={(e) => patchDraft({ pageType: e.target.value })}><option value="">未标注</option><option value="TEXT">文本</option><option value="SCAN">扫描</option><option value="TABLE">表格</option><option value="MIXED">混合图文</option><option value="STAMPED">印章页</option></select></label>
                <label className="field"><span>标注人</span><input value={draft.reviewer} onChange={(e) => patchDraft({ reviewer: e.target.value })} /></label>
              </div>
              <details className="golden-advanced" open={detail.category === 'COMPLEX_TABLE' || detail.category === 'MIXED_CONTENT'}><summary>结构化 Gold 字段</summary>
                <label className="field"><span>数字字段 JSON</span><textarea rows={4} className="mono" value={draft.numericFields} onChange={(e) => patchDraft({ numericFields: e.target.value })} /></label>
                <label className="field"><span>表格结构 JSON {detail.category === 'COMPLEX_TABLE' && '*'}</span><textarea rows={7} className="mono" value={draft.tableData} onChange={(e) => patchDraft({ tableData: e.target.value })} placeholder={'{"headers": [], "rows": [], "merged_cells": []}'} /></label>
                <label className="field"><span>版面元素 JSON</span><textarea rows={5} className="mono" value={draft.layoutElements} onChange={(e) => patchDraft({ layoutElements: e.target.value })} /></label>
                <label className="field"><span>视觉检索问题 {detail.category === 'MIXED_CONTENT' && '*'}</span><textarea rows={4} value={draft.visualQueries} onChange={(e) => patchDraft({ visualQueries: e.target.value })} placeholder="每行一个问题" /></label>
              </details>
              <label className="field"><span>审核备注</span><textarea rows={3} value={draft.notes} onChange={(e) => patchDraft({ notes: e.target.value })} /></label>
              {detail.validation_errors.length > 0 && <div className="golden-validation"><strong>批准前需要完成</strong>{detail.validation_errors.map((item) => <span key={item}>· {item}</span>)}</div>}
            </div>
          </div>
          <footer className="golden-actions"><div><button className="button danger" disabled={busy} onClick={() => void rejectOrReplace('reject')}><XCircle size={15} />拒绝</button><button className="button" disabled={busy} onClick={() => void rejectOrReplace('replace')}><Replace size={15} />替换候选</button></div><div><button className="button" disabled={busy} onClick={() => void save()}><Save size={15} />保存草稿</button><button className="button primary" disabled={busy} onClick={() => void approve()}><CheckCircle2 size={15} />保存并批准</button></div></footer>
        </>}
      </section>
    </div>
  </>
}
