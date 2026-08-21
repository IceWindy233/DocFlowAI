from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from docflow.core.settings import get_settings
from docflow.db.session import engine

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, object] = {}
    try:
        with Session(engine) as db:
            db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}
    try:
        response = httpx.get(f"{settings.qdrant_url}/healthz", timeout=2)
        checks["qdrant"] = {"ok": response.is_success}
    except Exception:
        checks["qdrant"] = {"ok": False}
    checks["artifact_store"] = {
        "ok": settings.artifact_root.exists() and settings.artifact_root.is_dir()
    }
    return {
        "status": "ok" if checks["database"]["ok"] else "degraded",
        "service": settings.app_name,
        "environment": settings.env,
        "checks": checks,
    }


@router.get("/artifacts/{artifact_path:path}")
def artifact(artifact_path: str):
    from fastapi.responses import FileResponse

    root = get_settings().artifact_root.expanduser().resolve()
    path = (root / artifact_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="产物不存在")
    return FileResponse(path)
