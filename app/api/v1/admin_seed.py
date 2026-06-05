"""Endpoint POST /api/v1/admin/seed-university — environnement non-production uniquement.

Crée une université de démo complète (université + programme + formulaire publié)
et retourne les credentials en clair UNE SEULE FOIS pour simplifier les tests.
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_key import (
    api_key_prefix,
    generate_api_key,
    generate_api_secret,
    generate_webhook_secret,
    hash_credential,
)
from app.config import settings
from app.database import get_db
from app.models.program import AdmissionForm, FormField, Program, RequiredDocument
from app.models.university import University
from app.schemas.program import SeedUniversityRequest, SeedUniversityResponse

router = APIRouter()


@router.post(
    "/seed-university",
    status_code=status.HTTP_201_CREATED,
    response_model=SeedUniversityResponse,
    summary="Créer une université de démonstration avec credentials (non-production)",
)
def seed_university(
    payload: SeedUniversityRequest,
    db: Annotated[Session, Depends(get_db)],
) -> SeedUniversityResponse:
    """Refuse en production. Crée université + programme + formulaire publié par défaut."""
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "FORBIDDEN_IN_PRODUCTION",
                    "message": "L'endpoint seed est désactivé en production.",
                },
            },
        )

    # Vérifier si un email identique existe déjà
    existing = db.execute(
        select(University).where(University.email == payload.email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "error": {
                    "code": "EMAIL_ALREADY_EXISTS",
                    "message": f"Une université avec l'email '{payload.email}' existe déjà.",
                },
            },
        )

    # Générer les credentials
    raw_api_key = generate_api_key()
    raw_api_secret = generate_api_secret()
    raw_webhook_secret = generate_webhook_secret()

    # Créer l'université
    university = University(
        id=uuid.uuid4(),
        name=payload.name,
        email=payload.email,
        api_key_hash=hash_credential(raw_api_key),
        api_secret_hash=hash_credential(raw_api_secret),
        api_key_prefix=api_key_prefix(raw_api_key),
        webhook_url=None,
        webhook_secret=raw_webhook_secret,
        is_active=True,
    )
    db.add(university)
    db.flush()

    # Créer un programme par défaut
    program = Program(
        university_id=university.id,
        name="Programme Demo",
        description="Programme de démonstration créé automatiquement.",
        is_active=True,
    )
    db.add(program)
    db.flush()

    # Créer un formulaire avec champs et documents par défaut
    form = AdmissionForm(
        program_id=program.id,
        is_published=True,
        published_at=datetime.now(timezone.utc),
    )
    db.add(form)
    db.flush()

    # Champs texte de base
    default_fields = [
        ("Nom complet", "text", 0),
        ("Email", "email", 1),
        ("Numéro de téléphone", "phone", 2),
        ("Date de naissance", "date", 3),
    ]
    for label, ftype, order in default_fields:
        db.add(FormField(
            form_id=form.id,
            label=label,
            field_type=ftype,
            order=order,
            is_required=True,
        ))

    # Documents requis par défaut
    default_docs = [
        ("CARTE_IDENTITE", "Carte d'identité ou passeport", 0),
        ("DIPLOME", "Diplôme ou attestation de réussite", 1),
        ("RELEVE_NOTES", "Relevé de notes", 2),
        ("PHOTO", "Photo d'identité", 3),
    ]
    for doc_type, doc_label, order in default_docs:
        db.add(RequiredDocument(
            form_id=form.id,
            document_type=doc_type,
            label=doc_label,
            order=order,
            is_required=True,
        ))

    db.commit()

    return SeedUniversityResponse(
        university_id=university.id,
        name=university.name,
        email=university.email,
        api_key=raw_api_key,
        api_secret=raw_api_secret,
        webhook_secret=raw_webhook_secret,
        program_id=program.id,
        program_name=program.name,
        form_id=form.id,
    )
