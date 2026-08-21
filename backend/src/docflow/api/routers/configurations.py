from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from docflow.db.session import get_db
from docflow.domain.config import (
    ConfigSaveRequest,
    ConfigValidateRequest,
    ModelProbeRequest,
    RuntimeConfigBundleV1,
)
from docflow.services.config_service import (
    ConfigConflictError,
    ConfigNotFoundError,
    ModelNotReadyError,
    calculate_impact,
    create_rebuild_job,
    get_current_config,
    list_versions,
    probe_model,
    rollback_config,
    save_config,
    secret_status,
    version_to_response,
)

router = APIRouter(prefix="/admin", tags=["configuration"])


@router.get("/configurations/schema")
def configuration_schema() -> dict:
    return RuntimeConfigBundleV1.model_json_schema()


@router.get("/configurations/current")
def current_configuration(db: Session = Depends(get_db)):
    return version_to_response(get_current_config(db))


@router.post("/configurations/validate")
def validate_configuration(payload: ConfigValidateRequest, db: Session = Depends(get_db)):
    current = get_current_config(db)
    old = RuntimeConfigBundleV1.model_validate(current.content)
    return {
        "valid": True,
        "content_hash": payload.config.content_hash(),
        "impact": calculate_impact(old, payload.config),
    }


@router.post("/configurations/impact-preview")
def impact_preview(payload: ConfigValidateRequest, db: Session = Depends(get_db)):
    current = get_current_config(db)
    return calculate_impact(RuntimeConfigBundleV1.model_validate(current.content), payload.config)


@router.put("/configurations/current")
def update_configuration(payload: ConfigSaveRequest, db: Session = Depends(get_db)):
    try:
        version = save_config(
            db,
            base_version_id=payload.base_version_id,
            config=payload.config,
            change_reason=payload.change_reason,
        )
        return version_to_response(version)
    except ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelNotReadyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/configurations/versions")
def configuration_versions(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return [version_to_response(version) for version in list_versions(db, limit)]


@router.post("/configurations/versions/{version_id}/rollback")
def rollback(version_id: str, db: Session = Depends(get_db)):
    try:
        return version_to_response(rollback_config(db, version_id))
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/configurations/current/rebuild")
def rebuild(db: Session = Depends(get_db)):
    return create_rebuild_job(db)


@router.post("/model-profiles/{profile_id}/probe")
def probe(
    profile_id: str,
    payload: ModelProbeRequest | None = None,
    db: Session = Depends(get_db),
):
    try:
        result = probe_model(db, profile_id, payload.profile if payload else None)
        return {
            "id": result.id,
            "profile_id": result.profile_id,
            "success": result.success,
            "latency_ms": result.latency_ms,
            "capability_details": result.capability_details,
            "error_message": result.error_message,
            "created_at": result.created_at,
        }
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelNotReadyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/model-profiles/{profile_id}/secret-status")
def profile_secret_status(profile_id: str, db: Session = Depends(get_db)):
    current = get_current_config(db)
    config = RuntimeConfigBundleV1.model_validate(current.content)
    try:
        return secret_status(config, profile_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
