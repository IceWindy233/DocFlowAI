.PHONY: dev infra backend worker frontend test scan

SOURCE_ROOT ?= ../examples/demo-corpus

infra:
	docker compose -f infra/docker-compose.yml up -d postgres redis qdrant

backend:
	cd backend && uv run uvicorn docflow.main:app --reload --host 127.0.0.1 --port 8000

worker:
	cd backend && uv run celery -A docflow.workers.celery_app:celery_app worker -l INFO --pool=solo --concurrency=1

frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run ruff check src tests
	cd backend && uv run pytest
	cd frontend && pnpm lint
	cd frontend && pnpm build

scan:
	cd backend && uv run docflow scan --source-root "$(SOURCE_ROOT)"
