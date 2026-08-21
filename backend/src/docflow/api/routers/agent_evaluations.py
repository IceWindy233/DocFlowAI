from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from docflow.db.session import get_db
from docflow.domain.agent_evaluation import AgentEvaluationRunRequest
from docflow.services.agent_evaluation import (
    fixed_catalog,
    list_agent_evaluation_runs,
    run_fixed_evaluation,
)
from docflow.services.retrieval import RetrievalContextError

router = APIRouter(prefix="/agent-evaluations", tags=["agent-evaluations"])


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    try:
        return fixed_catalog(db)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs")
def create_run(payload: AgentEvaluationRunRequest, db: Session = Depends(get_db)):
    try:
        return run_fixed_evaluation(db, payload)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs")
def runs(
    capability: str | None = Query(default=None, pattern="^(QA|REVIEW|DRAFT)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return list_agent_evaluation_runs(db, capability, limit)
