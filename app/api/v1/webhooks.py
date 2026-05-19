"""Endpoints de configuration et suivi des webhooks pour les universités."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_key import (
    generate_webhook_secret,
    get_current_university,
)
from app.database import get_db
from app.models.university import University
from app.models.webhook_log import WebhookDelivery, WebhookStatus
from app.schemas.webhook import (
    WebhookConfigCreatedSecret,
    WebhookConfigRead,
    WebhookConfigUpdate,
    WebhookDeliveryRead,
)

router = APIRouter()


# ----------------------------------------------------------------------
# GET /api/v1/webhooks — config courante
# ----------------------------------------------------------------------
@router.get("", summary="Consulter la configuration webhook")
def get_webhook_config(
    university: Annotated[University, Depends(get_current_university)],
) -> dict:
    return {
        "success": True,
        "data": WebhookConfigRead(
            webhook_url=university.webhook_url,
            has_secret=bool(university.webhook_secret),
        ).model_dump(mode="json"),
    }


# ----------------------------------------------------------------------
# PUT /api/v1/webhooks — mettre à jour URL / secret
# ----------------------------------------------------------------------
@router.put("", summary="Mettre à jour l'URL et/ou régénérer le secret webhook")
def update_webhook_config(
    payload: WebhookConfigUpdate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    university.webhook_url = str(payload.webhook_url)
    new_secret: str | None = None
    if payload.regenerate_secret or not university.webhook_secret:
        new_secret = generate_webhook_secret()
        university.webhook_secret = new_secret
    db.add(university)
    db.commit()
    db.refresh(university)

    if new_secret:
        # On retourne le secret en clair — affiché UNE SEULE FOIS
        return {
            "success": True,
            "data": WebhookConfigCreatedSecret(
                webhook_url=university.webhook_url,
                webhook_secret=new_secret,
            ).model_dump(mode="json"),
        }
    return {
        "success": True,
        "data": WebhookConfigRead(
            webhook_url=university.webhook_url,
            has_secret=bool(university.webhook_secret),
        ).model_dump(mode="json"),
    }


# ----------------------------------------------------------------------
# GET /api/v1/webhooks/deliveries — historique des livraisons
# ----------------------------------------------------------------------
@router.get("/deliveries", summary="Historique des tentatives de livraison")
def list_deliveries(
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: WebhookStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    filters = [WebhookDelivery.university_id == university.id]
    if status_filter:
        filters.append(WebhookDelivery.status == status_filter)

    stmt = (
        select(WebhookDelivery)
        .where(*filters)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).scalars().all()
    return {
        "success": True,
        "data": {
            "items": [
                WebhookDeliveryRead.model_validate(r).model_dump(mode="json") for r in rows
            ],
            "limit": limit,
            "offset": offset,
            "count": len(rows),
        },
    }
