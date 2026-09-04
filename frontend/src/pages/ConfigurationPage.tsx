import { AlertTriangle, History, RotateCcw, Save, TestTube2, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, postJson, putJson } from '../api'
import { Badge, Field, Loading, PageHeader, SelectField, Toggle } from '../components/UI'
import type { Capability, ConfigVersion, Impact, ModelProfile, RuntimeConfig } from '../types'

type Tab = 'models' | 'routing' | 'parsing' | 'index' | 'execution' | 'history'
type ConfigSection = 'routing' | 'parsing' | 'quality' | 'chunking' | 'indexes' | 'execution' | 'budget' | 'publication' | 'security'
type SectionSetter = <K extends ConfigSection>(section: K, patch: Partial<RuntimeConfig[K]>) => void
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T

const impactText: Record<Impact, string> = {
  HOT: '热更新', REPARSE_REQUIRED: '需要重解析', REINDEX_REQUIRED: '需要重建索引',
}

export function ConfigurationPage() {
  const [current, setCurrent] = useState<ConfigVersion | null>(null)
  const [draft, setDraft] = useState<RuntimeConfig | null>(null)
  const [versions, setVersions] = useState<ConfigVersion[]>([])
  const [tab, setTab] = useState<Tab>('models')
  const [selectedModel, setSelectedModel] = useState('')
  const [message, setMessage] = useState<{tone: string; text: string} | null>(null)
  const [impact, setImpact] = useState<{ impact: Impact; changed_paths: string[]; reasons: string[]; requires_rebuild: boolean } | null>(null)
  const [reason, setReason] = useState('调整 M1 运行配置')
  const [saving, setSaving] = useState(false)

  const load = async () => {
    const [active, history] = await Promise.all([
      api<ConfigVersion>('/admin/configurations/current'),
      api<ConfigVersion[]>('/admin/configurations/versions'),
    ])
    setCurrent(active); setDraft(clone(active.config)); setVersions(history)
    setSelectedModel((value) => value || active.config.models[0]?.profile_id || '')
  }
  useEffect(() => { void load().catch((e) => setMessage({tone: 'danger', text: e.message})) }, [])

  const dirty = useMemo(() => current && draft && JSON.stringify(current.config) !== JSON.stringify(draft), [current, draft])
  if (!current || !draft) return <Loading />

  function patchModel(profileId: string, patch: Partial<ModelProfile>) {
    setDraft((value) => value ? {...value, models: value.models.map((model) => model.profile_id === profileId ? {...model, ...patch} : model)} : value)
  }
  function setSection<K extends ConfigSection>(section: K, patch: Partial<RuntimeConfig[K]>) {
    setDraft((value) => value ? {...value, [section]: {...(value[section] as object), ...patch}} : value)
  }
  async function previewSave() {
    setMessage(null)
    try {
      const result = await postJson<typeof impact>('/admin/configurations/impact-preview', {config: draft})
      setImpact(result)
    } catch (e) { setMessage({tone: 'danger', text: e instanceof Error ? e.message : '配置校验失败'}) }
  }
  async function confirmSave() {
    if (!current) return
    setSaving(true)
    try {
      const saved = await putJson<ConfigVersion>('/admin/configurations/current', {base_version_id: current.id, change_reason: reason, config: draft})
      setCurrent(saved); setDraft(clone(saved.config)); setImpact(null)
      setMessage({tone: 'success', text: `配置 v${saved.version} 已生效，新任务将固定使用该版本。`})
      setVersions(await api('/admin/configurations/versions'))
    } catch (e) { setMessage({tone: 'danger', text: e instanceof Error ? e.message : '保存失败'}) }
    finally { setSaving(false) }
  }
  async function rollback(version: ConfigVersion) {
    if (!window.confirm(`确认回滚至 v${version.version}？系统会创建一个新的活动版本。`)) return
    try { await postJson(`/admin/configurations/versions/${version.id}/rollback`); await load(); setMessage({tone: 'success', text: `已基于 v${version.version} 创建新的活动版本。`}) }
    catch (e) { setMessage({tone: 'danger', text: e instanceof Error ? e.message : '回滚失败'}) }
  }

  const selected = draft.models.find((model) => model.profile_id === selectedModel)
  return <>
    <PageHeader title="配置中心" description="所有保存都会生成不可变快照；运行中任务不受后续修改影响。" actions={<>
      <span className="version-pill">当前 v{current.version}</span>
      <button className="button primary" disabled={!dirty} onClick={previewSave}><Save size={16} /> 保存并生效</button>
    </>} />
    {message && <div className={`alert ${message.tone}`}>{message.text}</div>}
    <div className="config-layout">
      <aside className="config-nav">
        {([['models','模型档案'],['routing','解析路由'],['parsing','解析与质量'],['index','切分与索引'],['execution','执行与预算'],['history','版本历史']] as [Tab,string][]).map(([key,label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}
      </aside>
      <section className="panel config-panel">
        {tab === 'models' && <ModelSettings config={draft} selected={selected} select={setSelectedModel} patch={patchModel} setConfig={setDraft} setMessage={setMessage} />}
        {tab === 'routing' && <RoutingSettings config={draft} setSection={setSection} />}
        {tab === 'parsing' && <ParsingSettings config={draft} setSection={setSection} />}
        {tab === 'index' && <IndexSettings config={draft} setSection={setSection} />}
        {tab === 'execution' && <ExecutionSettings config={draft} setSection={setSection} />}
        {tab === 'history' && <HistorySettings versions={versions} rollback={rollback} />}
      </section>
    </div>
    {impact && <div className="modal-backdrop"><div className="modal wide">
      <div className="modal-head"><div><AlertTriangle size={20} /><h2>确认配置变更</h2></div><button onClick={() => setImpact(null)}>×</button></div>
      <div className={`impact-banner impact-${impact.impact.toLowerCase()}`}><Badge tone={impact.impact === 'HOT' ? 'info' : 'warning'}>{impactText[impact.impact]}</Badge><strong>{impact.reasons.join('；')}</strong></div>
      <div className="changed-paths"><span>影响字段</span><div>{impact.changed_paths.map((path) => <code key={path}>{path}</code>)}</div></div>
      {impact.requires_rebuild && <div className="alert warning">保存后新任务立即使用此配置，但当前发布版保持不变。请在配置生效后手动发起影子重建。</div>}
      <Field label="变更原因" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="modal-actions"><button className="button" onClick={() => setImpact(null)}>取消</button><button className="button primary" disabled={saving || reason.trim().length < 2} onClick={confirmSave}>{saving ? '正在保存…' : '确认保存并生效'}</button></div>
    </div></div>}
  </>
}

function ModelSettings({ config, selected, select, patch, setConfig, setMessage }: {
  config: RuntimeConfig; selected?: ModelProfile; select: (id: string) => void
  patch: (id: string, patch: Partial<ModelProfile>) => void
  setConfig: (value: RuntimeConfig) => void
  setMessage: (value: {tone: string; text: string} | null) => void
}) {
  const [secretState, setSecretState] = useState<{required: boolean; configured: boolean; env_name: string | null} | null>(null)
  const selectedProfileId = selected?.profile_id
  useEffect(() => {
    if (!selectedProfileId) { setSecretState(null); return }
    void api<{required: boolean; configured: boolean; env_name: string | null}>(`/admin/model-profiles/${selectedProfileId}/secret-status`)
      .then(setSecretState)
      .catch(() => setSecretState(null))
  }, [selectedProfileId])
  async function probe(profile: ModelProfile) {
    try {
      const result = await postJson<{success: boolean; latency_ms: number; error_message: string | null}>(`/admin/model-profiles/${profile.profile_id}/probe`, {profile})
      setMessage({tone: result.success ? 'success' : 'danger', text: result.success ? `${profile.display_name} 探测成功（${result.latency_ms} ms）` : result.error_message ?? '模型探测失败'})
    } catch (e) { setMessage({tone: 'danger', text: e instanceof Error ? e.message : '探测失败'}) }
  }
  function addModel() {
    const suffix = Date.now().toString().slice(-6)
    const model: ModelProfile = {
      profile_id: `custom_${suffix}`, display_name: '新模型档案', provider_id: 'custom', adapter_type: 'openai_compatible', capability: 'CHAT_LLM', model_name: 'model-name', workspace_id: null, base_url: null, secret_env_name: null, enabled: false, fallback_profile_id: null, temperature: 0.1, max_output_tokens: 4096, timeout_seconds: 120, max_retries: 2, concurrency: 1, requests_per_minute: 30, embedding_dimension: null, model_signature: `custom:model-${suffix}`, price_input_per_million: 0, price_output_per_million: 0, request_options: {},
    }
    setConfig({...config, models: [...config.models, model]}); select(model.profile_id)
  }
  function removeModel(profile: ModelProfile) {
    const referenced = Object.values(config.routing).includes(profile.profile_id)
    if (referenced) { setMessage({tone: 'danger', text: '该模型仍被解析路由引用，不能删除。'}); return }
    setConfig({...config, models: config.models.filter((item) => item.profile_id !== profile.profile_id)})
    select(config.models.find((item) => item.profile_id !== profile.profile_id)?.profile_id ?? '')
  }
  const workspaceEndpointManaged = selected?.adapter_type === 'dashscope_openai'
    && ['TEXT_EMBEDDING', 'RERANKER'].includes(selected.capability)
  function updateWorkspace(profile: ModelProfile, rawValue: string) {
    const workspaceId = rawValue.trim().toLowerCase()
    patch(profile.profile_id, {
      workspace_id: workspaceId || null,
      base_url: workspaceId
        ? `https://${workspaceId}.cn-beijing.maas.aliyuncs.com/${profile.capability === 'RERANKER' ? 'compatible-api' : 'compatible-mode'}/v1`
        : null,
    })
  }
  return <>
    <div className="section-head"><div><h2>模型档案</h2><p>内置适配器负责能力约束，配置不能加载任意代码。</p></div><button className="button" onClick={addModel}>添加模型</button></div>
    <div className="model-layout">
      <div className="model-list">{config.models.map((model) => <button key={model.profile_id} className={model.profile_id === selected?.profile_id ? 'active' : ''} onClick={() => select(model.profile_id)}><span className={`model-state ${model.enabled ? 'on' : ''}`} /><span><strong>{model.display_name}</strong><small>{model.capability} · {model.provider_id}</small></span></button>)}</div>
      {selected && <div className="model-form">
        <div className="model-form-head"><div><Badge tone={selected.enabled ? 'success' : 'neutral'}>{selected.enabled ? '已启用' : '未启用'}</Badge>{secretState?.required && <Badge tone={secretState.configured ? 'success' : 'warning'}>{secretState.configured ? '密钥已配置' : '密钥未配置'}</Badge>}<code>{selected.profile_id}</code></div><div><button className="icon-button" title="连通性测试" onClick={() => probe(selected)}><TestTube2 size={17} /></button><button className="icon-button danger" title="删除" onClick={() => removeModel(selected)}><Trash2 size={17} /></button></div></div>
        <div className="form-grid three">
          <Field label="显示名称" value={selected.display_name} onChange={(e) => patch(selected.profile_id, {display_name: e.target.value})} />
          <Field label="供应商 ID" value={selected.provider_id} onChange={(e) => patch(selected.profile_id, {provider_id: e.target.value})} />
          <SelectField label="协议适配器" value={selected.adapter_type} onChange={(e) => patch(selected.profile_id, {adapter_type: e.target.value})}>
            <option value="openai_compatible">OpenAI 兼容</option>
            <option value="dashscope_openai">百炼 OpenAI 兼容</option>
            <option value="local_transformers">本地 Transformers</option>
            <option value="rapidocr">RapidOCR</option>
            <option value="tesseract">Tesseract</option>
            <option value="docling">Docling</option>
            <option value="libreoffice">LibreOffice</option>
          </SelectField>
          <SelectField label="能力" value={selected.capability} onChange={(e) => patch(selected.profile_id, {capability: e.target.value as Capability})}>{['VISION_LM','TEXT_EMBEDDING','VISUAL_RETRIEVAL','OCR','STRUCTURE_PARSER','CHAT_LLM','RERANKER'].map((item) => <option key={item}>{item}</option>)}</SelectField>
          <Field label="模型名称" value={selected.model_name} onChange={(e) => patch(selected.profile_id, {model_name: e.target.value})} />
          <Field label="模型签名" value={selected.model_signature} onChange={(e) => patch(selected.profile_id, {model_signature: e.target.value})} />
          <Field label="密钥环境变量" value={selected.secret_env_name ?? ''} onChange={(e) => patch(selected.profile_id, {secret_env_name: e.target.value || null})} hint="这里只保存变量名称" />
          {workspaceEndpointManaged && <Field label="百炼 Workspace ID" value={selected.workspace_id ?? ''} placeholder="llm-xxxxxxxx" pattern="[a-z0-9][a-z0-9-]{2,127}" onChange={(e) => updateWorkspace(selected, e.target.value)} hint="非密钥，保存于配置快照" />}
          <Field className="span-two" label={workspaceEndpointManaged ? '派生 Base URL' : 'Base URL'} value={selected.base_url ?? ''} readOnly={workspaceEndpointManaged} onChange={(e) => patch(selected.profile_id, {base_url: e.target.value || null})} hint={workspaceEndpointManaged ? '根据 Workspace ID 自动生成，无需手工拼接' : undefined} />
          <Field label="向量维度" type="number" value={selected.embedding_dimension ?? ''} onChange={(e) => patch(selected.profile_id, {embedding_dimension: e.target.value ? Number(e.target.value) : null})} />
          <Field label="超时（秒）" type="number" value={selected.timeout_seconds} onChange={(e) => patch(selected.profile_id, {timeout_seconds: Number(e.target.value)})} />
          <Field label="并发数" type="number" value={selected.concurrency} onChange={(e) => patch(selected.profile_id, {concurrency: Number(e.target.value)})} />
          <Field label="每分钟请求" type="number" value={selected.requests_per_minute} onChange={(e) => patch(selected.profile_id, {requests_per_minute: Number(e.target.value)})} />
          <Field label="输入单价（元/百万 Token）" type="number" min="0" step="0.001" value={selected.price_input_per_million} onChange={(e) => patch(selected.profile_id, {price_input_per_million: Number(e.target.value)})} hint="用于费用估算，未配置请保持 0" />
          <Field label="输出单价（元/百万 Token）" type="number" min="0" step="0.001" value={selected.price_output_per_million} onChange={(e) => patch(selected.profile_id, {price_output_per_million: Number(e.target.value)})} hint="生成模型需同时配置输入、输出单价" />
          {selected.adapter_type === 'openai_compatible' && <RequestOptionsField profile={selected} patch={patch} setMessage={setMessage} />}
        </div>
        <div className="toggle-list compact"><Toggle label="启用模型" checked={selected.enabled} onChange={(enabled) => patch(selected.profile_id, {enabled})} hint="云模型设为默认路由前必须先探测成功" /></div>
      </div>}
    </div>
  </>
}

function RequestOptionsField({ profile, patch, setMessage }: {
  profile: ModelProfile
  patch: (id: string, patch: Partial<ModelProfile>) => void
  setMessage: (value: {tone: string; text: string} | null) => void
}) {
  const serialized = JSON.stringify(profile.request_options ?? {})
  const [value, setValue] = useState(serialized)
  useEffect(() => setValue(serialized), [profile.profile_id, serialized])

  function commit() {
    try {
      const parsed = JSON.parse(value) as unknown
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('必须是 JSON 对象')
      const options = parsed as Record<string, unknown>
      if (Object.values(options).some((item) => !['boolean', 'number', 'string'].includes(typeof item))) {
        throw new Error('参数值只能是字符串、数字或布尔值')
      }
      patch(profile.profile_id, {request_options: options as ModelProfile['request_options']})
      setMessage(null)
    } catch (error) {
      setMessage({tone: 'danger', text: `请求扩展参数无效：${error instanceof Error ? error.message : 'JSON 格式错误'}`})
      setValue(serialized)
    }
  }

  return <Field className="span-two" label="请求扩展参数（JSON）" value={value} onChange={(event) => setValue(event.target.value)} onBlur={commit} hint='仅允许标量扩展参数，例如 {"enable_thinking":false}' />
}

function RoutingSettings({ config, setSection }: { config: RuntimeConfig; setSection: SectionSetter }) {
  const options = (cap: Capability) => config.models.filter((m) => m.enabled && m.capability === cap)
  const route = (label: string, key: keyof RuntimeConfig['routing'], cap: Capability, optional = false) => <SelectField label={label} value={(config.routing[key] as string | null) ?? ''} onChange={(e) => setSection('routing', {[key]: e.target.value || null})}>{optional && <option value="">不启用</option>}{options(cap).map((m) => <option value={m.profile_id} key={m.profile_id}>{m.display_name}</option>)}</SelectField>
  return <><div className="section-head"><div><h2>能力路由</h2><p>文本向量和轻量重排序使用百炼，问答、审核与撰写生成使用 OpenAI 兼容对话模型；保存时进行能力与连通性校验。</p></div></div><div className="form-grid">{route('结构解析器','structure_parser','STRUCTURE_PARSER')}{route('首选 OCR','ocr_primary','OCR')}{route('降级 OCR','ocr_fallback','OCR',true)}{route('复杂页面 VLM','vlm_primary','VISION_LM',true)}{route('视觉检索','visual_retrieval_primary','VISUAL_RETRIEVAL',true)}{route('文本向量（百炼）','text_embedding_primary','TEXT_EMBEDDING',true)}{route('轻量重排序（百炼）','reranker_primary','RERANKER',true)}{route('内容生成（OpenAI 兼容）','qa_generation_primary','CHAT_LLM',true)}</div><div className="route-flow"><span>NATIVE</span><b>→</b><span>OCR</span><b>→</b><span>REGION OCR</span><b>→</b><span>VLM</span></div></>
}

function NumberField({ label, value, onChange, step }: { label: string; value: number; onChange: (value: number) => void; step?: string }) { return <Field label={label} type="number" step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} /> }

function ParsingSettings({ config, setSection }: { config: RuntimeConfig; setSection: SectionSetter }) {
  return <><div className="section-head"><div><h2>解析与质量</h2><p>质量阈值满足通过分 &gt; 警告分 &gt; 重试分。</p></div></div><h3 className="group-title">解析限制</h3><div className="form-grid three">
    <NumberField label="最大文件（MB）" value={config.parsing.max_file_size_mb} onChange={(v) => setSection('parsing',{max_file_size_mb:v})}/><NumberField label="最大页数" value={config.parsing.max_page_count} onChange={(v) => setSection('parsing',{max_page_count:v})}/><NumberField label="PDF 渲染 DPI" value={config.parsing.pdf_render_dpi} onChange={(v) => setSection('parsing',{pdf_render_dpi:v})}/><NumberField label="可检索字符阈值" value={config.parsing.searchable_chars_per_page_min} onChange={(v) => setSection('parsing',{searchable_chars_per_page_min:v})}/><NumberField label="归档最大深度" value={config.parsing.archive_max_depth} onChange={(v) => setSection('parsing',{archive_max_depth:v})}/><NumberField label="解析超时（秒）" value={config.parsing.timeout_seconds} onChange={(v) => setSection('parsing',{timeout_seconds:v})}/>
  </div><h3 className="group-title">质量路由</h3><div className="form-grid three"><NumberField label="通过阈值" step="0.01" value={config.quality.pass_score} onChange={(v) => setSection('quality',{pass_score:v})}/><NumberField label="警告阈值" step="0.01" value={config.quality.warning_score} onChange={(v) => setSection('quality',{warning_score:v})}/><NumberField label="重试阈值" step="0.01" value={config.quality.retry_score} onChange={(v) => setSection('quality',{retry_score:v})}/><NumberField label="复杂表格阈值" step="0.01" value={config.quality.complex_table_threshold} onChange={(v) => setSection('quality',{complex_table_threshold:v})}/><NumberField label="视觉索引阈值" step="0.01" value={config.quality.visual_required_threshold} onChange={(v) => setSection('quality',{visual_required_threshold:v})}/></div></>
}

function IndexSettings({ config, setSection }: { config: RuntimeConfig; setSection: SectionSetter }) {
  return <><div className="section-head"><div><h2>切分与索引</h2><p>本页变化通常会建立新的影子索引，禁止混用向量空间。</p></div></div><h3 className="group-title">文本切分</h3><div className="form-grid three"><NumberField label="最小字符" value={config.chunking.target_min_chars} onChange={(v) => setSection('chunking',{target_min_chars:v})}/><NumberField label="最大字符" value={config.chunking.target_max_chars} onChange={(v) => setSection('chunking',{target_max_chars:v})}/><SelectField label="表格序列化" value={config.chunking.table_serialization} onChange={(e) => setSection('chunking',{table_serialization: e.target.value as 'markdown'|'row_text'|'html'})}><option value="row_text">行语义文本</option><option value="markdown">Markdown</option><option value="html">HTML</option></SelectField></div><h3 className="group-title">索引空间</h3><div className="form-grid three"><Field label="文本 Collection 前缀" value={config.indexes.text_collection_prefix} onChange={(e) => setSection('indexes',{text_collection_prefix:e.target.value})}/><Field label="视觉 Collection 前缀" value={config.indexes.visual_collection_prefix} onChange={(e) => setSection('indexes',{visual_collection_prefix:e.target.value})}/><NumberField label="文本向量维度" value={config.indexes.embedding_dimension} onChange={(v) => setSection('indexes',{embedding_dimension:v})}/><SelectField label="距离算法" value={config.indexes.distance} onChange={(e) => setSection('indexes',{distance:e.target.value as 'Cosine'|'Dot'|'Euclid'})}><option>Cosine</option><option>Dot</option><option>Euclid</option></SelectField></div><div className="toggle-list"><Toggle label="启用视觉索引" checked={config.indexes.visual_enabled} onChange={(v) => setSection('indexes',{visual_enabled:v})}/><Toggle label="仅复杂页面生成视觉向量" checked={config.indexes.visual_only_complex_pages} onChange={(v) => setSection('indexes',{visual_only_complex_pages:v})}/><Toggle label="发布前强制视觉索引就绪" checked={config.indexes.visual_required_before_publish} onChange={(v) => setSection('indexes',{visual_required_before_publish:v})}/></div></>
}

function ExecutionSettings({ config, setSection }: { config: RuntimeConfig; setSection: SectionSetter }) {
  return <><div className="section-head"><div><h2>执行与预算</h2><p>费用上限是硬限制，全量云端任务仍需在创建时二次确认。</p></div></div><div className="form-grid three"><NumberField label="CPU Worker 并发" value={config.execution.cpu_worker_concurrency} onChange={(v) => setSection('execution',{cpu_worker_concurrency:v})}/><NumberField label="ML Worker 并发" value={config.execution.ml_worker_concurrency} onChange={(v) => setSection('execution',{ml_worker_concurrency:v})}/><NumberField label="云调用/分钟" value={config.execution.cloud_calls_per_minute} onChange={(v) => setSection('execution',{cloud_calls_per_minute:v})}/><NumberField label="基准调用上限" value={config.budget.benchmark_cloud_call_limit} onChange={(v) => setSection('budget',{benchmark_cloud_call_limit:v})}/><NumberField label="单任务云调用上限" value={config.budget.max_cloud_calls_per_job} onChange={(v) => setSection('budget',{max_cloud_calls_per_job:v})}/><NumberField label="费用上限（元）" step="0.01" value={config.budget.estimated_cost_cny_limit} onChange={(v) => setSection('budget',{estimated_cost_cny_limit:v})}/></div><div className="toggle-list"><Toggle label="允许云端处理" checked={config.budget.cloud_processing_allowed} onChange={(v) => setSection('budget',{cloud_processing_allowed:v})} hint="开启后任务仍需显式选择允许云处理"/><Toggle label="全量云任务需要确认" checked={config.budget.full_run_requires_confirmation} onChange={(v) => setSection('budget',{full_run_requires_confirmation:v})}/></div></>
}

function HistorySettings({ versions, rollback }: { versions: ConfigVersion[]; rollback: (version: ConfigVersion) => void }) {
  return <><div className="section-head"><div><h2>版本历史</h2><p>回滚会复制旧配置并生成新版本，不修改审计历史。</p></div></div><div className="timeline">{versions.map((version) => <article key={version.id} className={version.active ? 'active' : ''}><span className="timeline-dot"><History size={15}/></span><div><div className="timeline-title"><strong>v{version.version} · {version.change_reason}</strong>{version.active && <Badge tone="success">当前</Badge>}<Badge tone={version.impact === 'HOT' ? 'info' : 'warning'}>{impactText[version.impact]}</Badge></div><small>{new Date(version.created_at).toLocaleString('zh-CN')} · {version.created_by}</small><code>{version.content_hash.slice(0,24)}…</code></div>{!version.active && <button className="button small" onClick={() => rollback(version)}><RotateCcw size={14}/> 回滚</button>}</article>)}</div></>
}
