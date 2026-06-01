"""Endpoint POST /api/v1/applications/{id}/documents — upload de document."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api_key import get_current_university
from app.database import get_db
from app.models.application import Application
from app.models.document import Document, DocumentType
from app.models.university import University
from app.schemas.document import DocumentRead
from app.services.storage_service import get_storage_service

router = APIRouter()


ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


def _error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"success": False, "error": {"code": code, "message": message}},
    )


@router.post(
    "/{application_id}/documents",
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un document à une candidature",
)
async def upload_document(
    application_id: uuid.UUID,
    university: Annotated[University, Depends(get_current_university)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(..., description="Fichier PDF ou image")],
    document_type: Annotated[DocumentType, Form(...)] = DocumentType.AUTRE,
) -> dict:
    # Vérification que la candidature appartient à l'université authentifiée
    application = db.execute(
        select(Application)
        .where(Application.id == application_id)
        .where(Application.university_id == university.id)
    ).scalar_one_or_none()
    if application is None:
        raise _error("APPLICATION_NOT_FOUND", "Candidature introuvable.", 404)

    # Validation du fichier
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise _error(
            "INVALID_FILE_TYPE",
            f"Type de fichier non supporté : {file.content_type}. "
            f"Acceptés : PDF, JPEG, PNG, WEBP, HEIC.",
            415,
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise _error(
            "FILE_TOO_LARGE",
            f"Fichier trop volumineux (max {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB).",
            413,
        )

    # Upload GCS
    storage = get_storage_service()
    import io

    gcs_path = storage.upload_document(
        application_id=application.id,
        file_stream=io.BytesIO(content),
        original_filename=file.filename or "document",
        mime_type=file.content_type,
    )

    document = Document(
        application_id=application.id,
        document_type=document_type,
        original_filename=file.filename,
        mime_type=file.content_type,
        file_size=len(content),
        gcs_path=gcs_path,
        is_valid=False,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Lancement asynchrone OCR → IA → revalidation
    from app.workers.ocr_tasks import run_ocr_task

    run_ocr_task.delay(str(document.id))

    return {
        "success": True,
        "data": DocumentRead.model_validate(document).model_dump(mode="json"),
    }
