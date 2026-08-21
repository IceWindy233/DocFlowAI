from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from docflow.db.session import get_db
from docflow.domain.qa_evaluation import (
    QaEvaluationGenerateRequest,
    QaEvaluationRunRequest,
    QaEvaluationSampleUpdate,
)
from docflow.services.qa_evaluation import (
    generate_samples,
    list_runs,
    list_samples,
    run_evaluation,
    update_sample,
)
from docflow.services.retrieval import RetrievalContextError

router = APIRouter(prefix="/qa-evaluations", tags=["qa-evaluations"])


@router.get("/samples")
def get_samples(db: Session = Depends(get_db)):
    try:
        return list_samples(db)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/samples/generate")
def create_samples(
    payload: QaEvaluationGenerateRequest,
    db: Session = Depends(get_db),
):
    try:
        return generate_samples(db, payload.target_count, payload.replace)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/samples/{sample_id}")
def patch_sample(
    sample_id: str,
    payload: QaEvaluationSampleUpdate,
    db: Session = Depends(get_db),
):
    try:
        return update_sample(db, sample_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs")
def create_run(payload: QaEvaluationRunRequest, db: Session = Depends(get_db)):
    try:
        return run_evaluation(db, payload.mode, payload.sample_ids)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs")
def get_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_runs(db, limit)
