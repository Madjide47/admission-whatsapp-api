"""Tool Executor du chatbot admin — les 7 outils appelés par l'orchestrateur LLM.

Chaque fonction est pure (reçoit une Session + l'university_id) et renvoie un
dict JSON-sérialisable. TOUTES les requêtes sont filtrées par university_id :
une université ne peut jamais toucher les données d'une autre.

Outils de lecture : search_applications, get_application_detail, get_stats,
get_program_criteria.
Outils d'écriture (dry_run obligatoire d'abord) : set_program_criteria,
bulk_decide, send_whatsapp_notification.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.application import Application, ApplicationStatus
from app.models.document import Document
from app.models.form_field_value import ApplicationFieldValue
from app.models.program import FormField, Program
from app.models.program_criteria import ProgramCriteria

logger = logging.getLogger(__name__)

MAX_LIMIT = 100
DEFAULT_LIMIT = 20

# Statuts encore « décidables » (pas de décision finale déjà prise)
_DECIDABLE_STATUSES = {
    ApplicationStatus.VALIDATED,
    ApplicationStatus.PENDING_ENROLLMENT,
    ApplicationStatus.SENT_TO_UNIVERSITY,
}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _coerce_status(status: str | None) -> ApplicationStatus | None:
    if not status:
        return None
    try:
        return ApplicationStatus(str(status).upper())
    except ValueError:
        return None


def _filtered_query(
    university_id: uuid.UUID,
    *,
    program_name: str | None = None,
    status: str | None = None,
    min_score: float | None = None,
    min_average: float | None = None,
    days_since_last_update: int | None = None,
):
    """Construit le SELECT Application filtré (toujours scoping université)."""
    stmt = select(Application).where(Application.university_id == university_id)

    if program_name:
        stmt = stmt.where(Application.program.ilike(f"%{program_name}%"))
    status_enum = _coerce_status(status)
    if status_enum is not None:
        stmt = stmt.where(Application.status == status_enum)
    if min_score is not None:
        stmt = stmt.where(Application.validation_score >= min_score)
    if min_average is not None:
        stmt = stmt.where(Application.average >= min_average)
    if days_since_last_update is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_since_last_update)
        stmt = stmt.where(Application.updated_at <= cutoff)
    return stmt


def _serialize_application(app: Application) -> dict:
    return {
        "application_id": str(app.id),
        "student_name": app.student_name,
        "student_phone": app.student_phone,
        "program": app.program,
        "status": app.status.value if app.status else None,
        "validation_score": app.validation_score,
        "average": app.average,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
    }


# ----------------------------------------------------------------------
# Outils de lecture
# ----------------------------------------------------------------------
def search_applications(
    db: Session,
    university_id: uuid.UUID,
    *,
    program_name: str | None = None,
    status: str | None = None,
    min_score: float | None = None,
    min_average: float | None = None,
    days_since_last_update: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Recherche/filtre les candidatures, classées du meilleur au moins bon."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    stmt = _filtered_query(
        university_id,
        program_name=program_name,
        status=status,
        min_score=min_score,
        min_average=min_average,
        days_since_last_update=days_since_last_update,
    )
    # Meilleurs d'abord : moyenne décroissante (nulls en dernier), puis score IA.
    stmt = stmt.order_by(
        Application.average.is_(None),
        Application.average.desc(),
        Application.validation_score.desc().nullslast(),
    ).limit(limit)

    rows = list(db.execute(stmt).scalars().all())
    return {
        "count": len(rows),
        "applications": [_serialize_application(a) for a in rows],
    }


def get_application_detail(
    db: Session, university_id: uuid.UUID, *, application_id: str
) -> dict:
    """Détail complet d'une candidature (champs, documents, scores)."""
    try:
        app_uuid = uuid.UUID(str(application_id))
    except (ValueError, AttributeError):
        return {"error": "Identifiant de candidature invalide."}

    app = db.execute(
        select(Application)
        .where(Application.id == app_uuid)
        .where(Application.university_id == university_id)
        .options(selectinload(Application.documents))
    ).scalar_one_or_none()
    if app is None:
        return {"error": "Candidature introuvable."}

    documents = [
        {
            "type": d.document_type.value if d.document_type else None,
            "is_valid": d.is_valid,
            "errors": d.validation_errors or [],
        }
        for d in app.documents
    ]

    field_values = db.execute(
        select(FormField.label, ApplicationFieldValue.value)
        .join(ApplicationFieldValue, ApplicationFieldValue.field_id == FormField.id)
        .where(ApplicationFieldValue.application_id == app.id)
    ).all()

    detail = _serialize_application(app)
    detail.update(
        {
            "ai_notes": app.ai_notes,
            "decision_comment": app.decision_comment,
            "decided_at": app.decided_at.isoformat() if app.decided_at else None,
            "documents": documents,
            "fields": [{"label": lbl, "value": val} for lbl, val in field_values],
        }
    )
    return detail


def get_stats(
    db: Session,
    university_id: uuid.UUID,
    *,
    period: str = "all",
    group_by: str | None = None,
) -> dict:
    """Métriques agrégées par période et/ou regroupement."""
    stmt = select(Application).where(Application.university_id == university_id)

    now = datetime.now(timezone.utc)
    period = (period or "all").lower()
    if period == "today":
        stmt = stmt.where(Application.created_at >= now - timedelta(days=1))
    elif period == "week":
        stmt = stmt.where(Application.created_at >= now - timedelta(days=7))
    elif period == "month":
        stmt = stmt.where(Application.created_at >= now - timedelta(days=30))

    subq = stmt.subquery()
    total = db.execute(select(func.count()).select_from(subq)).scalar_one()

    result: dict = {"period": period, "total": total}

    if group_by == "program":
        rows = db.execute(
            select(Application.program, func.count())
            .where(Application.id.in_(select(subq.c.id)))
            .group_by(Application.program)
        ).all()
        result["by_program"] = {(p or "—"): c for p, c in rows}
    elif group_by in ("status", None):
        rows = db.execute(
            select(Application.status, func.count())
            .where(Application.id.in_(select(subq.c.id)))
            .group_by(Application.status)
        ).all()
        result["by_status"] = {
            (s.value if hasattr(s, "value") else str(s)): c for s, c in rows
        }
    return result


def get_program_criteria(
    db: Session, university_id: uuid.UUID, *, program_name: str
) -> dict:
    """Critères d'admission configurés pour un programme (ou message si aucun)."""
    program = _find_program(db, university_id, program_name)
    if program is None:
        return {"error": f"Programme introuvable : {program_name}"}

    criteria = db.execute(
        select(ProgramCriteria).where(ProgramCriteria.program_id == program.id)
    ).scalar_one_or_none()
    if criteria is None:
        return {"program": program.name, "criteria": None}

    return {"program": program.name, "criteria": _serialize_criteria(criteria)}


# ----------------------------------------------------------------------
# Outils d'écriture (dry_run obligatoire d'abord)
# ----------------------------------------------------------------------
def set_program_criteria(
    db: Session,
    university_id: uuid.UUID,
    *,
    program_name: str,
    prerequisites: list[str] | None = None,
    min_average: float | None = None,
    required_degree: str | None = None,
    accepted_specialties: list[str] | None = None,
    additional_notes: str | None = None,
    whatsapp_display: bool = True,
    updated_by: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Crée/met à jour les critères d'admission d'un programme."""
    program = _find_program(db, university_id, program_name)
    if program is None:
        return {"error": f"Programme introuvable : {program_name}"}

    summary = {
        "program": program.name,
        "prerequisites": prerequisites or [],
        "min_average": min_average,
        "required_degree": required_degree,
        "accepted_specialties": accepted_specialties or [],
        "whatsapp_display": whatsapp_display,
    }
    if dry_run:
        return {"dry_run": True, "would_set": summary}

    criteria = db.execute(
        select(ProgramCriteria).where(ProgramCriteria.program_id == program.id)
    ).scalar_one_or_none()
    if criteria is None:
        criteria = ProgramCriteria(program_id=program.id)
        db.add(criteria)

    criteria.prerequisites = prerequisites or []
    criteria.min_average = min_average
    criteria.required_degree = required_degree
    criteria.accepted_specialties = accepted_specialties or []
    criteria.additional_notes = additional_notes
    criteria.whatsapp_display = whatsapp_display
    criteria.updated_by_admin = updated_by
    db.commit()
    db.refresh(criteria)
    return {"updated": True, "criteria": _serialize_criteria(criteria)}


def bulk_decide(
    db: Session,
    university_id: uuid.UUID,
    *,
    decision: str,
    reason: str | None = None,
    dry_run: bool = False,
    **filters,
) -> dict:
    """Applique ACCEPTED/REJECTED à un ensemble de candidatures filtrées."""
    decision_enum = _coerce_status(decision)
    if decision_enum not in (ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED):
        return {"error": "La décision doit être ACCEPTED ou REJECTED."}

    stmt = _filtered_query(university_id, **_clean_filters(filters))
    candidates = [
        a
        for a in db.execute(stmt).scalars().all()
        if a.status in _DECIDABLE_STATUSES
    ]

    if dry_run:
        return {
            "dry_run": True,
            "decision": decision_enum.value,
            "count": len(candidates),
            "sample": [_serialize_application(a) for a in candidates[:5]],
        }

    from app.workers.webhook_tasks import (
        dispatch_decision_acknowledged_task,
        notify_student_decision_task,
    )

    decided = 0
    for app in candidates:
        app.status = decision_enum
        app.decision_comment = reason
        app.decided_at = datetime.now(timezone.utc)
        db.add(app)
        decided += 1
    db.commit()

    for app in candidates:
        notify_student_decision_task.delay(str(app.id))
        dispatch_decision_acknowledged_task.delay(str(app.id))

    return {"decided": decided, "decision": decision_enum.value}


def send_whatsapp_notification(
    db: Session,
    university_id: uuid.UUID,
    *,
    message_template: str,
    dry_run: bool = False,
    **filters,
) -> dict:
    """Envoie un message WhatsApp personnalisé aux candidatures filtrées."""
    if not message_template or not message_template.strip():
        return {"error": "Le message est vide."}

    stmt = _filtered_query(university_id, **_clean_filters(filters))
    recipients = list(db.execute(stmt).scalars().all())

    if dry_run:
        return {"dry_run": True, "count": len(recipients)}

    from app.services.whatsapp_bot import send_whatsapp

    sent = 0
    for app in recipients:
        msg = (
            message_template.replace("{student_name}", app.student_name or "")
            .replace("{program_name}", app.program or "")
            .replace("{status}", app.status.value if app.status else "")
        )
        try:
            send_whatsapp(app.student_phone, msg)
            sent += 1
        except Exception:
            logger.warning("Échec d'envoi WhatsApp à %s", app.student_phone, exc_info=True)
    return {"sent": sent, "total": len(recipients)}


# ----------------------------------------------------------------------
# Helpers privés
# ----------------------------------------------------------------------
def _find_program(db: Session, university_id: uuid.UUID, name: str) -> Program | None:
    if not name:
        return None
    # Correspondance exacte d'abord, puis approximative
    program = db.execute(
        select(Program)
        .where(Program.university_id == university_id)
        .where(Program.name.ilike(name))
    ).scalars().first()
    if program is None:
        program = db.execute(
            select(Program)
            .where(Program.university_id == university_id)
            .where(Program.name.ilike(f"%{name}%"))
        ).scalars().first()
    return program


def _serialize_criteria(c: ProgramCriteria) -> dict:
    return {
        "prerequisites": c.prerequisites or [],
        "min_average": float(c.min_average) if c.min_average is not None else None,
        "required_degree": c.required_degree,
        "accepted_specialties": c.accepted_specialties or [],
        "additional_notes": c.additional_notes,
        "whatsapp_display": c.whatsapp_display,
    }


# Clés de filtre autorisées pour bulk_decide / send_whatsapp_notification
_FILTER_KEYS = {
    "program_name",
    "status",
    "min_score",
    "min_average",
    "days_since_last_update",
}


def _clean_filters(filters: dict) -> dict:
    """Ne garde que les clés de filtre connues (les filtres LLM peuvent être bruités)."""
    flat = dict(filters)
    if "filter" in flat and isinstance(flat["filter"], dict):
        flat = {**flat, **flat.pop("filter")}
    return {k: v for k, v in flat.items() if k in _FILTER_KEYS and v is not None}
