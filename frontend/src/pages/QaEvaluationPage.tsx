import { CheckCircle2, CloudCog, FlaskConical, RefreshCw, SearchCheck } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, postJson } from '../api'
import { Badge, Empty, Loading, PageHeader, StatusBadge } from '../components/UI'
import type {
  AgentEvaluationCapability,
  AgentEvaluationCatalog,
  AgentEvaluationMode,
  AgentEvaluationResult,
  AgentEvaluationRun,
  FixedAgentSample,
} from '../types'

const capabilityMeta: Record<AgentEvaluationCapability, { label: string; localMode: AgentEvaluationMode; fullMode: AgentEvaluationMode; localLabel: string; fullLabel: string }> = {
  QA: { label: '知识问答', localMode: 'LOCAL_RETRIEVAL', fullMode: 'FULL_QA', localLabel: '运行本地检索', fullLabel: '运行完整问答' },
  REVIEW: { label: '公文审核', localMode: 'LOCAL_RULES', fullMode: 'FULL_REVIEW', localLabel: '运行规则评测', fullLabel: '运行完整审核' },
  DRAFT: { label: '公文撰写', localMode: 'REQUIREMENT_GATE', fullMode: 'FULL_DRAFT', localLabel: '运行需求门禁', fullLabel: '运行完整撰写' },
}

function percent(value: number | string | null | undefined) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-'
}

function metricCards(capability: AgentEvaluationCapability, run: AgentEvaluationRun) {
  const common = [{ label: '样例通过率', value: percent(run.metrics.pass_rate) }]
  if (capability === 'QA') return [
    ...common,
    { label: 'Recall@5', value: percent(run.metrics.recall_at_5) },
    { label: '事实覆盖率', value: percent(run.metrics.fact_coverage) },
    { label: '证据页全覆盖', value: percent(run.metrics.locator_full_coverage_rate) },
    { label: '拒答正确率', value: percent(run.metrics.abstention_accuracy) },
  ]
  if (capability === 'REVIEW') return [
    ...common,
    { label: '问题召回率', value: percent(run.metrics.issue_recall) },
    { label: '对照组误报', value: String(run.metrics.clean_sample_false_positive_count ?? '-') },
    { label: '重复意见', value: String(run.metrics.duplicate_count ?? '-') },
  ]
  return [
    ...common,
    { label: '生成质量', value: percent(run.metrics.generation_quality_pass_rate) },
    { label: '安全门禁', value: percent(run.metrics.safety_gate_pass_rate) },
    { label: '最终校验通过', value: percent(run.metrics.verification_pass_rate) },
  ]
}

function sampleDescription(capability: AgentEvaluationCapability, sample: FixedAgentSample) {
  if (capability === 'QA') return sample.question
  if (capability === 'REVIEW') return sample.text
  const requirements = sample.requirements
  return requirements ? `${requirements.document_type === 'REQUEST' ? '请示' : '函'} · ${requirements.subject} · ${requirements.recipient || '主送单位缺失'}` : ''
}

function resultFacts(result: AgentEvaluationResult) {
  const values: string[] = []
  if (result.recall_at_5 != null) values.push(`Recall@5 ${result.recall_at_5 ? '命中' : '未命中'}`)
  if (result.locator_coverage != null) values.push(`目标证据页 ${percent(result.locator_coverage)}`)
  if (result.fact_coverage != null) values.push(`事实覆盖 ${percent(result.fact_coverage)}`)
  if (result.evidence_coverage != null) values.push(`证据覆盖 ${percent(result.evidence_coverage)}`)
  if (result.citation_coverage != null) values.push(`引用覆盖 ${percent(result.citation_coverage)}`)
  if (result.category_recall != null) values.push(`问题召回 ${percent(result.category_recall)}`)
  if (result.finding_count != null) values.push(`发现 ${result.finding_count} 条`)
  if (result.verification_passed != null) values.push(`事实校验 ${result.verification_passed ? '通过' : '未通过'}`)
  if (result.generation_quality_passed != null) values.push(`生成质量 ${result.generation_quality_passed ? '通过' : '未通过'}`)
  if (result.safety_gate_passed != null) values.push(`安全门禁 ${result.safety_gate_passed ? '通过' : '未通过'}`)
  if (result.repair_attempted != null) values.push(`自动修复 ${result.repair_attempted ? '已执行' : '无需执行'}`)
  if (result.preferred_evidence_hit != null) values.push(`目标证据 ${result.preferred_evidence_hit ? '命中' : '未命中'}`)
  if (result.post_edit_probe_passed != null) values.push(`无依据事实探针 ${result.post_edit_probe_passed ? '拦截成功' : '拦截失败'}`)
  return values.join(' · ')
}

export function QaEvaluationPage() {
  const [catalog, setCatalog] = useState<AgentEvaluationCatalog | null>(null)
  const [runs, setRuns] = useState<AgentEvaluationRun[]>([])
  const [capability, setCapability] = useState<AgentEvaluationCapability>('QA')
  const [selected, setSelected] = useState<Record<AgentEvaluationCapability, string[]>>({ QA: [], REVIEW: [], DRAFT: [] })
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState<AgentEvaluationMode | ''>('')
  const [message, setMessage] = useState('')

  async function load() {
    setLoading(true)
    try {
      const [nextCatalog, history] = await Promise.all([
        api<AgentEvaluationCatalog>('/agent-evaluations/catalog'),
        api<AgentEvaluationRun[]>('/agent-evaluations/runs'),
      ])
      setCatalog(nextCatalog)
      setRuns(history)
      setSelected((value) => ({
        QA: value.QA.length ? value.QA : nextCatalog.qa_samples.map((item) => item.id),
        REVIEW: value.REVIEW.length ? value.REVIEW : nextCatalog.review_samples.map((item) => item.id),
        DRAFT: value.DRAFT.length ? value.DRAFT : nextCatalog.draft_samples.map((item) => item.id),
      }))
      setMessage('')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '固定评测集加载失败')
    } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  const samples = useMemo(() => {
    if (!catalog) return []
    return capability === 'QA' ? catalog.qa_samples : capability === 'REVIEW' ? catalog.review_samples : catalog.draft_samples
  }, [catalog, capability])
  const latest = runs.find((item) => item.capability === capability)
  const meta = capabilityMeta[capability]

  function toggle(sampleId: string) {
    setSelected((value) => ({
      ...value,
      [capability]: value[capability].includes(sampleId)
        ? value[capability].filter((item) => item !== sampleId)
        : [...value[capability], sampleId],
    }))
  }

  async function run(mode: AgentEvaluationMode) {
    const cloudMode = mode.startsWith('FULL_')
    if (cloudMode && !window.confirm(`将运行 ${selected[capability].length} 条${meta.label}完整评测，可能调用百炼和云端对话模型并生成真实工作流记录。确认继续？`)) return
    setWorking(mode); setMessage('')
    try {
      const result = await postJson<AgentEvaluationRun>('/agent-evaluations/runs', {
        capability,
        mode,
        sample_ids: selected[capability],
      })
      setRuns((value) => [result, ...value])
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '评测运行失败')
    } finally { setWorking('') }
  }

  if (loading) return <Loading />
  return <div className="agent-evaluation-page">
    <PageHeader
      title="Agent 固定评测"
      description={`${catalog?.distribution.qa ?? 0} 条问答、${catalog?.distribution.review ?? 0} 条审核与 ${catalog?.distribution.draft ?? 0} 条撰写样例；本地模式零云费用，完整模式验证真实模型链路。`}
      actions={<button className="button" onClick={() => void load()}><RefreshCw size={15} />刷新</button>}
    />
    {message && <div className="alert warning">{message}</div>}
    <section className="agent-eval-tabs panel">
      {(Object.keys(capabilityMeta) as AgentEvaluationCapability[]).map((item) => <button key={item} className={capability === item ? 'active' : ''} onClick={() => setCapability(item)}>{capabilityMeta[item].label}<Badge tone="neutral">{catalog?.distribution[item.toLowerCase() as Lowercase<AgentEvaluationCapability>] ?? 0}</Badge></button>)}
    </section>
    <section className="eval-overview panel">
      <div className="eval-overview-stats">
        <article><span>{meta.label}样例</span><strong>{samples.length}</strong><small>{catalog?.set_id}</small></article>
        <article><span>本次已选择</span><strong>{selected[capability].length}</strong><small>{catalog?.context.index_generation_id}</small></article>
        {capability === 'QA' && <article><span>证据已定位</span><strong>{catalog?.resolution.resolved_evidence_count ?? 0}/{catalog?.resolution.evidence_count ?? 0}</strong><small>{catalog?.context.source}</small></article>}
      </div>
      <div className="eval-overview-actions">
        <p>建议先运行本地基线；完整模式会调用当前配置的云模型。</p>
        <div>
          <button className="button" disabled={!selected[capability].length || !!working} onClick={() => void run(meta.localMode)}><SearchCheck size={15} />{working === meta.localMode ? '运行中…' : meta.localLabel}</button>
          <button className="button primary" disabled={!selected[capability].length || !!working} onClick={() => void run(meta.fullMode)}><CloudCog size={15} />{working === meta.fullMode ? '运行中…' : meta.fullLabel}</button>
        </div>
      </div>
    </section>
    {latest && <section className="evaluation-metrics panel">
      <div className="eval-section-head"><div><h2>最近一次{meta.label}评测</h2><p><StatusBadge status={latest.status} /><span>{latest.mode}</span><span>{new Date(latest.created_at).toLocaleString('zh-CN')}</span><span>云调用 {latest.cloud_usage.calls ?? 0} 次</span>{(latest.cloud_usage.calls ?? 0) > 0 && <span>{latest.cloud_usage.pricing_configured ? `估算费用 ¥${(latest.cloud_usage.estimated_cost_cny ?? 0).toFixed(4)}` : '估算费用：单价未配置'}</span>}</p></div><div className="eval-run-id"><span>运行 ID</span><code>{latest.id}</code></div></div>
      <div className="metric-cards">{metricCards(capability, latest).map((item) => <article key={item.label}><span>{item.label}</span><strong>{item.value}</strong></article>)}</div>
    </section>}
    <section className="evaluation-samples panel">
      <div className="eval-section-head"><div><h2>固定样例</h2><p>样例来源和预期结果已冻结；可按需选择少量样例做冒烟测试。</p></div><div className="sample-actions"><button onClick={() => setSelected((value) => ({ ...value, [capability]: samples.map((item) => item.id) }))}>全选</button><button onClick={() => setSelected((value) => ({ ...value, [capability]: [] }))}>清空</button></div></div>
      {!samples.length && <Empty>当前能力没有固定样例。</Empty>}
      <div className="sample-table eval-sample-list">{samples.map((sample, index) => <article key={sample.id} className={!selected[capability].includes(sample.id) ? 'disabled' : ''}>
        <b>{index + 1}</b>
        <div className="eval-sample-content"><span><Badge tone={sample.expected.behavior === 'ABSTAIN' ? 'warning' : sample.difficulty === 'HARD' ? 'info' : 'neutral'}>{sample.expected.behavior ?? sample.difficulty ?? capability}</Badge>{sample.resolvable === false && <Badge tone="danger">证据未定位</Badge>}<code>{sample.id}</code></span><h3>{sample.name}</h3><p>{sampleDescription(capability, sample)}</p><small>{sample.coverage.join(' · ')}</small></div>
        <div className="sample-actions"><button title={selected[capability].includes(sample.id) ? '取消选择' : '选择样例'} onClick={() => toggle(sample.id)}><CheckCircle2 size={16} />{selected[capability].includes(sample.id) ? '已选' : '选择'}</button></div>
      </article>)}</div>
    </section>
    {!!latest?.results.length && <section className="evaluation-results panel">
      <div className="eval-section-head"><div><h2>逐条结果</h2><p>失败项可以直接定位到召回、事实覆盖、审核规则或事实门禁。</p></div></div>
      <div className="eval-result-list">{latest.results.map((result) => <article key={result.sample_id}><div className="eval-result-meta"><Badge tone={result.passed == null ? 'neutral' : result.passed ? 'success' : 'danger'}>{result.passed == null ? '未计分' : result.passed ? '通过' : '未通过'}</Badge><code>{result.sample_id}</code></div><h3>{result.name}</h3>{resultFacts(result) && <p>{resultFacts(result)}</p>}{result.error && <div className="alert danger">{result.error}</div>}{result.answer && <blockquote>{result.answer}</blockquote>}{result.missing_fields?.length ? <p>缺失字段：{result.missing_fields.join('、')}</p> : null}{result.missing_facts?.length ? <p>缺失事实：{result.missing_facts.join('、')}</p> : null}{result.findings?.map((finding, index) => <blockquote key={`${finding.category}-${index}`}><b>{finding.severity} · {finding.category}</b> {finding.reason}</blockquote>)}</article>)}</div>
    </section>}
    {!latest && <section className="panel"><Empty><FlaskConical size={20} /> 选择样例后先运行本地评测，确认基线稳定，再决定是否运行完整云端链路。</Empty></section>}
  </div>
}
