import { ArrowRight, Bot, CheckCircle2, Database, FileImage, FileText, GitCompareArrows, Layers3, Quote, ScanSearch, Search, SlidersHorizontal, Workflow } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { api, postJson } from '../api'
import { Empty, PageHeader } from '../components/UI'
import type { RetrievalAnswerResponse, RetrievalOptions, RetrievalResponse } from '../types'

export function RetrievalPage({ userMode = false }: { userMode?: boolean }) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<'hybrid' | 'visual' | 'text'>('hybrid')
  const [response, setResponse] = useState<RetrievalAnswerResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [options, setOptions] = useState<RetrievalOptions | null>(null)
  const [caseId, setCaseId] = useState('')
  const [documentRole, setDocumentRole] = useState('')
  const [versionRole, setVersionRole] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [authoritativeOnly, setAuthoritativeOnly] = useState(false)
  const [debugVisible, setDebugVisible] = useState(!userMode)
  const [comparison, setComparison] = useState<Record<string, RetrievalResponse> | null>(null)
  const [comparing, setComparing] = useState(false)

  useEffect(() => {
    void api<RetrievalOptions>('/retrieval/options').then(setOptions).catch(() => setOptions(null))
  }, [])

  const filters = {
    case_ids: caseId ? [caseId] : [],
    document_roles: documentRole ? [documentRole] : [],
    version_roles: versionRole ? [versionRole] : [],
    date_from: dateFrom || null,
    date_to: dateTo || null,
    authoritative_only: authoritativeOnly,
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const value = query.trim()
    if (value.length < 2) return
    setLoading(true)
    setError('')
    try {
      setResponse(await postJson<RetrievalAnswerResponse>('/retrieval/answer', {
        query: value,
        mode,
        limit: 12,
        evidence_limit: 5,
        debug: true,
        rerank: true,
        ...filters,
      }))
    } catch (reason) {
      setResponse(null)
      setError(reason instanceof Error ? reason.message : '问答失败')
    } finally {
      setLoading(false)
    }
  }

  async function compareModes() {
    const value = query.trim()
    if (value.length < 2) return
    setComparing(true); setError('')
    try {
      const modes = ['text', 'visual', 'hybrid'] as const
      const values = await Promise.all(modes.map((currentMode) => postJson<RetrievalResponse>('/retrieval/search', {
        query: value, mode: currentMode, limit: 5, debug: true, rerank: false, ...filters,
      })))
      setComparison(Object.fromEntries(modes.map((currentMode, index) => [currentMode, values[index]])))
    } catch (reason) { setError(reason instanceof Error ? reason.message : '模式对比失败') }
    finally { setComparing(false) }
  }

  return <div className={userMode ? 'user-feature-page user-qa-page' : ''}>
    <PageHeader title="知识问答" description={userMode ? '用自然语言查询已发布公文，回答中的事实均可追溯到原始页面。' : 'LangGraph 编排问题理解、混合召回、答案生成和逐页引用校验。'} />
    <section className="retrieval-hero panel">
      {!userMode && <div className="retrieval-mode" role="group" aria-label="检索模式">
        <button type="button" className={mode === 'hybrid' ? 'active' : ''} onClick={() => setMode('hybrid')}><Layers3 size={14} />混合检索</button>
        <button type="button" className={mode === 'visual' ? 'active' : ''} onClick={() => setMode('visual')}><ScanSearch size={14} />视觉检索</button>
        <button type="button" className={mode === 'text' ? 'active' : ''} onClick={() => setMode('text')}><FileText size={14} />文本检索</button>
      </div>}
      <form onSubmit={submit} className="retrieval-search">
        <Search size={20} />
        <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：查找包含年度预算执行情况的表格" />
        <button className="button primary" disabled={loading || query.trim().length < 2}>
          {loading ? '正在执行工作流…' : '生成带引用答案'}
        </button>
      </form>
      {options && <div className="retrieval-presets">
        <span>试试：</span>{options.presets.slice(0, 4).map((item) => <button type="button" key={item.question} onClick={() => setQuery(item.question)}>{item.question}</button>)}
      </div>}
      {!userMode && <div className="retrieval-filters">
        <SlidersHorizontal size={15} />
        <select value={caseId} onChange={(event) => setCaseId(event.target.value)}><option value="">全部案件</option>{options?.cases.map((item) => <option key={item.value} value={item.value}>{item.value}（{item.count}）</option>)}</select>
        <select value={documentRole} onChange={(event) => setDocumentRole(event.target.value)}><option value="">全部公文类型</option>{options?.document_roles.map((item) => <option key={item.value} value={item.value}>{item.value}（{item.count}）</option>)}</select>
        <select value={versionRole} onChange={(event) => setVersionRole(event.target.value)}><option value="">全部版本</option>{options?.version_roles.map((item) => <option key={item.value} value={item.value}>{item.value}（{item.count}）</option>)}</select>
        <label>从 <input type="date" value={dateFrom} min={options?.date_range.from ?? undefined} max={options?.date_range.to ?? undefined} onChange={(event) => setDateFrom(event.target.value)} /></label>
        <label>至 <input type="date" value={dateTo} min={options?.date_range.from ?? undefined} max={options?.date_range.to ?? undefined} onChange={(event) => setDateTo(event.target.value)} /></label>
        <label className="filter-check"><input type="checkbox" checked={authoritativeOnly} onChange={(event) => setAuthoritativeOnly(event.target.checked)} />仅权威版本</label>
      </div>}
      {!userMode && <div className="retrieval-tools"><button type="button" className="button small" disabled={comparing || query.trim().length < 2} onClick={compareModes}><GitCompareArrows size={14} />{comparing ? '正在对比…' : '对比三种召回'}</button><button type="button" className="button small" onClick={() => setDebugVisible((value) => !value)}>{debugVisible ? '隐藏检索调试' : '显示检索调试'}</button></div>}
      {!userMode && <div className="retrieval-flow">
        <span><Search size={15} /> 用户问题</span><ArrowRight size={15} />
        <span>{mode === 'visual' ? <ScanSearch size={15} /> : mode === 'text' ? <FileText size={15} /> : <Layers3 size={15} />} {mode === 'hybrid' ? '文本召回 + ColPali' : mode === 'visual' ? 'ColPali 查询向量' : '中文词法召回'}</span><ArrowRight size={15} />
        <span><Database size={15} /> {mode === 'hybrid' ? 'RRF 融合排序' : mode === 'visual' ? 'Qdrant MaxSim' : 'PostgreSQL Chunk'}</span><ArrowRight size={15} />
        <span><Bot size={15} /> 答案与引用校验</span>
      </div>}
    </section>

    {!userMode && comparison && <section className="retrieval-comparison panel"><div className="section-head"><div><h2>同一问题召回对比</h2><p>仅比较检索，不额外调用答案生成模型。</p></div></div><div className="comparison-grid">{(['text','visual','hybrid'] as const).map((key) => { const item = comparison[key]; const top = item?.results[0]; return <article key={key}><b>{key === 'text' ? '文本' : key === 'visual' ? '视觉' : '混合'}</b><strong>{item?.total ?? 0} 条</strong><span>Top 1：{top?.title ?? '无结果'}</span><code>{top?.score.toFixed(4) ?? '-'}</code></article> })}</div></section>}

    {error && <div className="alert warning retrieval-alert"><strong>当前还不能回答：</strong>{error}<small>请确认后端、Qdrant 已启动，并且已有激活的索引发布版本。</small></div>}
    {response?.retrieval.warnings.map((warning) => <div className="alert warning" key={warning}>{warning}</div>)}
    {response?.generation_warning && <div className="alert warning">{response.generation_warning}</div>}

    {response && <section className="answer-panel panel">
      <div className="answer-heading">
        <div className="answer-title"><Bot size={21} /><div><small>DOCFLOW AGENT</small><h2>基于知识库回答</h2></div></div>
        <div className="answer-confidence"><span>置信度</span><strong>{Math.round(response.confidence * 100)}%</strong>{!userMode && <code>{response.generation_model_signature}</code>}</div>
      </div>
      <div className="answer-text">{response.answer}</div>
      <div className="evidence-assessment"><span>检索改写：{response.rewritten_query}</span><span>证据评分 <b>{Math.round(response.evidence_assessment.score * 100)}%</b> · {response.evidence_assessment.reasons.join('、')}</span></div>
      {!userMode && <div className="workflow-trace">
        <div className="workflow-trace-title"><Workflow size={15} /><span>LangGraph 运行轨迹 · 云调用 {response.cloud_usage.calls} 次 · 输入 {response.cloud_usage.input_tokens} / 输出 {response.cloud_usage.output_tokens} Tokens</span><code>{response.workflow.run_id}</code></div>
        <div className="workflow-steps">
          {response.workflow.trace.map((step, index) => <div className="workflow-step" key={`${step.sequence}-${step.node}`}>
            <span className="workflow-status"><CheckCircle2 size={14} /></span>
            <div><b>{step.label}</b><small>{step.summary} · {step.duration_ms} ms</small></div>
            {index < response.workflow.trace.length - 1 && <ArrowRight size={13} />}
          </div>)}
        </div>
      </div>}
      {response.citations.length > 0 && <div className="answer-citations">
        <div className="citation-title"><Quote size={14} />引用证据 · 已通过来源一致性校验</div>
        <div className="citation-grid">
          {response.citations.map((citation) => <a key={citation.id} href={citation.preview_url ?? undefined} target={citation.preview_url ? '_blank' : undefined} rel="noreferrer">
            <b>[{citation.id}] {citation.title || '未识别标题'}</b>
            <span>{citation.document_number ?? '无文号'} · 第 {citation.page_number} 页</span>
            <p>{citation.excerpt}</p>
          </a>)}
        </div>
      </div>}
    </section>}

    {!userMode && response?.retrieval.debug && debugVisible && <section className="retrieval-debug panel">
      <div className="section-head"><div><h2>检索调试面板</h2><p>每个分支独立召回后以 RRF 融合，再由可选 Qwen3 Reranker 重排。</p></div><code>RRF k={response.retrieval.debug.fusion.constant}</code></div>
      <div className="debug-branches">{Object.entries(response.retrieval.debug.branches).map(([key, branch]) => <article key={key}><header><b>{key === 'bm25' ? 'BM25 词法' : key === 'semantic' ? '百炼语义向量' : 'ColPali 视觉'}</b><span>{branch.total} 条</span></header><code>{branch.model_signature ?? (key === 'visual' ? 'ColPali / Qdrant' : '-')}</code>{branch.results.slice(0, 5).map((item) => <div key={item.page_id}><b>#{item.rank}</b><span>{item.title} · P{item.page_number}</span><small>{item.score.toFixed(4)}</small></div>)}</article>)}</div>
      <div className="rerank-state"><b>Reranker：</b>{response.retrieval.debug.reranker.applied ? `${response.retrieval.debug.reranker.model_signature} · ${response.retrieval.debug.reranker.candidate_count} 条候选` : response.retrieval.debug.reranker.configured ? `已配置但本次降级：${response.retrieval.debug.reranker.warning ?? '未返回结果'}` : '未启用，使用 RRF 排序'}</div>
    </section>}

    {!userMode && response && <div className="retrieval-summary">
      找到 <strong>{response.retrieval.total}</strong> 个候选页面
      <span>索引代际 <code>{response.retrieval.context.index_generation_id}</code> · {response.retrieval.context.source}</span>
    </div>}
    {response && response.retrieval.results.length === 0 && <section className="panel"><Empty>没有找到匹配页面，请换一个更具体的描述。</Empty></section>}
    {!userMode && response && response.retrieval.results.length > 0 && <section className="retrieval-results">
      {response.retrieval.results.map((result) => <article className="retrieval-card panel" key={result.page_id}>
        <div className="retrieval-preview">
          {result.preview_url ? <img src={result.preview_url} alt={`${result.title} 第 ${result.page_number} 页`} /> : <FileImage size={30} />}
          <b>#{result.rank}</b>
        </div>
        <div className="retrieval-content">
          <div className="retrieval-score"><span>{result.ranking_algorithm}</span><strong>{result.score.toFixed(3)}</strong></div>
          <small>{result.relative_path ?? '未知来源'}</small>
          <h2>{result.title || '未识别标题'}</h2>
          <p>第 {result.page_number} 页 · {result.page_type} · {result.document_number ?? '无文号'}</p>
          <blockquote>{result.snippet || '该页面依靠视觉特征命中，暂无可用 OCR 文本。'}</blockquote>
          <div className="retrieval-sources">{result.match_sources.map((source) => <span key={source}>{source === 'visual' ? '视觉命中' : source === 'semantic' ? '云向量命中' : 'BM25 命中'}</span>)}</div>
          <small>分支排名 {Object.entries(result.branch_ranks).map(([key, value]) => `${key}#${value}`).join(' · ') || '-'}{result.rrf_score != null ? ` · RRF ${result.rrf_score.toFixed(4)}` : ''}{result.rerank_score != null ? ` · Rerank ${result.rerank_score.toFixed(4)}` : ''}</small>
          <code>{result.model_signature}</code>
        </div>
      </article>)}
    </section>}
  </div>
}
