"""Service de stockage — GCS en production, filesystem local en mode démo."""
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO

from app.config import settings

logger = logging.getLogger(__name__)

# Répertoire local utilisé en mode démo
_LOCAL_UPLOAD_DIR = Path("uploads")


class LocalStorageService:
    """Stockage local sur le filesystem — uniquement pour le mode démo."""

    def upload_document(
        self,
        application_id: uuid.UUID,
        file_stream: BinaryIO,
        original_filename: str,
        mime_type: str | None = None,
    ) -> str:
        random_id = uuid.uuid4().hex[:12]
        safe_name = original_filename.replace("/", "_").replace("\\", "_")
        path = f"applications/{application_id}/{random_id}_{safe_name}"
        dest = _LOCAL_UPLOAD_DIR / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(file_stream.read())
        logger.info("[DEMO] Document sauvegardé localement : %s", dest)
        return path

    def download_to_bytes(self, gcs_path: str) -> bytes:
        src = _LOCAL_UPLOAD_DIR / gcs_path
        if not src.exists():
            raise FileNotFoundError(f"Fichier local introuvable : {src}")
        return src.read_bytes()

    def generate_signed_url(self, gcs_path: str, expiry_minutes: int | None = None) -> str:
        return f"file://uploads/{gcs_path}"

    def delete(self, gcs_path: str) -> None:
        path = _LOCAL_UPLOAD_DIR / gcs_path
        if path.exists():
            path.unlink()


class StorageService:
    """Wrapper autour de google-cloud-storage."""

    def __init__(self) -> None:
        from google.cloud import storage as gcs
        self.client = gcs.Client(project=settings.GCS_PROJECT_ID)
        self.bucket = self.client.bucket(settings.GCS_BUCKET_NAME)

    def upload_document(
        self,
        application_id: uuid.UUID,
        file_stream: BinaryIO,
        original_filename: str,
        mime_type: str | None = None,
    ) -> str:
        """Upload un fichier dans GCS et retourne le chemin (path).

        Le chemin suit le pattern :
            applications/<application_id>/<random_uuid>_<filename>
        """
        random_id = uuid.uuid4().hex[:12]
        safe_name = original_filename.replace("/", "_").replace("\\", "_")
        path = f"applications/{application_id}/{random_id}_{safe_name}"

        blob = self.bucket.blob(path)
        if mime_type:
            blob.content_type = mime_type

        blob.upload_from_file(file_stream, rewind=True)
        logger.info("Document uploadé sur GCS: %s", path)
        return path

    def download_to_bytes(self, gcs_path: str) -> bytes:
        """Récupère le contenu d'un fichier GCS en mémoire (pour OCR, IA, etc.)."""
        from google.cloud.exceptions import NotFound
        blob = self.bucket.blob(gcs_path)
        try:
            return blob.download_as_bytes()
        except NotFound:
            logger.error("Fichier GCS introuvable: %s", gcs_path)
            raise

    def generate_signed_url(
        self,
        gcs_path: str,
        expiry_minutes: int | None = None,
    ) -> str:
        """Génère une URL signée à durée limitée pour télécharger un document.

        Les universités reçoivent ce type d'URL dans les payloads webhook.
        """
        expiry_minutes = expiry_minutes or settings.GCS_SIGNED_URL_EXPIRY_MINUTES
        blob = self.bucket.blob(gcs_path)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiry_minutes),
            method="GET",
        )
        return url

    def delete(self, gcs_path: str) -> None:
        """Supprime un fichier de GCS."""
        from google.cloud.exceptions import NotFound
        blob = self.bucket.blob(gcs_path)
        try:
            blob.delete()
            logger.info("Document supprimé de GCS: %s", gcs_path)
        except NotFound:
            logger.warning("Fichier GCS déjà absent: %s", gcs_path)


_storage_service: StorageService | LocalStorageService | None = None


def get_storage_service() -> StorageService | LocalStorageService:
    """Retourne le singleton — LocalStorageService en mode démo ou LOCAL_STORAGE, GCS sinon."""
    global _storage_service
    if _storage_service is None:
        if settings.DEMO_MODE or settings.LOCAL_STORAGE:
            logger.info("[LOCAL] Stockage local activé (pas de GCS)")
            _storage_service = LocalStorageService()
        else:
            _storage_service = StorageService()
    return _storage_service
