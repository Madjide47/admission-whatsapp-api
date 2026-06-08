"""Endpoint POST /api/v1/applications/{id}/decision — décision de l'université."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_key import get_current_university
from app.database import get_db
from app.models.application import Application, ApplicationStatus
from app.models.university import University
from app.schemas.application import ApplicationDecisionRequest, ApplicationRead

router = APIRouter()

# Seules ces deux valeurs sont acceptées comme décision
ALLOWED_DECISIONS = {ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED}


def _error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


@router.post(
    "/{application_id}/decision",
    summary="Envoyer la décision finale (ACCEPT / REJECT)",
)
def submit_decision(
    application_id: uuid.UUID,
    payload: ApplicationDecisionRequest,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if payload.decision not in ALLOWED_DECISIONS:
        raise _error(
            "INVALID_DECISION",
            "La décision doit être ACCEPTED ou REJECTED.",
            422,
        )

    application = db.execute(
        select(Application)
        .where(Application.id == application_id)
        .where(Application.university_id == university.id)
    ).scalar_one_or_none()
    if application is None:
        raise _error("APPLICATION_NOT_FOUND", "Candidature introuvable.", 404)

    # Idempotence : si la même décision est déjà enregistrée, on retourne 200 sans rien refaire
    if application.status == payload.decision:
        return {
            "success": True,
            "data": {
                "idempotent": True,
                "application": ApplicationRead.model_validate(application).model_dump(mode="json"),
            },
        }

    if application.status in ALLOWED_DECISIONS:
        raise _error(
            "DECISION_ALREADY_TAKEN",
            f"Une décision a déjà été prise ({application.status.value}).",
            409,
        )

    application.status = payload.decision
    application.decision_comment = payload.comment
    application.decided_at = datetime.now(timezone.utc)
    db.add(application)
    db.commit()
    db.refresh(application)

    # Notification WhatsApp à l'étudiant + accusé webhook — tous deux asynchrones
    # avec retry (file « webhooks »). La notification n'est donc plus perdue si
    # Twilio échoue ponctuellement (quota 429, réseau) : elle est rejouée.
    from app.workers.webhook_tasks import (
        dispatch_decision_acknowledged_task,
        notify_student_decision_task,
    )

    notify_student_decision_task.delay(str(application.id))
    dispatch_decision_acknowledged_task.delay(str(application.id))

    return {
        "success": True,
        "data": ApplicationRead.model_validate(application).model_dump(mode="json"),
    }
