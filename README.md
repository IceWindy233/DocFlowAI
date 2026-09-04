# DocFlow AI

面向中文公文的多模态 RAG 与 Agent 工作台：把分散的 Word、PDF、表格和扫描件转化为可追溯知识库，并提供公文问答、审核和撰写能力。

> 已完成 M0–M1 知识底座，以及 M2 问答、M3 审核、M4 撰写 MVP。

## 项目亮点

- **智能文档路由**：DOC/DOCX/WPS/PDF/XLS/XLSX/图片按格式和页面质量进入 LibreOffice、Docling、本地 OCR 或可选 VLM。
- **结构与视觉双路入库**：正文、表格和标题结构化为统一 JSON/Markdown；复杂页面同时生成截图与 ColPali 多向量。
- **混合检索**：融合中文词法检索、文本向量召回和 ColPali 视觉召回，通过 RRF、可选 Reranker 与文档多样化选择证据。
- **三类 LangGraph Agent**：知识问答、公文审核、公文撰写均有独立状态图、人工门禁、引用校验和失败降级。
- **可复现配置与发布**：任务固定不可变配置快照；高影响变更创建影子索引，完整性校验通过后原子切换 Publication。
- **安全与可观测性**：密钥只读环境变量；工作流记录节点耗时、模型签名、Token、费用状态、配置版本和索引代际。
- **固定评测闭环**：公开仓库提供 19 条全虚构样例，覆盖问答、审核和撰写，可分别运行零云费用基线与完整模型链路；私有语料评测集不随仓库发布。

## 当前完成度

| 阶段 | 状态 | 主要交付 |
| --- | --- | --- |
| M0 数据治理 | 已完成 | 2,570 个文件全量盘点、SHA-256 去重、格式识别、终态统计 |
| M1 解析入库 | 已完成 | 多格式解析、OCR/VLM 路由、复杂表格、ColPali、文本/视觉索引、Publication |
| M2 知识问答 | MVP 已完成 | 混合检索、证据充分性判断、对话模型/本地抽取、页级引用校验 |
| M3 公文审核 | MVP 已完成 | 规则审核、语义审核、参考证据、意见合并、人工接受/驳回、DOCX 报告 |
| M4 公文撰写 | MVP 已完成 | 对话式需求理解、案例检索、文种自适应结构、初稿与事实核验、版本历史、DOCX 导出 |

## 系统架构

~~~mermaid
flowchart LR
    U["业务用户"] --> PORTAL["React 用户工作台<br/>问答 / 审核 / 撰写"]
    A["管理员"] --> ADMIN["React 管理中心<br/>入库 / 发布 / 配置 / 评测"]
    PORTAL --> API["FastAPI API"]
    ADMIN --> API

    API --> CFG["配置中心<br/>不可变快照 / 影响分析"]
    API --> WF["LangGraph Agent<br/>问答 / 审核 / 撰写"]
    API --> TASK["Celery 任务编排"]
    TASK <--> REDIS[("Redis")]

    TASK --> PARSE["解析与质量路由<br/>LibreOffice / Docling / OCR / VLM"]
    PARSE --> ART[("本地产物存储<br/>JSON / Markdown / HTML / 页面图")]
    PARSE --> PG[("PostgreSQL<br/>元数据 / 页面 / Chunk / 轨迹")]
    PARSE --> QD[("Qdrant<br/>文本向量 / ColPali 多向量")]

    WF --> RET["混合检索<br/>词法 + 文本向量 + 视觉向量"]
    RET --> PG
    RET --> QD
    WF --> CLOUD["可选云模型<br/>百炼 Embedding / Reranker / VLM<br/>OpenAI 兼容对话模型"]
~~~

完整的离线入库、在线检索、Agent 状态图和配置发布关系见
[系统架构图](docs/系统架构图.md)。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| Web | React、TypeScript、Vite |
| API 与数据模型 | FastAPI、Pydantic 2、SQLAlchemy、Alembic |
| 工作流与任务 | LangGraph、LangChain Document、Celery、Redis |
| 元数据与向量 | PostgreSQL 17、Qdrant |
| 文档解析 | LibreOffice、Docling、pdfplumber、python-docx、openpyxl |
| OCR 与视觉 | RapidOCR / PP-OCRv6、Tesseract、ColQwen2.5 / ColPali |
| 云模型适配 | 百炼 OpenAI 兼容、通用 OpenAI 兼容 API |
| 工程化 | uv、pnpm、Docker Compose、Pytest、Ruff、ESLint |

## 快速启动

### 1. 环境要求

- Python 3.11 或 3.12；
- [uv](https://docs.astral.sh/uv/)；
- Node.js 与 pnpm；
- Docker Desktop；
- LibreOffice（处理 DOC/WPS/XLS 等旧格式时需要）。

### 2. 启动基础设施

~~~bash
cp .env.example .env
make infra
~~~

Docker Compose 会在本机启动 PostgreSQL、Redis 和 Qdrant，端口只绑定到 <code>127.0.0.1</code>。

### 3. 初始化后端

~~~bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
cd ..
make backend
~~~

API 地址：<code>http://127.0.0.1:8000</code>
健康检查：<code>http://127.0.0.1:8000/api/v1/health</code>
OpenAPI：<code>http://127.0.0.1:8000/docs</code>

### 4. 启动 Worker

新开终端：

~~~bash
make worker
~~~

macOS 上默认使用 <code>solo</code> 池和单并发，避免多个子进程重复加载 OCR、Docling 与 ColPali 模型。

### 5. 启动 Web

~~~bash
cd frontend
pnpm install
cd ..
make frontend
~~~

访问：

- 用户工作台：<code>http://127.0.0.1:5173</code>；
- 管理中心：<code>http://127.0.0.1:5173/admin</code>。

用户工作台只提供知识问答、公文审核和公文撰写；入库、文档校正、Publication、配置和固定评测
集中在管理中心。详细操作见 [用户侧使用说明](docs/用户侧使用说明.md)。

### 6. 安装本地 ML 能力

如需运行 Docling、RapidOCR 与 ColPali：

~~~bash
cd backend
uv sync --extra dev --extra ml
~~~

当前 macOS/MPS 环境固定 Torch 2.5.1；ColQwen2.5 不兼容时可在配置中心切换到 ColQwen2。

## 云模型配置

默认配置关闭云模型，本地解析、词法检索、规则审核和需求门禁仍可运行。需要完整链路时：

1. 在根目录 <code>.env</code> 设置 <code>DASHSCOPE_API_KEY</code> 和 <code>CHAT_LLM_API_KEY</code>；
2. 重启后端与 Worker；
3. 在“配置中心 → 模型档案”填写百炼 Workspace ID，并执行模型探测；
4. 配置模型输入/输出单价；保持 0 时系统显示“单价未配置”，不会误报为免费；
5. 在“能力路由”启用需要的模型并保存新配置版本。

推荐职责：

| 能力 | 默认方案 | 是否必须 |
| --- | --- | --- |
| 文本 Embedding | 百炼 <code>qwen3.7-text-embedding</code>，2560 维 | 可选；启用后需重建文本索引 |
| Reranker | 百炼 <code>qwen3-rerank</code> | 可选；失败自动保留 RRF 排序 |
| RAG/审核/撰写生成 | 通用 OpenAI 兼容对话模型（出厂默认示例：硅基流动 <code>deepseek-ai/DeepSeek-V4-Flash</code>） | 完整 Agent 链路需要 |
| 复杂页面增强 | 百炼 VLM | 可选；本地视觉索引不依赖它 |
| 视觉检索 | 本地 ColQwen2.5 / ColPali | 复杂页面检索需要 |

内容生成模型通过通用 `openai_compatible` 适配器接入；配置中心“云端对话模型（OpenAI 兼容）”档案统一描述
Base URL、模型名、密钥变量 `CHAT_LLM_API_KEY` 及安全的标量扩展参数，因此切换其他 OpenAI 兼容服务只需
修改档案内容，无需改动模型网关，也无需改动密钥变量名。

详细说明见 [云模型接入说明](docs/云模型接入说明.md)。

## 推荐演示流程

1. **导入公开样例**：首次体验可直接选择 `examples/demo-corpus`，其中全部内容均为虚构数据。
2. **配置模型与阈值**：在配置中心检查 Parser、OCR、Embedding、Reranker、ColPali 和生成模型。
3. **选择数据源**：通过系统目录选择器一次添加一个或多个目录。
4. **创建入库任务**：先用普通文档做小样本解析，再按需要加入扫描件、复杂表格和混合图文。
5. **检查解析结果**：在文档中心对照原文、页面图、Markdown/JSON 和表格结构。
6. **处理人工审核**：修正低质量页面、角色、案件、版本和权威性。
7. **发布索引**：完整性校验通过后切换当前 Publication。
8. **体验三类 Agent**：
   - 知识问答：查看混合召回、答案、页码引用和节点轨迹；
   - 公文审核：查看规则/模型意见、证据和自动修改；
   - 公文撰写：用对话描述事项，确认内容顺序，生成初稿并执行事实复验。简单事项使用连续正文，多子项或分阶段事项才显式分节。
9. **运行固定评测**：先跑公开样例的本地基线，再按需选择少量样例运行完整云模型链路。

## 评测与验证

公开脱敏评测集包含：

- 9 条知识问答：日期、多数字事实、条件集合、列表、跨材料汇总和安全拒答；
- 5 条公文审核：敏感信息、格式、结构、事实一致性和无问题对照；
- 5 条公文撰写：事实覆盖、证据隔离、需求门禁和无依据事实拦截。

仓库不附带任何真实语料、数据库或运行结果。README 中涉及的完成度与规模是开发阶段的聚合记录，
用于说明系统经过的链路验证，不构成公开数据集。克隆后请使用 `examples/demo-corpus` 建立自己的
Publication，运行结果取决于本机环境、模型配置和索引代际。

最近一次本地工程回归包含 118 条后端测试，前端通过 TypeScript 生产构建。执行完整验证：

~~~bash
make test
~~~

或分别运行：

~~~bash
cd backend
uv run ruff check src tests
uv run pytest

cd ../frontend
pnpm lint
pnpm build
~~~

准备公开仓库前可额外执行：

~~~bash
./scripts/check-public-repo.sh
~~~

该检查会拒绝跟踪本地数据目录、疑似密钥、本机绝对路径和超过 20 MiB 的文件。

评测集定义、样例选择和计分逻辑见 [Agent 固定评测说明](evaluation/README.md)。

## 本地开发阶段的 M0 语料盘点

| 状态 | 数量 |
| --- | ---: |
| 可进入解析 | 2,005 |
| 精确重复 | 352 |
| 临时文件 | 189 |
| 不支持格式或容器 | 24 |
| 合计 | **2,570** |

以上仅保留聚合数量，原始文件、文件名、来源路径、案件关系和解析产物均不会进入公开仓库。
同一 SHA-256 内容只执行一次重型解析，但保留全部来源关系。Golden Set 与 Benchmark 作为可选
质量工具保留，不阻塞入库流程。

## 配置与数据安全

- API Key 只通过环境变量注入，数据库仅保存环境变量名称；
- 配置导出、日志、异常堆栈和 Web API 不回显密钥值；
- 每个任务固定 <code>config_version_id</code>、模型签名和 <code>index_generation_id</code>；
- Embedding 维度、Chunk 或 ColPali 模型变化会触发影子重建，禁止污染当前索引；
- 原始语料保持只读；数据库、模型缓存、解析产物和页面图片不应提交 Git；
- 云端 VLM 或 Embedding 任务必须显式授权，并受调用次数、Token 和费用预算约束。

### 公开仓库数据边界

- `examples/demo-corpus` 与公开固定评测集均为虚构内容，可用于本地演示；
- 原始语料目录、`data/`、`backend/data/`、数据库、页面图、OCR/Markdown/JSON 产物和模型权重均被 Git 忽略；
- `.env` 不提交，`.env.example` 只保留变量名称和本地开发默认值；
- 与真实 Publication 绑定的固定评测集保留在本机，不进入公开仓库；
- 导入自有文档前，应确认拥有处理和上传权限，并根据组织要求完成脱敏。

## 仓库结构

~~~text
DocFlowAI/
├── backend/                 FastAPI、Celery、LangGraph、解析与检索
│   ├── src/docflow/
│   ├── alembic/
│   └── tests/
├── frontend/                React 用户工作台与管理中心
├── infra/                   PostgreSQL、Redis、Qdrant
├── docs/                    设计、架构与运行文档
├── evaluation/              固定 Agent 评测集
├── examples/demo-corpus/    全虚构的公开演示语料
├── scripts/                 辅助脚本
├── Makefile
└── .env.example
~~~

## 项目边界

当前版本面向本地单机部署，不包含多租户 RBAC、分布式对象存储、生产级灾备与大规模
压测。系统优先保证可解释的文档路由、双路知识入库、可追溯 Agent 工作流和配置版本治理。

## 开源许可证

本项目采用 [MIT License](LICENSE)。公开演示语料同样随仓库按 MIT License 分发；你自行导入的
第三方文档、模型权重和云服务不因本项目许可证而获得额外授权。

## 相关文档

- [系统架构图](docs/系统架构图.md)
- [系统设计文档](docs/DocFlow-AI-系统设计文档.md)
- [文档解析与知识入库详细设计](docs/文档解析与知识入库详细设计.md)
- [云模型接入说明](docs/云模型接入说明.md)
- [公文文体基线说明](docs/公文文体基线说明.md)
- [Agent 固定评测说明](evaluation/README.md)
- [安全与数据披露说明](SECURITY.md)
