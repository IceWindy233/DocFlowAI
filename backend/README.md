# DocFlow AI Backend

DocFlow AI 后端提供 M0 文件治理、M1 多模态解析与知识入库，以及 M2–M4 问答、审核、撰写
Agent API。完整项目介绍、架构和演示流程请阅读[根目录 README](../README.md)。

## 本地启动

~~~bash
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn docflow.main:app --reload --host 127.0.0.1 --port 8000
~~~

需要 Docling、RapidOCR 与 ColPali 时：

~~~bash
uv sync --extra dev --extra ml
~~~

使用 Celery 模式时另开终端启动：

~~~bash
uv run celery -A docflow.workers.celery_app:celery_app worker -l INFO --pool=solo --concurrency=1
~~~

健康检查：<code>GET /api/v1/health</code>
OpenAPI：<code>http://127.0.0.1:8000/docs</code>

## 验证

~~~bash
uv run ruff check src tests
uv run pytest
~~~

配置、模型密钥、索引发布与云端预算说明见：

- [系统架构图](../docs/系统架构图.md)
- [云模型接入说明](../docs/云模型接入说明.md)
