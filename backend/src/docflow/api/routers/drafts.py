from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from docflow.db.session import get_db
from docflow.domain.agents import (
    DraftCreateRequest,
    DraftInterpretRequest,
    DraftOutlineUpdate,
    DraftRegenerateRequest,
    DraftTextUpdate,
)
from docflow.services.draft_agent import (
    create_draft,
    draft_detail,
    export_draft,
    generate_draft,
    list_drafts,
    list_revisions,
    restore_revision,
    update_draft_text,
    update_outline,
)
from docflow.services.draft_conversation import interpret_draft_message
from docflow.services.retrieval import RetrievalContextError

router = APIRouter(prefix="/drafts", tags=["document-draft-agent"])


@router.post("/interpret")
def interpret(payload: DraftInterpretRequest, db: Session = Depends(get_db)):
    """Interpret one conversational turn into a confirmed requirement patch."""
    try:
        return interpret_draft_message(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("")
def create(payload: DraftCreateRequest, db: Session = Depends(get_db)):
    try:
        return create_draft(db, payload.requirements)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("")
def list_items(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return list_drafts(db, limit)


@router.get("/{draft_id}")
def detail(draft_id: str, db: Session = Depends(get_db)):
    try:
        return draft_detail(db, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{draft_id}/outline")
def approve_outline(
    draft_id: str,
    payload: DraftOutlineUpdate,
    db: Session = Depends(get_db),
):
    try:
        return update_outline(db, draft_id, payload.outline)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{draft_id}/generate")
def generate(draft_id: str, db: Session = Depends(get_db)):
    try:
        return generate_draft(db, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{draft_id}/regenerate")
def regenerate(
    draft_id: str,
    payload: DraftRegenerateRequest,
    db: Session = Depends(get_db),
):
    try:
        return generate_draft(
            db,
            draft_id,
            payload.mode,
            payload.section_id,
            payload.instruction,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{draft_id}/revisions")
def revisions(draft_id: str, db: Session = Depends(get_db)):
    try:
        return list_revisions(db, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{draft_id}/revisions/{revision_id}/restore")
def restore(draft_id: str, revision_id: str, db: Session = Depends(get_db)):
    try:
        return restore_revision(db, draft_id, revision_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{draft_id}/text")
def save_text(draft_id: str, payload: DraftTextUpdate, db: Session = Depends(get_db)):
    try:
        return update_draft_text(db, draft_id, payload.draft_text)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{draft_id}/export")
def export(draft_id: str, db: Session = Depends(get_db)):
    try:
        export_draft(db, draft_id)
        return draft_detail(db, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
