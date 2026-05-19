"""Service OCR — extraction de texte depuis PDF ou images via Tesseract."""
import io
import logging

import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes

from app.config import settings

logger = logging.getLogger(__name__)

# Configurer le chemin de Tesseract si fourni explicitement
if settings.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


class OCRService:
    """Extrait le texte d'un document (PDF ou image) avec Tesseract."""

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang or settings.TESSERACT_LANG

    def extract_text(self, content: bytes, mime_type: str | None = None) -> str:
        """Extrait le texte d'un fichier en mémoire.

        Détection automatique du type via mime_type (préféré) ou les
        premiers octets du fichier.
        """
        mime = (mime_type or "").lower()

        if mime == "application/pdf" or content[:4] == b"%PDF":
            return self._extract_from_pdf(content)

        # Tout le reste est traité comme une image
        return self._extract_from_image(content)

    def _extract_from_image(self, content: bytes) -> str:
        """OCR sur une image (PNG, JPEG, etc.)."""
        try:
            image = Image.open(io.BytesIO(content))
            # Conversion en RGB pour éviter les warnings sur les PNG palette
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            text = pytesseract.image_to_string(image, lang=self.lang)
            return text.strip()
        except Exception as e:
            logger.exception("Erreur OCR sur image: %s", e)
            return ""

    def _extract_from_pdf(self, content: bytes) -> str:
        """OCR sur un PDF — chaque page est convertie en image puis OCR.

        On limite à 20 pages pour éviter les abus.
        """
        try:
            pages = convert_from_bytes(content, dpi=200, first_page=1, last_page=20)
        except Exception as e:
            logger.exception("Erreur de conversion PDF→images: %s", e)
            return ""

        chunks: list[str] = []
        for i, page in enumerate(pages, start=1):
            try:
                txt = pytesseract.image_to_string(page, lang=self.lang)
                if txt.strip():
                    chunks.append(f"--- Page {i} ---\n{txt.strip()}")
            except Exception as e:
                logger.warning("Erreur OCR page %d: %s", i, e)

        return "\n\n".join(chunks)


_ocr_service: OCRService | None = None


def get_ocr_service() -> OCRService:
    """Retourne le singleton OCRService."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service
