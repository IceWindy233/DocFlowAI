from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from docflow.db.session import get_db
from docflow.domain.retrieval import RetrievalAnswerRequest, RetrievalSearchRequest
from docflow.services.retrieval import RetrievalContextError, answer, retrieval_options, search
from docflow.services.vector_index import VisualIndexError

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.get("/options")
def get_retrieval_options(db: Session = Depends(get_db)):
    try:
        return retrieval_options(db)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/search")
def retrieval_search(payload: RetrievalSearchRequest, db: Session = Depends(get_db)):
    try:
        return search(db, payload)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VisualIndexError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/answer")
def retrieval_answer(payload: RetrievalAnswerRequest, db: Session = Depends(get_db)):
    try:
        return answer(db, payload)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VisualIndexError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
