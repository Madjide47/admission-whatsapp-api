"""Endpoints du chatbot admin — POST /api/v1/admin/chat et historique.

L'orchestrateur (Gemini) traduit le langage naturel en appels d'outils ;
toutes les données sont scoping par l'université authentifiée.
"""
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_key import get_current_university
from app.database import get_db
from app.models.admin_chat import AdminChatSession
from app.models.university import University
from app.schemas.admin_chat import (
    AdminChatRequest,
    AdminChatResponse,
    AdminChatSessionRead,
)
from app.services.admin_chat import AdminChatOrchestrator, load_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Orchestrateur partagé (le client LLM est instancié paresseusement au 1er appel).
_orchestrator = AdminChatOrchestrator()


def _success(data) -> dict:
    return {"success": True, "data": data}


def _error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


@router.post("/chat", summary="Chatbot admin — piloter la base en langage naturel")
def admin_chat(
    payload: AdminChatRequest,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    try:
        result = _orchestrator.process(
            db,
            university,
            payload.message,
            session_id=str(payload.session_id) if payload.session_id else None,
            confirm_action=payload.confirm_action,
        )
    except Exception:
        logger.exception("Erreur chatbot admin")
        raise _error("CHAT_ERROR", "L'assistant a rencontré une erreur.", 500)

    # Persistance audit (best-effort, ne bloque jamais la réponse)
    try:
        _persist_audit(db, university, result)
    except Exception:
        logger.warning("Persistance audit chat échouée", exc_info=True)

    response = AdminChatResponse(
        session_id=result.session_id,
        message=result.message,
        data_table=result.data_table,
        pending_action=result.pending_action,
        tool_used=result.tool_used,
    )
    return _success(response.model_dump(mode="json"))


@router.get("/chat/history", summary="Historique des sessions de chat de l'université")
def admin_chat_history(
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 20,
) -> dict:
    limit = max(1, min(limit, 100))
    rows = db.execute(
        select(AdminChatSession)
        .where(AdminChatSession.university_id == university.id)
        .order_by(AdminChatSession.last_activity_at.desc())
        .limit(limit)
    ).scalars().all()
    return _success(
        [AdminChatSessionRead.model_validate(r).model_dump(mode="json") for r in rows]
    )


def _persist_audit(db: Session, university: University, result) -> None:
    """Sauvegarde la session de chat dans admin_chat_sessions (audit/débogage)."""
    session_state = load_session(result.session_id) or {}
    messages = session_state.get("history", [])

    try:
        row_id = uuid.UUID(result.session_id)
    except (ValueError, TypeError):
        return

    existing = db.get(AdminChatSession, row_id)
    if existing is None:
        existing = AdminChatSession(
            id=row_id,
            university_id=university.id,
            admin_identifier=university.name,
            messages=messages,
        )
        db.add(existing)
    else:
        existing.messages = messages
    db.commit()
