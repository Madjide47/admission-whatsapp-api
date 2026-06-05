"""Endpoints CRUD pour les candidatures (Application)."""
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.api_key import get_current_university
from app.database import get_db
from app.models.application import Application, ApplicationStatus
from app.models.university import University
from app.schemas.application import (
    ApplicationCreate,
    ApplicationListItem,
    ApplicationRead,
    ApplicationUpdate,
)

router = APIRouter()


def _success(data) -> dict:
    return {"success": True, "data": data}


def _error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


# ----------------------------------------------------------------------
# POST /api/v1/applications — création manuelle
# ----------------------------------------------------------------------
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Créer une candidature manuellement",
)
def create_application(
    payload: ApplicationCreate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    application = Application(
        university_id=university.id,
        student_phone=payload.student_phone,
        student_name=payload.student_name,
        student_email=payload.student_email,
        program=payload.program,
        status=ApplicationStatus.COLLECTING_FIELDS,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return _success(ApplicationRead.model_validate(application).model_dump(mode="json"))


# ----------------------------------------------------------------------
# GET /api/v1/applications — liste avec filtres
# ----------------------------------------------------------------------
@router.get(
    "",
    summary="Lister les candidatures (filtres optionnels)",
)
def list_applications(
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: ApplicationStatus | None = Query(None, alias="status"),
    program: str | None = Query(None, max_length=255),
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    filters = [Application.university_id == university.id]
    if status_filter:
        filters.append(Application.status == status_filter)
    if program:
        filters.append(Application.program.ilike(f"%{program}%"))
    if created_after:
        filters.append(Application.created_at >= created_after)
    if created_before:
        filters.append(Application.created_at <= created_before)

    stmt = (
        select(Application)
        .where(and_(*filters))
        .order_by(Application.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).scalars().all()
    items = [ApplicationListItem.model_validate(r).model_dump(mode="json") for r in rows]
    return _success({"items": items, "limit": limit, "offset": offset, "count": len(items)})


# ----------------------------------------------------------------------
# GET /api/v1/applications/{id}
# ----------------------------------------------------------------------
@router.get(
    "/{application_id}",
    summary="Détail complet d'une candidature avec ses documents",
)
def get_application(
    application_id: uuid.UUID,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    stmt = (
        select(Application)
        .options(selectinload(Application.documents))
        .where(Application.id == application_id)
        .where(Application.university_id == university.id)
    )
    application = db.execute(stmt).scalar_one_or_none()
    if application is None:
        raise _error("APPLICATION_NOT_FOUND", "Candidature introuvable.", 404)
    return _success(ApplicationRead.model_validate(application).model_dump(mode="json"))


# ----------------------------------------------------------------------
# PATCH /api/v1/applications/{id}
# ----------------------------------------------------------------------
@router.patch(
    "/{application_id}",
    summary="Mettre à jour partiellement une candidature",
)
def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    application = db.execute(
        select(Application)
        .where(Application.id == application_id)
        .where(Application.university_id == university.id)
    ).scalar_one_or_none()
    if application is None:
        raise _error("APPLICATION_NOT_FOUND", "Candidature introuvable.", 404)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(application, field, value)
    db.add(application)
    db.commit()
    db.refresh(application)
    return _success(ApplicationRead.model_validate(application).model_dump(mode="json"))
