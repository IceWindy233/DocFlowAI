# DocFlow AI 系统设计文档

> 基于案件级 RAG 的公文撰写、审核与信息检索问答 Agent

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v0.5 |
| 文档状态 | 初稿 |
| 更新日期 | 2026-08-11 |
| 适用阶段 | 需求分析、架构设计、MVP 实施 |

> v0.2 调整：将复杂表格、混合图文页面增强和 ColPali 视觉索引前移为文档解析与知识入库的基础能力，不再作为 Agent 完成后的增强项。

> v0.3 范围裁剪：M1 先交付智能解析路由、Markdown/表格产物、ColPali 视觉入库与文本查询页面召回；Golden Set 和企业级验收门禁降为可选，后续再迭代问答、审核与撰写能力。

> v0.4 已实现 M2 问答 MVP：使用 LangGraph 编排问题理解、混合召回、证据判断、答案生成和引用校验；使用 LangChain `Document` 作为证据边界，并持久化每次运行的节点轨迹。

> v0.5 固定云模型分工：文本稠密向量采用百炼 `qwen3.7-text-embedding` 2560 维，RAG、审核与撰写生成通过通用 OpenAI 兼容适配器调用配置中心指定的对话模型（出厂默认示例为硅基流动 `deepseek-ai/DeepSeek-V4-Flash`）；BM25、ColPali、Qdrant 和引用校验继续本地运行。

---

## 1. 文档目的

本文档用于定义 DocFlow AI 的产品边界、系统架构、数据模型、核心流程、接口约定、评估体系和实施路线，作为后续开发、测试与验收的共同依据。

DocFlow AI 面向企业公文场景，提供三个主要功能：

1. **公文撰写**：基于用户提供的事项信息、历史权威公文、模板和组织规范，辅助生成可追溯的公文初稿。
2. **公文审核**：对用户上传或在线编辑的文稿进行结构、格式、事实、用词、版本及引用审核，输出可定位、可解释的修改建议。
3. **信息检索问答**：基于历史公文事项、主文、附件、复函、意见汇总和呈批材料回答问题，并提供文件、页码和原文证据。

系统不是通用聊天机器人，也不以“替代人工签发公文”为目标。系统的定位是：

> 以案件级知识组织、混合检索、证据引用和人工确认机制，提高公文检索、撰写与审核效率。

---

## 2. 项目背景

### 2.1 当前语料概况

当前工作区为单一组织的历史公文集合，规模约数千个文件、总量 GB 级，具有以下特征：

- 以 DOC、DOCX、WPS 等 Office 文档和 PDF 为主，另有少量 XLS/XLSX、图片和压缩文件；
- 相当比例的 PDF 为扫描件，需要 OCR 与版面理解；
- 普遍存在完全重复文件与 Office 临时文件，需要去重和过滤；
- 按“请示”“函”等文种和事项分目录组织，单个事项常跨多个文件。

现有目录体现了较完整的公文业务链：

```text
一个公文事项
├── 公文主文
├── 公文稿纸
├── 征求意见函
├── 多部门复函
├── 意见汇总
├── 政府/党委会议呈批表
├── 合同、协议、财务表等附件
└── 旧版、修改版、以此为准等版本
```

### 2.2 现有痛点

1. 历史文件数量多，单纯依赖目录和文件名难以找到真正相似的事项。
2. 一个事项的材料散布在多个文件中，缺少案件级关联。
3. 旧版、临时版、定稿版同时存在，检索时容易误用旧稿。
4. 大量 PDF 为扫描件，传统文本提取无法直接处理。
5. 公文中的金额、面积、期限、公司名称等事实分散在正文、表格和附件中。
6. 多部门复函需要人工逐份阅读并汇总意见。
7. 公文撰写依赖个人经验，历史案例、固定结构和常用表述复用效率较低。
8. 审核主要依赖人工，结构遗漏、数字不一致和附件编号错误不易发现。
9. 公文包含姓名、电话、签名、印章、合同及财务数据，不能直接使用公共云服务或公开到 GitHub。

---

## 3. 建设目标与非目标

### 3.1 建设目标

#### G1：建立案件级公文知识库

将属于同一事项的主文、附件、征求意见函、复函、意见汇总、呈批表和不同版本关联为一个 `Case`，而不是将每个文件作为互不相关的知识单元。

#### G2：提供有依据的公文撰写能力

生成结果必须能够说明参考了哪些历史案例、模板、固定表述和事实材料。无法从用户输入或知识库确认的事实不得擅自补全。

#### G3：提供可解释的公文审核能力

每条审核意见应包含问题位置、问题类型、原文、建议、理由、证据和严重程度。

#### G4：提供可追溯的检索问答能力

问答结果默认携带来源文件、事项、页码、章节和原文片段。证据不足时明确拒答或提示用户补充材料。

#### G5：建立可量化评估体系

从解析、检索、生成、审核、引用和性能等维度建立离线评估集，避免仅凭主观演示判断效果。

#### G6：满足本地化与隐私要求

默认支持本地解析、本地存储和本地模型；所有云服务均为显式可选能力，并受到数据分级和脱敏策略控制。

### 3.2 非目标

MVP 阶段不包含：

- 自动替代领导审批、签发或盖章；
- 自动向外部单位发送公文；
- 对法律、财务事项给出无人工确认的最终决策；
- 对所有历史文件进行无差别公开；
- 训练模仿具体个人写作风格的模型；
- 一次性支持所有法定公文文种；
- 将 Agent 设计为拥有无限工具调用能力的全自主系统。

---

## 4. 用户角色

| 角色 | 主要诉求 | 权限范围 |
| --- | --- | --- |
| 撰稿人员 | 查找历史案例、生成提纲和初稿 | 查询授权事项、创建和编辑草稿 |
| 审核人员 | 发现格式、事实和表述问题 | 查询证据、创建审核报告、确认或驳回建议 |
| 业务人员 | 查询历史事项、附件、意见和结论 | 只读查询授权范围内的知识库 |
| 知识库管理员 | 入库、修正分类、维护版本和权限 | 管理数据源、版本、权限和索引 |
| 系统管理员 | 配置模型、任务、日志和部署 | 系统配置和运维，不默认拥有公文业务权限 |

---

## 5. 产品功能范围

### 5.1 功能一：公文撰写

#### 5.1.1 用户输入

用户可以通过表单、对话或附件提供：

- 目标文种：请示、函等；
- 事项主题；
- 主送单位；
- 背景和政策依据；
- 关键事实；
- 需要请示、协调或支持的事项；
- 金额、面积、日期、期限等结构化信息；
- 相关合同、表格或扫描附件；
- 希望参考的历史事项；
- 保密等级和输出要求。

#### 5.1.2 系统输出

- 需求完整性检查结果；
- 推荐的历史相似事项；
- 公文结构提纲；
- 带证据引用的公文初稿；
- 待确认事实清单；
- 引用来源清单；
- DOCX 导出文件；
- 生成过程审计记录。

#### 5.1.3 主要约束

- 只使用用户明确提供的事实和授权知识库中的证据；
- 默认只从权威版、定稿版、正式复函和有效模板中获取生成依据；
- 历史旧稿可用于分析修改规律，但不得直接作为事实依据；
- 生成的每个关键数字、日期、单位名称和政策引用必须可追溯；
- 缺失关键字段时应向用户提示，而不是自动编造；
- 导出前必须经过人工确认。

### 5.2 功能二：公文审核

#### 5.2.1 审核维度

1. **文种与结构审核**
   - 标题是否完整；
   - 主送单位是否缺失；
   - 背景、基本情况、请示事项是否清晰；
   - 附件说明、落款和日期是否完整。

2. **格式与规范审核**
   - 文号、日期、数字和计量单位格式；
   - 标题层级和序号；
   - 附件编号及正文引用；
   - 公文常用规范表达。

3. **事实一致性审核**
   - 金额、面积、期限、日期前后一致；
   - 表格与正文一致；
   - 公司、部门和人员称谓一致；
   - 附件事实与正文表述一致。

4. **依据与引用审核**
   - 政策、合同、会议结论是否有对应证据；
   - 引用的文号、合同编号和文件名是否存在；
   - 引用内容是否与原文相符。

5. **版本与时效审核**
   - 是否引用旧版或被替代文件；
   - 机构名称和政策是否仍有效；
   - 是否存在更新的“以此为准”版本。

6. **语言与风格审核**
   - 是否口语化、歧义或冗长；
   - 是否符合当前文种的语气；
   - 请示事项是否明确且可执行；
   - 固定表述是否符合组织习惯。

7. **敏感信息审核**
   - 是否包含不应公开的手机号、身份证号等；
   - 是否包含不应出现在正文中的内部批注或修订痕迹；
   - 是否错误暴露印章、签名和合同敏感字段。

#### 5.2.2 审核结果结构

```json
{
  "review_id": "rvw_xxx",
  "summary": {
    "critical": 1,
    "major": 2,
    "minor": 4,
    "suggestion": 3
  },
  "findings": [
    {
      "finding_id": "f_001",
      "severity": "major",
      "category": "fact_consistency",
      "location": {
        "page": 2,
        "paragraph_id": "p_12",
        "start": 18,
        "end": 31
      },
      "original_text": "……",
      "suggested_text": "……",
      "reason": "正文与附件中的租赁期限不一致",
      "evidence_ids": ["ev_101", "ev_102"],
      "confidence": 0.96,
      "auto_fixable": false
    }
  ]
}
```

#### 5.2.3 严重程度

| 级别 | 含义 | 示例 |
| --- | --- | --- |
| Critical | 可能导致事实、法律或决策错误 | 金额与合同不一致、引用错误政策 |
| Major | 明显影响公文完整性或准确性 | 请示事项缺失、引用旧版材料 |
| Minor | 格式、用词或局部规范问题 | 日期写为“某月某号” |
| Suggestion | 非错误的优化建议 | 句子过长、结构可进一步精简 |

### 5.3 功能三：信息检索问答

#### 5.3.1 支持的问题类型

- 精确查询：“示例请〔2027〕7号的请示事项是什么？”
- 相似事项：“以前有没有类似的物业续租请示？”
- 条件筛选：“查找 2025 年涉及新增银行借款的请示。”
- 事实查询：“某事项最终审议的面积和单价是多少？”
- 关系查询：“某请示征求了哪些部门意见？”
- 汇总查询：“汇总各单位对某事项的主要意见。”
- 比较查询：“比较两个年度类似事项的金额和期限。”
- 来源查询：“这项结论来自哪个文件、哪一页？”

#### 5.3.2 回答契约

每个回答应包含：

1. 直接答案；
2. 必要的背景或条件；
3. 证据引用；
4. 来源文件、页码和章节；
5. 证据不足或版本冲突提示；
6. 可继续追问的相关事项。

回答状态分为：

- `ANSWERED`：证据充分；
- `PARTIALLY_ANSWERED`：只能回答部分内容；
- `CONFLICTED`：不同来源存在冲突；
- `INSUFFICIENT_EVIDENCE`：证据不足，拒绝给出确定结论；
- `ACCESS_DENIED`：用户无权查看相关证据。

---

## 6. 总体架构

```text
┌──────────────────────────────────────────────────────────┐
│                       用户交互层                          │
│   公文撰写工作台     公文审核工作台      知识问答界面     │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│                    API 与身份权限层                       │
│  FastAPI / Auth / RBAC / Rate Limit / Audit / File API   │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│                    Agent 工作流层                         │
│ Intent Router / Draft Graph / Review Graph / QA Graph     │
│ Human-in-the-loop / State / Checkpoint / Tool Policy      │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│                       RAG 服务层                          │
│ Query Parser / Case Retrieval / Hybrid Search / Rerank   │
│ Evidence Builder / Citation Verify / Fact Verify          │
└───────────────┬───────────────────────┬───────────────────┘
                │                       │
┌───────────────▼──────────┐ ┌──────────▼──────────────────┐
│      结构化知识与索引     │ │       原始材料与解析结果     │
│ PostgreSQL / pgvector    │ │ Object Storage / JSON / MD │
│ BM25 / Entity / Relation │ │ Page Image / Table / DOCX  │
└───────────────▲──────────┘ └──────────▲──────────────────┘
                │                       │
┌───────────────┴───────────────────────┴───────────────────┐
│                    文档处理与知识构建层                    │
│ Detect / Dedup / Convert / OCR / Parse / Route / Chunk    │
│ Version / Metadata / PII / Quality / Visual Index         │
└───────────────────────────────────────────────────────────┘
```

### 6.1 三个功能的共享底座

三个功能共用：

- 文档解析和统一数据格式；
- 案件、文档、版本和附件关系；
- 混合检索和 Reranker；
- 权限过滤；
- 事实与实体抽取；
- 引用构建和引用校验；
- 模型网关；
- 审计日志和评估服务。

三个功能的差异主要体现在：

- 检索范围；
- 允许使用的版本；
- Prompt 和输出 Schema；
- 是否允许生成新内容；
- 是否需要人工确认；
- 失败和拒答策略。

---

## 7. 自适应多模态文档处理

### 7.1 处理原则

1. 原生结构优先，不先将所有文档统一转为图片。
2. 结构化 JSON 是标准中间格式，Markdown 是派生表示。
3. 页面图片作为视觉证据保留，但不替代文本和结构。
4. 扫描文档优先 OCR；只有复杂图文或低质量页面才进入视觉检索。
5. 解析结果必须带有页码、区域坐标、置信度和来源校验值。
6. 解析失败必须显式记录，不能将空结果当作有效文档入库。

### 7.2 预处理

```text
文件发现
→ 安全检查
→ 格式识别
→ SHA-256 计算
→ 临时文件过滤
→ 精确去重
→ 事项目录识别
→ 文档角色初判
→ 版本状态初判
```

需要过滤或隔离：

- `~$` 开头的 Office 临时文件；
- 空文件和损坏文件；
- 无法确认内容的快捷方式；
- 压缩包中的重复副本；
- 标记为“空的”的事项目录；
- 恶意宏、嵌入对象和异常文件。

### 7.3 解析路由

| 文档特征 | 主要路线 | 备选路线 |
| --- | --- | --- |
| 原生 DOCX | OOXML/Docling | 渲染后视觉复核 |
| 旧版 DOC/WPS | LibreOffice 转换后解析 | 转 PDF 后 OCR |
| 可搜索简单 PDF | Docling/Marker | OCR 校正 |
| 单栏扫描公文 | 中文 OCR + 版面恢复 | VLM 校正 |
| 复杂表格/表单 | 表格解析 + 页面截图 | VLM 结构化抽取 |
| 混合图文复杂页面 | 文本解析 + ColPali 视觉索引 | VLM 页面理解 |
| 解析质量不合格 | 升级解析路线 | 人工复核 |

路由器第一阶段采用规则和质量评分，积累标注数据后再训练轻量分类模型。

### 7.4 统一文档对象

```json
{
  "document_id": "doc_xxx",
  "case_id": "case_xxx",
  "source": {
    "path": "...",
    "filename": "...",
    "sha256": "...",
    "mime_type": "application/pdf"
  },
  "classification": {
    "document_type": "请示",
    "document_role": "main_document",
    "version_status": "authoritative",
    "confidentiality": "internal"
  },
  "parse": {
    "route": "ocr_docling",
    "quality_score": 0.93,
    "requires_review": false
  },
  "pages": [],
  "relations": [],
  "entities": []
}
```

### 7.5 表格表示

每个表格同时保存：

- 单元格 JSON；
- HTML；
- 适用于全文检索和 Embedding 的文本序列化；
- 表格区域截图；
- 所在页码和 Bounding Box；
- 解析置信度；
- 跨页关系。

### 7.6 ColPali 视觉索引

ColPali 视觉索引属于文档解析与知识入库的基础能力，必须在三个 Agent 功能开发前完成。它不替代 Docling、Marker、OCR 等结构化解析器，而是为传统解析容易丢失版式、图文关系和表格语义的页面提供第二种可检索表示。

为控制计算和存储成本，不要求所有页面无差别进入 ColPali，但满足下列条件的页面必须在入库阶段建立视觉多向量索引：

- 复杂表格或表单；
- 图中包含核心业务信息；
- OCR 阅读顺序不稳定；
- 文字和图形位置关系影响语义；
- 传统文本检索在基准集中表现明显较差。

视觉索引返回相关页面后，系统仍需读取页面对应的 OCR、结构化文本或调用 VLM 对命中区域进行二次提取，才能形成可引用证据。

复杂页面只有同时满足以下条件，才视为完成文档解析：

- 保存可回源的页面图片；
- 生成 OCR 或结构化文本表示；
- 复杂表格保留单元格 JSON、HTML、文本序列化和区域截图；
- 建立 ColPali 页面视觉多向量及索引状态；
- 页面、文本、表格与视觉表示使用同一 `page_id` 关联；
- 文本检索和视觉检索均通过基础冒烟测试。

---

## 8. 案件级知识模型

### 8.1 核心实体

#### Case

表示一个完整公文事项。

```text
case_id
case_type
year
serial_number
subject
topic_tags
status
owner_department
confidentiality
created_at
updated_at
```

#### Document

表示一个物理或逻辑文档。

```text
document_id
case_id
document_type
document_role
title
document_number
version_status
authority_level
issuing_org
issue_date
source_path
sha256
parse_quality
access_policy_id
```

#### DocumentVersion

```text
version_id
document_id
version_label
is_active
supersedes_version_id
version_reason
detected_by
confirmed_by
```

#### Page / Block / Chunk

用于页面级引用、版面定位和检索。

#### Entity

包括：

- 组织机构；
- 人员；
- 日期；
- 金额；
- 面积；
- 租赁期限；
- 地址；
- 文号；
- 合同编号；
- 政策文件；
- 项目名称。

#### Relation

```text
attachment_of
reply_to
solicits_opinion_from
summarized_by
revision_of
supersedes
cites
submitted_to_meeting
related_to
```

### 8.2 文档角色

```text
main_document       主文
draft               草稿
official_copy       正式版/盖章版
manuscript_sheet    公文稿纸
solicitation        征求意见函
reply               复函
opinion_summary     意见汇总
meeting_form        会议呈批表
attachment          普通附件
contract            合同/协议
financial_table     财务或业务表格
policy              政策/制度依据
```

### 8.3 版本权威等级

| 权威等级 | 示例 | 撰写 | 审核 | 问答 |
| --- | --- | --- | --- | --- |
| A：权威有效 | 以此为准、正式盖章版、当前有效政策 | 可用 | 可用 | 默认可用 |
| B：正式参考 | 定稿 DOCX、正式复函、会议结论 | 可用 | 可用 | 默认可用 |
| C：历史参考 | 旧版、修改版、被替代版本 | 不作为事实依据 | 可用于版本差异 | 明确要求时可用 |
| D：不可信 | 临时文件、空文件、解析失败文件 | 禁止 | 禁止 | 禁止 |

版本状态首先由文件名、目录名、文件时间和内容差异自动推断，再由管理员对低置信度结果进行确认。

---

## 9. 索引与检索设计

### 9.1 索引类型

1. **案件摘要索引**：快速召回相关 Case。
2. **章节文本索引**：召回正文中的背景、事实和事项。
3. **标题与文号索引**：处理精确匹配。
4. **表格事实索引**：处理金额、面积、期限等查询。
5. **实体索引**：支持公司、部门、项目和文号过滤。
6. **关系索引**：支持复函、附件和版本链查询。
7. **视觉页面索引**：仅覆盖复杂多模态页面。

### 9.2 检索流程

```text
用户问题/撰写需求/审核问题
→ 意图与查询结构化
→ 权限过滤
→ 案件级候选召回
→ 文档角色和版本过滤
→ BM25 关键词召回
→ Dense Vector 语义召回
→ 对已建立视觉表示的复杂页面执行 ColPali 召回
→ RRF 融合
→ Cross-Encoder Rerank
→ 去重与证据覆盖优化
→ Evidence Bundle
```

### 9.3 查询结构

```json
{
  "intent": "compare_cases",
  "document_types": ["请示"],
  "document_roles": ["main_document", "opinion_summary"],
  "topics": ["物业续租"],
  "entities": [],
  "time_range": {
    "from": "2025-01-01",
    "to": "2026-12-31"
  },
  "version_policy": "authoritative_only",
  "requested_fields": ["面积", "单价", "期限"],
  "raw_query": "……"
}
```

### 9.4 Evidence Bundle

Agent 不直接接收大量原始 Chunk，而接收经过整理的证据包：

```json
{
  "query_id": "q_xxx",
  "cases": [],
  "evidences": [
    {
      "evidence_id": "ev_xxx",
      "case_id": "case_xxx",
      "document_id": "doc_xxx",
      "version_status": "authoritative",
      "page": 2,
      "section": "二、请示事项",
      "text": "……",
      "bbox": [0, 0, 0, 0],
      "retrieval_score": 0.91,
      "authority_score": 1.0
    }
  ],
  "conflicts": [],
  "coverage": 0.88
}
```

### 9.5 引用规则

- 每个关键事实至少关联一个 `evidence_id`；
- 金额、面积、日期、文号等高风险事实优先要求两个来源交叉验证；
- 引用必须指向用户有权查看的源文档；
- 当证据存在版本冲突时不得自动选择低权威版本；
- 引用校验失败时不允许将内容标记为“已验证”；
- 前端支持从回答跳转到源文件对应页面或段落。

---

## 10. Agent 工作流设计

### 10.1 设计原则

- Agent 采用受控状态机，而非完全自主循环；
- 模型负责语义判断、归纳和语言生成；
- 权限、版本过滤、数字校验和格式检查优先采用确定性程序；
- 每个节点使用结构化输入和输出；
- 高风险节点必须支持暂停、检查和人工确认；
- 每次工具调用记录输入摘要、输出摘要、耗时和错误；
- 从文档检索到的内容一律视为数据，不视为系统指令，防止 Prompt Injection。

### 10.2 公文撰写工作流

```text
Draft Intake
→ Requirement Validator
→ Document Type Planner
→ Similar Case Retriever
→ Template & Style Retriever
→ Fact Evidence Builder
→ Outline Generator
→ Human Outline Approval
→ Section Draft Workers
→ Draft Composer
→ Fact & Citation Verifier
→ Draft Review
→ Human Final Approval
→ DOCX Export
```

#### Draft State

```json
{
  "task_id": "draft_xxx",
  "user_id": "user_xxx",
  "document_type": "请示",
  "requirements": {},
  "missing_fields": [],
  "selected_cases": [],
  "evidence_bundle": {},
  "outline": [],
  "draft_sections": [],
  "verification_results": [],
  "approval_status": "pending",
  "export_id": null
}
```

#### 人工确认点

1. 需求字段不完整；
2. 检索到多个可能参考的事项；
3. 提纲生成完成；
4. 证据之间存在冲突；
5. 出现无法验证的关键事实；
6. DOCX 导出前。

### 10.3 公文审核工作流

```text
Review Intake
→ Document Parser
→ Review Scope Selector
→ Deterministic Rule Checks
→ Reference Case Retrieval
→ Structure Reviewer
→ Fact Consistency Reviewer
→ Citation & Version Reviewer
→ Language Reviewer
→ Finding Merger & Dedup
→ Confidence Calibration
→ Human Review
→ Review Report / Optional Patch
```

审核默认不直接修改原文件。只有用户逐条接受建议后，系统才生成新版本，并保留原文件、审核报告和变更记录。

### 10.4 信息检索问答工作流

```text
Question Intake
→ Intent & Scope Parser
→ Access Filter
→ Case Retrieval
→ Evidence Retrieval
→ Evidence Sufficiency Check
→ Conflict Detection
→ Answer Generator
→ Citation Verifier
→ Answer / Partial Answer / Refusal
```

当问题需要聚合多个部门意见时，增加：

```text
Reply Collector
→ Department Opinion Extractor
→ Opinion Normalizer
→ Agreement/Conflict Analyzer
→ Summary Composer
```

---

## 11. 确定性规则与模型能力边界

| 能力 | 优先实现方式 |
| --- | --- |
| 文号格式检查 | 正则与规则 |
| 日期格式检查 | 正则、日期解析 |
| 金额/面积一致性 | 实体抽取 + 程序比较 |
| 附件序号连续性 | 规则 |
| 版本过滤 | 数据库状态与规则 |
| 权限判断 | RBAC/ABAC |
| 相似事项检索 | BM25 + Embedding + Rerank |
| 意见归纳 | LLM 结构化输出 |
| 语气与表达审核 | LLM + 组织风格规则 |
| 初稿生成 | LLM + Evidence Bundle |
| 引用存在性验证 | 程序 |
| 引用是否支持结论 | NLI/LLM 判定 + 抽样人工评估 |

---

## 12. API 初步设计

### 12.1 公文撰写

```text
POST   /api/v1/drafts
GET    /api/v1/drafts/{draft_id}
POST   /api/v1/drafts/{draft_id}/requirements
POST   /api/v1/drafts/{draft_id}/retrieve
POST   /api/v1/drafts/{draft_id}/outline
POST   /api/v1/drafts/{draft_id}/approve-outline
POST   /api/v1/drafts/{draft_id}/generate
POST   /api/v1/drafts/{draft_id}/verify
POST   /api/v1/drafts/{draft_id}/export
```

### 12.2 公文审核

```text
POST   /api/v1/reviews
GET    /api/v1/reviews/{review_id}
GET    /api/v1/reviews/{review_id}/findings
POST   /api/v1/reviews/{review_id}/findings/{finding_id}/accept
POST   /api/v1/reviews/{review_id}/findings/{finding_id}/reject
POST   /api/v1/reviews/{review_id}/apply
GET    /api/v1/reviews/{review_id}/report
```

### 12.3 检索问答

```text
POST   /api/v1/qa/sessions
POST   /api/v1/qa/sessions/{session_id}/messages
GET    /api/v1/qa/answers/{answer_id}
GET    /api/v1/evidences/{evidence_id}
GET    /api/v1/cases/{case_id}
GET    /api/v1/documents/{document_id}/pages/{page_number}
```

### 12.4 知识库管理

```text
POST   /api/v1/admin/ingestion/jobs
GET    /api/v1/admin/ingestion/jobs/{job_id}
POST   /api/v1/admin/documents/{document_id}/reparse
PATCH  /api/v1/admin/documents/{document_id}/classification
PATCH  /api/v1/admin/documents/{document_id}/version
POST   /api/v1/admin/index/rebuild
```

所有写接口必须支持幂等键，并返回 `request_id`、任务状态和错误详情。

---

## 13. 前端交互设计

### 13.1 公文撰写工作台

建议采用左右三栏：

```text
左侧：事项信息与参考案例
中间：提纲和正文编辑器
右侧：证据、待确认事实和审核问题
```

关键交互：

- 点击正文中的引用标记查看原文；
- 用户可以锁定不允许模型改写的段落；
- 未验证事实使用醒目标识；
- 生成前展示将要使用的历史案例；
- 支持逐段重新生成，不默认全文覆盖；
- 导出前展示最终检查清单。

### 13.2 公文审核工作台

```text
左侧：原文与页码
右侧：按严重程度分类的审核意见
底部：接受、拒绝、备注、生成修订版
```

每条问题应能定位到原文，显示证据，并允许用户反馈“正确、误报、建议不适用”。反馈可用于后续评估和规则优化。

### 13.3 信息检索问答界面

回答区与证据区同时展示：

- 回答正文；
- 引用编号；
- 相关事项卡片；
- 原文件预览；
- 版本状态；
- 证据冲突提示；
- 检索范围和过滤条件。

---

## 14. 模型与基础设施建议

### 14.1 MVP 技术栈

| 层级 | 建议 |
| --- | --- |
| 后端 | Python、FastAPI、Pydantic |
| Agent 编排 | LangGraph 或自研有限状态机 |
| 关系与元数据 | PostgreSQL |
| 向量索引 | pgvector |
| 中文关键词检索 | PostgreSQL FTS 预分词；效果不足时替换为 Elasticsearch/OpenSearch |
| 对象存储 | 本地文件系统；部署阶段使用 MinIO/S3 兼容存储 |
| 缓存与任务 | Redis + Celery/RQ，或轻量任务队列 |
| 文档解析 | Docling、Marker、LibreOffice、OCR、复杂表格与图文页面增强 |
| 视觉检索 | ColPali，作为知识入库基础组件，按复杂页面选择性建立索引 |
| 前端 | React/Vue + 文档预览组件 |
| 可观测性 | OpenTelemetry、结构化日志、Prompt/检索 Trace |
| 部署 | Docker Compose 起步 |

### 14.2 模型网关

系统通过统一模型网关调用：

- Chat/Reasoning Model；
- Embedding Model；
- Reranker；
- OCR/VLM；
- ColPali 类视觉检索模型。

网关负责：

- 本地与云端模型切换；
- 数据分级校验；
- 超时、重试和熔断；
- Token 和成本统计；
- Prompt 版本管理；
- 输出 Schema 校验；
- 敏感字段脱敏；
- 日志内容最小化。

---

## 15. 安全与隐私设计

### 15.1 数据分级

```text
Public       公开材料
Internal     内部一般材料
Sensitive    合同、财务、个人信息等敏感材料
Restricted   特别受限材料
```

### 15.2 安全要求

- 原始公文目录默认不纳入 Git；
- 公开仓库仅使用虚构或彻底脱敏材料；
- 云解析、云模型默认关闭；
- 上传文件进行病毒、宏和嵌入对象检查；
- 数据库、对象存储和备份加密；
- 查询前进行权限过滤，而不是生成答案后再过滤；
- Evidence 不得指向用户无权查看的文档；
- 日志不记录完整敏感正文；
- 导出文件写入审计信息但不泄露内部路径；
- 支持按事项、部门、文档密级实施 RBAC/ABAC；
- 支持数据删除、索引删除和缓存失效联动；
- 将检索文档内容视为不可信数据，防止文档内 Prompt Injection。

---

## 16. 质量评估

### 16.1 文档解析评估

- OCR CER；
- 标题和章节识别 F1；
- 阅读顺序准确率；
- 表格单元格识别准确率；
- 数字、日期、金额和文号准确率；
- 页面定位准确率；
- 单页解析耗时；
- 解析失败率；
- 人工复核率。

### 16.2 检索评估

- Case Recall@5；
- Chunk Recall@10；
- MRR@10；
- nDCG@10；
- 最终版本选择准确率；
- 旧版本误召回率；
- 复函完整召回率；
- 引用页码准确率。

### 16.3 问答评估

- Answer Correctness；
- Faithfulness；
- Citation Precision；
- Citation Coverage；
- 拒答准确率；
- 冲突发现率；
- 数字事实准确率；
- 用户权限越界率，目标必须为 0。

### 16.4 公文撰写评估

- 必要结构完整率；
- 关键事实一致率；
- 引用支持率；
- 未验证事实产生率；
- 人工修改距离；
- 审核人员可采纳率；
- 单份初稿耗时。

### 16.5 公文审核评估

- 问题检出 Precision/Recall/F1；
- Critical/Major 问题漏检率；
- 误报率；
- 建议可采纳率；
- 问题位置定位准确率；
- 修改后引入新问题的比例。

### 16.6 评估集构建

可从现有材料构建：

1. “旧版—以此为准”版本识别集；
2. “部门复函—意见汇总”意见归纳集；
3. “主文—附件”事实一致性集；
4. “相似主题跨年度事项”检索集；
5. 扫描件、表格、呈批表解析集；
6. 人工注入日期、金额、单位名称错误的审核集；
7. 权限越界与 Prompt Injection 安全集。

评估集必须与开发索引分离，避免测试答案直接泄漏到 Prompt 或 Few-shot 示例中。

---

## 17. 异常处理与降级策略

| 异常 | 系统行为 |
| --- | --- |
| 文件损坏 | 标记失败并进入人工队列，不入有效索引 |
| OCR 质量低 | 升级解析路线或要求人工确认 |
| 检索无结果 | 返回证据不足，建议用户调整范围 |
| 多版本冲突 | 优先权威版并提示冲突，不静默合并 |
| 数字来源冲突 | 阻止标记为已验证，要求用户确认 |
| 模型输出不符合 Schema | 自动重试一次，仍失败则终止节点 |
| 模型超时 | 保留状态，允许从检查点继续 |
| 云服务不可用 | 切换本地模型或进入排队状态 |
| 用户无权限 | 不返回答案、标题、摘要或证据片段 |
| DOCX 导出失败 | 保留草稿与验证结果，允许重新导出 |

---

## 18. 建议的实施路线

虽然产品展示顺序是“撰写、审核、问答”，工程实施建议先完成知识底座和问答，再开发审核和撰写。

### M0：数据治理与基准集

- 建立数据清单；
- 排除临时文件；
- 完成 SHA-256 去重；
- 选择解析基准页；
- 确定脱敏和 Git 隔离策略；
- 建立最小评估集。

### M1：多模态文档解析与案件建模

- DOC/DOCX/WPS/PDF 解析；
- OCR 路由；
- 统一 JSON；
- 页面类型与复杂度识别；
- 复杂表格的单元格 JSON、HTML、文本序列化和区域截图；
- 混合图文页面的 VLM 增强；
- ColPali 页面视觉多向量和视觉索引；
- 文本、表格与视觉表示的统一 `page_id` 对齐；
- 事项、文档角色和版本识别；
- 页面、段落和表格定位；
- 管理端校正页面。

### M2：信息检索问答 MVP

- 案件级索引；
- BM25 + Dense Retrieval；
- Metadata Filter；
- Reranker；
- Evidence Bundle；
- 引用式回答和拒答；
- 检索评估。

### M3：公文审核 MVP

- 规则引擎；
- 事实和实体抽取；
- 正文与附件一致性检查；
- 版本检查；
- 审核报告；
- 人工反馈闭环。

### M4：公文撰写 MVP

- 需求表单；
- 相似案例与模板检索；
- 提纲生成；
- 分节生成；
- 事实与引用校验；
- 人工确认；
- DOCX 导出。

### M5：评估与性能优化

- 完整离线评估报告；
- 文本与视觉混合检索性能优化；
- 解析、OCR、VLM 和视觉索引成本优化；
- 架构说明与性能指标文档。

---

## 19. 建议的仓库结构

```text
DocFlowAI/
├── apps/
│   ├── api/                 FastAPI 服务
│   └── web/                 前端应用
├── docflow/
│   ├── agents/              三类工作流
│   ├── parsers/             文档解析与路由
│   ├── ingestion/           入库任务
│   ├── retrieval/           混合检索与 Rerank
│   ├── knowledge/           案件、实体与关系
│   ├── review/              审核规则和审核器
│   ├── drafting/            提纲、生成与导出
│   ├── citations/           引用构建与校验
│   ├── security/            权限、脱敏与审计
│   └── models/              模型网关
├── migrations/              数据库迁移
├── evals/
│   ├── datasets/            仅保存脱敏评估数据
│   ├── parser/
│   ├── retrieval/
│   ├── qa/
│   ├── review/
│   └── drafting/
├── tests/
├── docs/
├── data/
│   ├── synthetic/           可公开模拟数据
│   └── README.md
├── scripts/
├── docker-compose.yml
├── .env.example
└── README.md
```

原始公文目录不应作为未来 Git 仓库的一部分。正式初始化仓库前应先编写严格的 `.gitignore`，并考虑将当前原始材料移至仓库之外的受控数据目录。

---

## 20. MVP 验收标准

### 20.1 知识库

- 能正确识别并展示一个事项的主文、附件、复函和意见汇总；
- 能区分权威版、旧版和不可用文件；
- 能从扫描 PDF 中提取可检索文本；
- 能将复杂表格解析为结构化单元格并保留表格区域截图；
- 能为复杂表格、表单和混合图文页面建立 ColPali 视觉索引；
- 能通过同一 `page_id` 在结构化文本、页面图片和视觉向量间回源；
- 能从引用跳转到原始页面；
- 删除文档后，元数据、索引、缓存和页面图片同步失效。

### 20.2 公文撰写

- 支持至少“请示”和“函”两种文种；
- 能展示参考案例和证据；
- 缺失关键事实时不会自动补全；
- 初稿中的关键数字和日期可追溯；
- 支持人工确认后导出 DOCX。

### 20.3 公文审核

- 能定位结构、日期格式、附件序号和数字不一致问题；
- 审核意见包含位置、理由和证据；
- 不直接覆盖原文件；
- 用户可以逐条接受或拒绝建议；
- 审核报告可导出并保留审计记录。

### 20.4 信息检索问答

- 支持文号、主题、单位、年份和相似事项查询；
- 默认只使用有效权威版本；
- 回答包含文件、页码和原文证据；
- 证据不足时能够拒答；
- 无权限用户无法通过回答或引用获知受限内容。

---

## 21. 待决策事项

1. 首批支持的文种是否限定为“请示、函”。
2. 是否部署完全本地的大模型、Embedding 和 Reranker。
3. DOC/WPS 的统一转换方案及格式保真要求。
4. 中文 OCR 的主引擎和备选引擎。
5. PostgreSQL FTS 是否能够满足中文关键词检索，还是直接引入 Elasticsearch/OpenSearch。
6. ColPali 使用的具体模型、向量数据库和页面筛选标准。
7. 是否需要集成现有 OA、公文系统或组织架构数据。
8. 用户权限按部门、事项还是密级控制。
9. 公文格式规范来源及其有效版本管理方式。
10. DOCX 导出是复用已有公文模板，还是重新实现版式生成器。

---

## 22. 后续设计文档

后续建议依次补充：

1. 《文档解析与知识入库详细设计》；
2. 《案件级数据模型与数据库设计》；
3. 《混合检索与 Evidence Bundle 详细设计》；
4. 《公文撰写 Agent 工作流详细设计》；
5. 《公文审核规则与审核 Agent 详细设计》；
6. 《信息检索问答 Agent 详细设计》；
7. 《API 与前端交互设计》；
8. 《安全、权限与脱敏设计》；
9. 《离线评估集与指标设计》；
10. 《部署与运维设计》。

---

## 23. 项目能力概述

完成 MVP 后，项目可概括为：

> 面向企业公文检索、撰写和审核场景，设计并实现基于案件级知识组织的多模态 RAG Agent。构建覆盖 DOC、DOCX、WPS、PDF 和扫描件的自适应解析流程，通过文档角色识别、版本权威控制、BM25 与向量混合检索、Reranker、事实与引用校验，实现可追溯的公文撰写、审核及知识问答，并基于历史版本对和“复函—意见汇总”材料建立离线评估体系。
