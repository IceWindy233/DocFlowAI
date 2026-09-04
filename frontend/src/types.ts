export type Impact = 'HOT' | 'REPARSE_REQUIRED' | 'REINDEX_REQUIRED'

export type Capability =
  | 'VISION_LM'
  | 'TEXT_EMBEDDING'
  | 'VISUAL_RETRIEVAL'
  | 'OCR'
  | 'STRUCTURE_PARSER'
  | 'CHAT_LLM'
  | 'RERANKER'

export interface ModelProfile {
  profile_id: string
  display_name: string
  provider_id: string
  adapter_type: string
  capability: Capability
  model_name: string
  workspace_id: string | null
  base_url: string | null
  secret_env_name: string | null
  enabled: boolean
  fallback_profile_id: string | null
  temperature: number
  max_output_tokens: number
  timeout_seconds: number
  max_retries: number
  concurrency: number
  requests_per_minute: number
  embedding_dimension: number | null
  model_signature: string
  price_input_per_million: number
  price_output_per_million: number
  request_options: Record<string, boolean | number | string>
}

export interface RuntimeConfig {
  schema_version: '1.0'
  profile_name: string
  models: ModelProfile[]
  routing: {
    structure_parser: string
    ocr_primary: string
    ocr_fallback: string | null
    vlm_primary: string | null
    visual_retrieval_primary: string | null
    text_embedding_primary: string | null
    reranker_primary: string | null
    qa_generation_primary: string | null
    upgrade_order: ('NATIVE' | 'OCR' | 'REGION_OCR' | 'VLM')[]
  }
  parsing: {
    max_file_size_mb: number
    max_page_count: number
    timeout_seconds: number
    pdf_render_dpi: number
    searchable_chars_per_page_min: number
    archive_max_depth: number
    archive_max_entries: number
    archive_max_uncompressed_mb: number
    macros_allowed: boolean
    embedded_files_allowed: boolean
  }
  quality: {
    pass_score: number
    warning_score: number
    retry_score: number
    complex_table_threshold: number
    visual_required_threshold: number
  }
  chunking: {
    target_min_chars: number
    target_max_chars: number
    preserve_heading_boundary: boolean
    repeat_table_headers: boolean
    table_serialization: 'markdown' | 'row_text' | 'html'
  }
  indexes: {
    text_collection_prefix: string
    visual_collection_prefix: string
    distance: 'Cosine' | 'Dot' | 'Euclid'
    embedding_dimension: number
    visual_enabled: boolean
    visual_only_complex_pages: boolean
    visual_required_before_publish: boolean
  }
  execution: {
    cpu_worker_concurrency: number
    ml_worker_concurrency: number
    retry_backoff_seconds: number
    circuit_breaker_failures: number
    cloud_calls_per_minute: number
  }
  budget: {
    cloud_processing_allowed: boolean
    benchmark_cloud_call_limit: number
    full_run_requires_confirmation: boolean
    max_cloud_calls_per_job: number
    max_input_tokens_per_job: number
    estimated_cost_cny_limit: number
  }
  publication: {
    supported_publish_rate_min: number
    authority_score_review_threshold: number
    require_no_missing_page_alignment: boolean
    require_visual_ready_when_required: boolean
  }
  security: {
    bind_localhost_only: boolean
    allowed_secret_env_names: string[]
    log_document_content: boolean
    prompt_response_trace_enabled: boolean
  }
  prompt_templates: Record<string, unknown>
}

export interface ConfigVersion {
  id: string
  version: number
  active: boolean
  content_hash: string
  impact: Impact
  impact_details: { changed_paths?: string[]; reasons?: string[] }
  change_reason: string
  created_by: string
  created_at: string
  config: RuntimeConfig
}

export interface Job {
  id: string
  job_type: string
  source_root: string
  source_roots: string[]
  status: string
  config_version_id: string
  index_generation_id: string
  options: Record<string, unknown>
  progress: Record<string, number>
  stage_counts: Record<string, number>
  model_signatures: Record<string, string>
  cloud_usage: Record<string, number>
  error_code: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface PublicationValidation {
  config_version_id: string
  index_generation_id: string
  passed: boolean
  checks: {
    publish_rate: { value: number; required: number; passed: boolean }
    visual_ready: { missing: number; passed: boolean }
    embedding_ready: { failed: number; missing?: number; passed: boolean }
    page_alignment: { missing: number; passed: boolean }
    counts: {
      supported_sources: number
      published_ready_sources: number
      documents: number
      chunks: number
    }
  }
}

export interface Publication {
  id: string
  config_version_id: string
  index_generation_id: string
  status: string
  active: boolean
  validation: PublicationValidation
  created_at: string
  published_at: string | null
}

export interface ReviewTask {
  id: string
  job_id: string | null
  source_file_id: string | null
  document_id: string | null
  category: string
  severity: string
  status: string
  summary: string
  details: Record<string, unknown>
  created_at: string
}

export interface DocumentSummary {
  id: string
  source_file_id: string
  config_version_id: string
  case_id: string
  title: string
  document_number: string | null
  document_role: string
  version_role: string
  authority_score: number
  selected: boolean
  parser_route: string
  parser_version: string
  quality_score: number
  created_at: string
}

export interface RetrievalResult {
  rank: number
  score: number
  ranking_algorithm: 'RRF' | 'MaxSim' | 'BM25' | 'QWEN_RERANK'
  page_id: string
  page_number: number
  page_type: string
  document_id: string
  title: string
  document_number: string | null
  case_id: string
  document_role: string
  version_role: string
  authority_score: number
  relative_path: string | null
  snippet: string
  preview_url: string | null
  model_signature: string
  collection: string
  match_sources: ('visual' | 'text' | 'semantic')[]
  visual_score: number | null
  text_score: number | null
  semantic_score: number | null
  branch_ranks: Record<string, number>
  rrf_contributions: Record<string, number>
  rrf_score: number | null
  rerank_score: number | null
}

export interface RetrievalDebugBranch {
  total: number
  model_signature?: string | null
  collection?: string | null
  results: { rank: number; page_id: string; document_id: string; score: number; title: string; page_number: number }[]
}

export interface RetrievalResponse {
  query: string
  mode: 'hybrid' | 'visual' | 'text'
  context: {
    config_version_id: string
    index_generation_id: string
    source: string
  }
  total: number
  runtime_config_version_id: string
  warnings: string[]
  cloud_usage: {
    calls: number
    input_tokens: number
    output_tokens: number
  }
  results: RetrievalResult[]
  debug: {
    candidate_limit: number
    filters: Record<string, unknown>
    branches: {
      visual: RetrievalDebugBranch
      bm25: RetrievalDebugBranch
      semantic: RetrievalDebugBranch
    }
    fusion: { algorithm: string; constant: number }
    reranker: { requested: boolean; configured: boolean; applied: boolean; model_signature: string | null; candidate_count?: number; warning?: string }
  } | null
}

export interface RetrievalCitation {
  id: number
  page_id: string
  document_id: string
  case_id: string
  title: string
  document_number: string | null
  page_number: number
  relative_path: string | null
  excerpt: string
  preview_url: string | null
  match_sources: ('visual' | 'text' | 'semantic')[]
}

export interface WorkflowTraceStep {
  sequence: number
  node: string
  label: string
  status: 'SUCCEEDED' | 'FAILED'
  duration_ms: number
  summary: string
}

export interface RetrievalAnswerResponse {
  question: string
  rewritten_query: string
  answer: string
  answer_mode: 'LOCAL_EXTRACTIVE' | 'SAFE_REFUSAL' | string
  confidence: number
  citations: RetrievalCitation[]
  case_ids: string[]
  verification: {
    citations_valid: boolean
    answer_grounded: boolean
    inline_citations_resolved: boolean
    unresolved_citation_ids: number[]
    citation_count: number
  }
  evidence_assessment: { sufficient: boolean; score: number; reasons: string[] }
  generation_model_signature: string
  generation_warning: string | null
  generation_config_version_id: string
  cloud_usage: {
    calls: number
    input_tokens: number
    output_tokens: number
  }
  retrieval: RetrievalResponse
  workflow: {
    run_id: string
    workflow_type: string
    status: string
    engine: string
    engine_version: string
    trace: WorkflowTraceStep[]
    started_at: string
    finished_at: string | null
  }
}

export interface RetrievalOptions {
  context: RetrievalResponse['context']
  cases: { value: string; count: number }[]
  document_roles: { value: string; count: number }[]
  version_roles: { value: string; count: number }[]
  date_range: { from: string | null; to: string | null; source: string }
  presets: { question: string; document_id: string }[]
}

export interface QaEvaluationSample {
  id: string
  index_generation_id: string
  question: string
  reference_answer: string
  answer_aliases: string[]
  expected_page_ids: string[]
  expected_document_ids: string[]
  category: string
  status: 'DRAFT' | 'CONFIRMED' | 'DISABLED'
  source: string
  notes: string
  created_at: string
  updated_at: string
}

export interface QaEvaluationRun {
  id: string
  index_generation_id: string
  config_version_id: string
  status: string
  metrics: { sample_count?: number; completed?: number; recall_at_5?: number | null; answer_accuracy?: number | null; citation_accuracy?: number | null }
  results: { sample_id: string; question: string; category: string; reference_answer: string; generated_answer: string; recall_at_5: boolean; answer_correct: boolean | null; citation_correct: boolean | null }[]
  cloud_usage: { calls?: number; input_tokens?: number; output_tokens?: number }
  error_message: string | null
  created_at: string
  finished_at: string | null
}

export type AgentEvaluationCapability = 'QA' | 'REVIEW' | 'DRAFT'
export type AgentEvaluationMode =
  | 'LOCAL_RETRIEVAL'
  | 'FULL_QA'
  | 'LOCAL_RULES'
  | 'FULL_REVIEW'
  | 'REQUIREMENT_GATE'
  | 'FULL_DRAFT'

export interface FixedAgentSample {
  id: string
  name: string
  category?: string
  difficulty?: 'EASY' | 'MEDIUM' | 'HARD'
  question?: string
  title?: string
  text?: string
  scope?: string[]
  requirements?: DraftTask['requirements']
  coverage: string[]
  resolvable?: boolean
  expected: {
    behavior?: 'ANSWER' | 'ABSTAIN'
    reference_answer?: string
    evidence?: { resolved: boolean; page_id: string | null; title: string; page_number: number }[]
    required_categories?: string[]
    required_facts_in_draft?: string[]
    should_create?: boolean
    [key: string]: unknown
  }
}

export interface AgentEvaluationCatalog {
  set_id: string
  name: string
  description: string
  schema_version: string
  distribution: Record<Lowercase<AgentEvaluationCapability>, number>
  context: {
    config_version_id: string
    index_generation_id: string
    source: string
    source_publication_matches_snapshot: boolean
  }
  qa_samples: FixedAgentSample[]
  review_samples: FixedAgentSample[]
  draft_samples: FixedAgentSample[]
  resolution: {
    qa_sample_count: number
    resolvable_count: number
    evidence_count: number
    resolved_evidence_count: number
  }
}

export interface AgentEvaluationResult {
  sample_id: string
  name: string
  passed: boolean | null
  error?: string
  question?: string
  behavior?: 'ANSWER' | 'ABSTAIN'
  answer?: string
  answer_mode?: string
  recall_at_5?: boolean | null
  locator_recall_at_5?: boolean | null
  locator_coverage?: number | null
  locator_evidence_coverage?: number | null
  locator_citation_coverage?: number | null
  fact_coverage?: number
  evidence_coverage?: number
  citation_coverage?: number
  abstention_correct?: boolean
  category_recall?: number
  finding_count?: number
  duplicate_count?: number
  findings?: { severity: string; category: string; original_text: string; reason: string; sources: string[] }[]
  missing_fields?: string[]
  missing_facts?: string[]
  verification_passed?: boolean
  generation_quality_passed?: boolean
  safety_gate_passed?: boolean
  repair_attempted?: boolean
  preferred_evidence_hit?: boolean
  post_edit_probe_passed?: boolean | null
  review_id?: string
  draft_id?: string
}

export interface AgentEvaluationRun {
  id: string
  sample_set_id: string
  capability: AgentEvaluationCapability
  mode: AgentEvaluationMode
  status: string
  config_version_id: string | null
  index_generation_id: string | null
  metrics: Record<string, number | string | null>
  results: AgentEvaluationResult[]
  cloud_usage: { calls?: number; input_tokens?: number; output_tokens?: number; estimated_cost_cny?: number; pricing_configured?: boolean }
  error_message: string | null
  created_at: string
  finished_at: string | null
}

export interface WorkflowRunSummary {
  id: string
  workflow_type: string
  status: string
  config_version_id: string | null
  index_generation_id: string | null
  engine: string
  engine_version: string
  trace: WorkflowTraceStep[]
  error_message: string | null
  created_at: string
  started_at: string
  finished_at: string | null
  input?: Record<string, unknown>
  state?: Record<string, unknown>
  output?: Record<string, unknown>
  model_signature?: string | null
  cloud_usage?: { calls?: number; input_tokens?: number; output_tokens?: number; estimated_cost_cny?: number; pricing_configured?: boolean }
}

export interface DocumentReviewFinding {
  id: string
  review_id: string
  severity: 'CRITICAL' | 'MAJOR' | 'MINOR' | 'SUGGESTION'
  category: string
  location: { paragraph?: number | null; start?: number | null; end?: number | null }
  original_text: string
  suggested_text: string
  reason: string
  evidence: { title?: string; page_id?: string; page_number?: number; preview_url?: string }[]
  sources: ('RULE' | 'LLM')[]
  confidence: number
  auto_fixable: boolean
  status: 'PENDING' | 'ACCEPTED' | 'REJECTED'
  feedback: string
}

export interface DocumentReviewRun {
  id: string
  document_id: string | null
  title: string
  input_text: string
  status: string
  scope: string[]
  summary: { total?: number; critical?: number; major?: number; minor?: number; suggestion?: number; warning?: string | null }
  revised_text: string
  report_url: string | null
  config_version_id: string
  workflow_run_id: string | null
  model_signature: string
  cloud_usage: { calls?: number; input_tokens?: number; output_tokens?: number }
  created_at: string
  finished_at: string | null
  findings: DocumentReviewFinding[]
}

export interface DraftTask {
  id: string
  document_type: 'REQUEST' | 'LETTER'
  title: string
  status: string
  requirements: {
    document_type: 'REQUEST' | 'LETTER'
    subject: string
    recipient: string
    background: string
    facts: string
    requested_action: string
    sender: string
    date: string
    reference_query: string
  }
  missing_fields: string[]
  selected_cases: { case_id: string; document_id: string; title: string; document_number: string | null; authority_score: number }[]
  evidence_bundle: { id: number; case_id: string; document_id: string; page_id: string; title: string; document_number: string | null; page_number: number; snippet: string; preview_url: string | null; evidence_type: 'FACT_EVIDENCE' | 'STYLE_REFERENCE'; relevance_score: number; selection_reason: string }[]
  outline: { id: string; title: string; render_heading?: boolean }[]
  draft_text: string
  verification: { passed?: boolean; missing_required_facts?: string[]; missing_required_fields?: string[]; invalid_citation_ids?: number[]; unverified_facts?: string[]; warning?: string | null; fact_count?: number; citation_count?: number }
  export_url: string | null
  config_version_id: string
  workflow_run_id: string | null
  model_signature: string
  cloud_usage: { calls?: number; input_tokens?: number; output_tokens?: number }
  created_at: string
  updated_at: string
  finished_at: string | null
  revision_count: number
}

export interface DraftInterpretation {
  requirements: DraftTask['requirements']
  requirements_patch: Partial<DraftTask['requirements']>
  missing_field_keys: string[]
  missing_fields: string[]
  follow_up_question: string
  confidence: number
  ambiguities: string[]
  model_signature: string
  cloud_usage: DraftTask['cloud_usage']
  warning: string | null
  trace: { sequence: number; node: string; label: string; status: string; duration_ms: number; summary: string }[]
  config_version_id: string
}

export interface DraftRevision {
  id: string
  draft_id: string
  revision_number: number
  source: 'GENERATED' | 'MANUAL_EDIT' | 'REGENERATED' | 'RESTORED'
  draft_text: string
  verification: DraftTask['verification']
  model_signature: string
  cloud_usage: DraftTask['cloud_usage']
  note: string
  created_at: string
}
