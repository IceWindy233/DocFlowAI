from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import ConfigVersion, DocumentReview, DraftTask, WorkflowRun
from docflow.db.session import get_db
from docflow.domain.config import RuntimeConfigBundleV1

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _cost_from_usage(db: Session, run: WorkflowRun) -> dict:
    usage = dict((run.state_json or {}).get("cloud_usage") or {})
    if usage.get("estimated_cost_cny") is None:
        usage["estimated_cost_cny"] = 0.0
    usage["pricing_configured"] = False
    if not run.config_version_id:
        return usage
    version = db.get(ConfigVersion, run.config_version_id)
    if not version:
        return usage
    config = RuntimeConfigBundleV1.model_validate(version.content)
    signature = str((run.state_json or {}).get("model_signature") or "")
    profile = next((item for item in config.models if item.model_signature == signature), None)
    if profile:
        usage["pricing_configured"] = bool(
            profile.price_input_per_million or profile.price_output_per_million
        )
    if profile and not usage.get("estimated_cost_cny"):
        usage["estimated_cost_cny"] = round(
            (
                int(usage.get("input_tokens", 0)) * profile.price_input_per_million
                + int(usage.get("output_tokens", 0)) * profile.price_output_per_million
            )
            / 1_000_000,
            6,
        )
    return usage


def _serialize(db: Session, run: WorkflowRun, include_payload: bool = False) -> dict:
    value = {
        "id": run.id,
        "workflow_type": run.workflow_type,
        "status": run.status,
        "config_version_id": run.config_version_id,
        "index_generation_id": run.index_generation_id,
        "engine": run.engine,
        "engine_version": run.engine_version,
        "trace": run.trace_json,
        "input": run.input_json,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cloud_usage": _cost_from_usage(db, run),
        "model_signature": (run.state_json or {}).get("model_signature"),
    }
    if include_payload:
        value.update(
            {
                "state": run.state_json,
                "output": run.output_json,
            }
        )
    return value


@router.get("/runs")
def list_workflow_runs(
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    runs = db.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit))
    return [_serialize(db, run) for run in runs]


@router.get("/runs/{run_id}")
def get_workflow_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流运行记录不存在")
    return _serialize(db, run, include_payload=True)


@router.get("/targets/{target_type}/{target_id}")
def get_target_workflow_runs(
    target_type: str,
    target_id: str,
    db: Session = Depends(get_db),
):
    target_type = target_type.upper()
    if target_type == "REVIEW":
        target = db.get(DocumentReview, target_id)
    elif target_type == "DRAFT":
        target = db.get(DraftTask, target_id)
    else:
        raise HTTPException(status_code=422, detail="不支持的工作流目标类型")
    if not target:
        raise HTTPException(status_code=404, detail="工作流目标不存在")
    key = f"{target_type.lower()}_id"
    candidates = db.scalars(
        select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(500)
    )
    runs = [run for run in candidates if (run.input_json or {}).get(key) == target_id]
    return [_serialize(db, run, include_payload=True) for run in runs]
