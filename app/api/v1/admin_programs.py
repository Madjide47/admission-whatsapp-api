"""Endpoints d'administration des formulaires dynamiques — Dev 1 v2.

Routes (toutes protégées par X-API-Key + X-API-Secret) :
  GET    /api/v1/admin/programs                 — liste les programmes de l'université
  POST   /api/v1/admin/programs                 — crée un programme
  GET    /api/v1/admin/forms/{program_id}       — récupère la config complète d'un formulaire
  PUT    /api/v1/admin/forms/{program_id}       — remplace champs + documents requis
  POST   /api/v1/admin/forms/{program_id}/publish — publie le formulaire
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.api_key import get_current_university
from app.database import get_db
from app.models.program import AdmissionForm, FormField, Program, RequiredDocument
from app.models.program_criteria import ProgramCriteria
from app.models.university import University
from app.schemas.program import (
    AdmissionFormRead,
    AdmissionFormUpdate,
    ProgramCreate,
    ProgramCriteriaRead,
    ProgramCriteriaUpdate,
    ProgramRead,
    ProgramUpdate,
)

router = APIRouter()


def _success(data) -> dict:
    return {"success": True, "data": data}


def _error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


def _get_program_or_404(
    program_id: uuid.UUID,
    university: University,
    db: Session,
) -> Program:
    prog = db.execute(
        select(Program)
        .where(Program.id == program_id)
        .where(Program.university_id == university.id)
    ).scalar_one_or_none()
    if prog is None:
        raise _error("PROGRAM_NOT_FOUND", "Programme introuvable.", 404)
    return prog


# ----------------------------------------------------------------------
# GET /api/v1/admin/programs
# ----------------------------------------------------------------------
@router.get(
    "/programs",
    summary="Lister les programmes de l'université",
)
def list_programs(
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    rows = db.execute(
        select(Program)
        .where(Program.university_id == university.id)
        .order_by(Program.created_at.desc())
    ).scalars().all()
    return _success([ProgramRead.model_validate(p).model_dump(mode="json") for p in rows])


# ----------------------------------------------------------------------
# POST /api/v1/admin/programs
# ----------------------------------------------------------------------
@router.post(
    "/programs",
    status_code=status.HTTP_201_CREATED,
    summary="Créer un programme",
)
def create_program(
    payload: ProgramCreate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    prog = Program(
        university_id=university.id,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    db.add(prog)
    # Créer d'office un formulaire vide (non publié) pour ce programme
    form = AdmissionForm(program_id=prog.id)
    db.add(form)
    db.commit()
    db.refresh(prog)
    return _success(ProgramRead.model_validate(prog).model_dump(mode="json"))


# ----------------------------------------------------------------------
# PATCH /api/v1/admin/programs/{program_id}
# ----------------------------------------------------------------------
@router.patch(
    "/programs/{program_id}",
    summary="Mettre à jour un programme",
)
def update_program(
    program_id: uuid.UUID,
    payload: ProgramUpdate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    prog = _get_program_or_404(program_id, university, db)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(prog, field, value)
    db.add(prog)
    db.commit()
    db.refresh(prog)
    return _success(ProgramRead.model_validate(prog).model_dump(mode="json"))


# ----------------------------------------------------------------------
# GET /api/v1/admin/forms/{program_id}
# ----------------------------------------------------------------------
@router.get(
    "/forms/{program_id}",
    summary="Récupérer la configuration complète du formulaire d'un programme",
)
def get_form(
    program_id: uuid.UUID,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _get_program_or_404(program_id, university, db)

    form = db.execute(
        select(AdmissionForm)
        .options(
            selectinload(AdmissionForm.fields),
            selectinload(AdmissionForm.required_documents),
        )
        .where(AdmissionForm.program_id == program_id)
        .order_by(AdmissionForm.created_at.desc())
    ).scalar_one_or_none()

    if form is None:
        raise _error("FORM_NOT_FOUND", "Aucun formulaire trouvé pour ce programme.", 404)

    return _success(AdmissionFormRead.model_validate(form).model_dump(mode="json"))


# ----------------------------------------------------------------------
# PUT /api/v1/admin/forms/{program_id}
# ----------------------------------------------------------------------
@router.put(
    "/forms/{program_id}",
    summary="Remplacer entièrement les champs et documents requis du formulaire",
)
def update_form(
    program_id: uuid.UUID,
    payload: AdmissionFormUpdate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _get_program_or_404(program_id, university, db)

    form = db.execute(
        select(AdmissionForm)
        .options(
            selectinload(AdmissionForm.fields),
            selectinload(AdmissionForm.required_documents),
        )
        .where(AdmissionForm.program_id == program_id)
    ).scalar_one_or_none()

    if form is None:
        # Crée un nouveau formulaire si inexistant
        form = AdmissionForm(program_id=program_id)
        db.add(form)
        db.flush()

    if form.is_published:
        raise _error(
            "FORM_ALREADY_PUBLISHED",
            "Le formulaire est publié. Dépubliez-le avant de le modifier.",
            409,
        )

    # Supprime les anciens champs / documents puis recrée
    for old_field in list(form.fields):
        db.delete(old_field)
    for old_doc in list(form.required_documents):
        db.delete(old_doc)
    db.flush()

    for i, f in enumerate(payload.fields):
        db.add(FormField(
            form_id=form.id,
            label=f.label,
            field_type=f.field_type.value,
            order=f.order if f.order else i,
            is_required=f.is_required,
            validation_regex=f.validation_regex,
        ))

    for i, d in enumerate(payload.required_documents):
        db.add(RequiredDocument(
            form_id=form.id,
            document_type=d.document_type,
            label=d.label,
            order=d.order if d.order else i,
            is_required=d.is_required,
        ))

    db.commit()

    form = db.execute(
        select(AdmissionForm)
        .options(
            selectinload(AdmissionForm.fields),
            selectinload(AdmissionForm.required_documents),
        )
        .where(AdmissionForm.id == form.id)
    ).scalar_one()

    return _success(AdmissionFormRead.model_validate(form).model_dump(mode="json"))


# ----------------------------------------------------------------------
# POST /api/v1/admin/forms/{program_id}/publish
# ----------------------------------------------------------------------
@router.post(
    "/forms/{program_id}/publish",
    summary="Publier le formulaire (rend le bot opérationnel pour ce programme)",
)
def publish_form(
    program_id: uuid.UUID,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _get_program_or_404(program_id, university, db)

    form = db.execute(
        select(AdmissionForm)
        .options(
            selectinload(AdmissionForm.fields),
            selectinload(AdmissionForm.required_documents),
        )
        .where(AdmissionForm.program_id == program_id)
    ).scalar_one_or_none()

    if form is None:
        raise _error("FORM_NOT_FOUND", "Aucun formulaire trouvé pour ce programme.", 404)

    if form.is_published:
        return _success({
            "message": "Le formulaire est déjà publié.",
            "form": AdmissionFormRead.model_validate(form).model_dump(mode="json"),
        })

    form.is_published = True
    form.published_at = datetime.now(timezone.utc)
    db.add(form)
    db.commit()
    db.refresh(form)

    return _success({
        "message": "Formulaire publié avec succès.",
        "form": AdmissionFormRead.model_validate(form).model_dump(mode="json"),
    })


# ----------------------------------------------------------------------
# POST /api/v1/admin/forms/{program_id}/unpublish
# ----------------------------------------------------------------------
@router.post(
    "/forms/{program_id}/unpublish",
    summary="Dépublier le formulaire (le repasse en brouillon pour le modifier)",
)
def unpublish_form(
    program_id: uuid.UUID,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _get_program_or_404(program_id, university, db)

    form = db.execute(
        select(AdmissionForm)
        .options(
            selectinload(AdmissionForm.fields),
            selectinload(AdmissionForm.required_documents),
        )
        .where(AdmissionForm.program_id == program_id)
    ).scalar_one_or_none()

    if form is None:
        raise _error("FORM_NOT_FOUND", "Aucun formulaire trouvé pour ce programme.", 404)

    form.is_published = False
    form.published_at = None
    db.add(form)
    db.commit()
    db.refresh(form)

    return _success({
        "message": "Formulaire dépublié — vous pouvez le modifier.",
        "form": AdmissionFormRead.model_validate(form).model_dump(mode="json"),
    })


# ----------------------------------------------------------------------
# GET /api/v1/admin/programs/{program_id}/criteria
# ----------------------------------------------------------------------
@router.get(
    "/programs/{program_id}/criteria",
    summary="Lire les critères d'admission d'un programme",
)
def get_program_criteria(
    program_id: uuid.UUID,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _get_program_or_404(program_id, university, db)

    criteria = db.execute(
        select(ProgramCriteria).where(ProgramCriteria.program_id == program_id)
    ).scalar_one_or_none()

    if criteria is None:
        return _success(None)
    return _success(ProgramCriteriaRead.model_validate(criteria).model_dump(mode="json"))


# ----------------------------------------------------------------------
# PUT /api/v1/admin/programs/{program_id}/criteria
# ----------------------------------------------------------------------
@router.put(
    "/programs/{program_id}/criteria",
    summary="Créer / mettre à jour les critères d'admission (aussi via chatbot)",
)
def update_program_criteria(
    program_id: uuid.UUID,
    payload: ProgramCriteriaUpdate,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    _get_program_or_404(program_id, university, db)

    criteria = db.execute(
        select(ProgramCriteria).where(ProgramCriteria.program_id == program_id)
    ).scalar_one_or_none()

    if criteria is None:
        criteria = ProgramCriteria(program_id=program_id)
        db.add(criteria)

    criteria.prerequisites = payload.prerequisites
    criteria.min_average = payload.min_average
    criteria.required_degree = payload.required_degree
    criteria.accepted_specialties = payload.accepted_specialties
    criteria.additional_notes = payload.additional_notes
    criteria.whatsapp_display = payload.whatsapp_display
    criteria.updated_by_admin = university.name

    db.commit()
    db.refresh(criteria)

    return _success(ProgramCriteriaRead.model_validate(criteria).model_dump(mode="json"))
