from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from docflow.api.routers import (
    agent_evaluations,
    configurations,
    document_reviews,
    documents,
    drafts,
    golden_set,
    jobs,
    publications,
    qa_evaluations,
    retrieval,
    reviews,
    system,
    workflows,
)
from docflow.core.logging import configure_logging
from docflow.core.settings import get_settings
from docflow.db.models import Base
from docflow.db.session import SessionLocal, engine
from docflow.services.config_service import ensure_default_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_default_config(db)
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="公文多模态解析、知识入库与运行配置中心",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(agent_evaluations.router, prefix=settings.api_prefix)
app.include_router(configurations.router, prefix=settings.api_prefix)
app.include_router(document_reviews.router, prefix=settings.api_prefix)
app.include_router(drafts.router, prefix=settings.api_prefix)
app.include_router(jobs.router, prefix=settings.api_prefix)
app.include_router(golden_set.router, prefix=settings.api_prefix)
app.include_router(reviews.router, prefix=settings.api_prefix)
app.include_router(documents.router, prefix=settings.api_prefix)
app.include_router(publications.router, prefix=settings.api_prefix)
app.include_router(qa_evaluations.router, prefix=settings.api_prefix)
app.include_router(retrieval.router, prefix=settings.api_prefix)
app.include_router(workflows.router, prefix=settings.api_prefix)
