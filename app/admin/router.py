"""Routeur FastAPI pour l'interface d'administration web.

Pages :
  GET  /admin/              → tableau de bord
  GET  /admin/login         → formulaire de connexion
  POST /admin/login         → traitement du login
  GET  /admin/logout        → déconnexion
  GET  /admin/applications  → liste des candidatures (toutes universités)
  GET  /admin/applications/{id}  → détail d'une candidature
  POST /admin/applications/{id}/decision → enregistrer une décision
  GET  /admin/universities  → liste des universités
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin.auth import (
    COOKIE_NAME,
    check_credentials,
    create_session_cookie,
    get_admin_user,
)
from app.database import get_db
from app.models.application import Application, ApplicationStatus
from app.models.document import Document
from app.models.university import University
from app.models.webhook_log import WebhookDelivery

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


# ─── Helpers ───────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "COLLECTING": "blue",
    "VALIDATING": "yellow",
    "VALIDATED": "indigo",
    "SENT_TO_UNIVERSITY": "purple",
    "ACCEPTED": "green",
    "REJECTED": "red",
}

STATUS_LABELS = {
    "COLLECTING": "En collecte",
    "VALIDATING": "Validation IA",
    "VALIDATED": "Validé",
    "SENT_TO_UNIVERSITY": "Envoyé",
    "ACCEPTED": "Accepté",
    "REJECTED": "Refusé",
}

DOC_LABELS = {
    "DIPLOME": "Diplôme",
    "RELEVE_NOTES": "Relevé de notes",
    "CARTE_IDENTITE": "Carte d'identité",
    "PHOTO": "Photo",
    "AUTRE": "Autre",
}


def _base_ctx(request: Request, username: str, **extra) -> dict:
    return {"request": request, "admin_user": username,
            "status_colors": STATUS_COLORS, "status_labels": STATUS_LABELS,
            "doc_labels": DOC_LABELS, **extra}


# ─── Login / Logout ────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if check_credentials(username, password):
        response = RedirectResponse(url="/admin/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            COOKIE_NAME,
            create_session_cookie(username),
            httponly=True,
            samesite="lax",
            max_age=8 * 3600,
        )
        return response
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "error": "Identifiants incorrects"},
        status_code=401,
    )


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


# ─── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: str = Depends(get_admin_user),
):
    # Compteurs par statut
    status_counts = dict(
        db.execute(
            select(Application.status, func.count(Application.id))
            .group_by(Application.status)
        ).all()
    )

    total = sum(status_counts.values())
    universities_count = db.execute(
        select(func.count(University.id)).where(University.is_active == True)  # noqa: E712
    ).scalar_one()

    # 10 candidatures les plus récentes
    recent = db.execute(
        select(Application, University.name.label("univ_name"))
        .join(University, Application.university_id == University.id)
        .order_by(Application.created_at.desc())
        .limit(10)
    ).all()

    # Webhooks échoués
    failed_webhooks = db.execute(
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.status == "FAILED"
        )
    ).scalar_one()

    ctx = _base_ctx(
        request, username,
        total=total,
        status_counts=status_counts,
        universities_count=universities_count,
        recent=recent,
        failed_webhooks=failed_webhooks,
        page_title="Tableau de bord",
    )
    return templates.TemplateResponse("admin/dashboard.html", ctx)


# ─── Liste des candidatures ────────────────────────────────────────────────

@router.get("/applications", response_class=HTMLResponse, include_in_schema=False)
async def applications_list(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: str = Depends(get_admin_user),
    status_filter: str = "",
    university_id: str = "",
    search: str = "",
    page: int = 1,
):
    per_page = 20
    query = (
        select(Application, University.name.label("univ_name"))
        .join(University, Application.university_id == University.id)
    )

    if status_filter:
        query = query.where(Application.status == status_filter)
    if university_id:
        try:
            query = query.where(Application.university_id == uuid.UUID(university_id))
        except ValueError:
            pass
    if search:
        query = query.where(
            Application.student_name.ilike(f"%{search}%")
            | Application.student_phone.ilike(f"%{search}%")
            | Application.program.ilike(f"%{search}%")
        )

    total = db.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar_one()

    rows = db.execute(
        query.order_by(Application.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()

    universities = db.execute(
        select(University).where(University.is_active == True).order_by(University.name)  # noqa: E712
    ).scalars().all()

    ctx = _base_ctx(
        request, username,
        rows=rows,
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
        status_filter=status_filter,
        university_id=university_id,
        search=search,
        universities=universities,
        all_statuses=list(ApplicationStatus),
        page_title="Candidatures",
    )
    return templates.TemplateResponse("admin/applications.html", ctx)


# ─── Détail d'une candidature ──────────────────────────────────────────────

@router.get("/applications/{application_id}", response_class=HTMLResponse, include_in_schema=False)
async def application_detail(
    request: Request,
    application_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    username: str = Depends(get_admin_user),
):
    row = db.execute(
        select(Application, University.name.label("univ_name"), University.email.label("univ_email"))
        .join(University, Application.university_id == University.id)
        .where(Application.id == application_id)
    ).first()

    if not row:
        return RedirectResponse("/admin/applications", status_code=303)

    application, univ_name, univ_email = row

    documents = db.execute(
        select(Document).where(Document.application_id == application_id)
    ).scalars().all()

    deliveries = db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.application_id == application_id)
        .order_by(WebhookDelivery.created_at.desc())
    ).scalars().all()

    ctx = _base_ctx(
        request, username,
        application=application,
        univ_name=univ_name,
        univ_email=univ_email,
        documents=documents,
        deliveries=deliveries,
        page_title=f"Candidature — {application.student_name or application.student_phone}",
        can_decide=application.status not in (ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED),
    )
    return templates.TemplateResponse("admin/application_detail.html", ctx)


# ─── Enregistrer une décision ──────────────────────────────────────────────

@router.post("/applications/{application_id}/decision", include_in_schema=False)
async def admin_decision(
    application_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    username: str = Depends(get_admin_user),
    decision: str = Form(...),
    comment: str = Form(""),
):
    application = db.get(Application, application_id)
    if application and application.status not in (
        ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED
    ):
        application.status = ApplicationStatus(decision)
        application.decision_comment = comment or None
        application.decided_at = datetime.now(timezone.utc)
        db.add(application)
        db.commit()

    return RedirectResponse(
        f"/admin/applications/{application_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ─── Liste des universités ─────────────────────────────────────────────────

@router.get("/universities", response_class=HTMLResponse, include_in_schema=False)
async def universities_list(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: str = Depends(get_admin_user),
):
    rows = db.execute(
        select(
            University,
            func.count(Application.id).label("app_count"),
        )
        .outerjoin(Application, Application.university_id == University.id)
        .group_by(University.id)
        .order_by(University.name)
    ).all()

    ctx = _base_ctx(request, username, rows=rows, page_title="Universités")
    return templates.TemplateResponse("admin/universities.html", ctx)
