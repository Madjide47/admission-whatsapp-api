"""Service OCR — extraction de texte depuis PDF ou images via Tesseract."""
import io
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class OCRService:
    """Extrait le texte d'un document (PDF ou image) avec Tesseract."""

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang or settings.TESSERACT_LANG

    def extract_text(self, content: bytes, mime_type: str | None = None) -> str:
        """Extrait le texte d'un fichier en mémoire."""
        mime = (mime_type or "").lower()
        if mime == "application/pdf" or content[:4] == b"%PDF":
            return self._extract_from_pdf(content)
        return self._extract_from_image(content)

    def _extract_from_image(self, content: bytes) -> str:
        """OCR sur une image (PNG, JPEG, etc.)."""
        try:
            import pytesseract
            from PIL import Image

            if settings.TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

            image = Image.open(io.BytesIO(content))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            return pytesseract.image_to_string(image, lang=self.lang).strip()
        except Exception as e:
            logger.exception("Erreur OCR sur image: %s", e)
            return ""

    def _extract_from_pdf(self, content: bytes) -> str:
        """OCR sur un PDF — chaque page convertie en image puis OCR."""
        try:
            import pytesseract
            from pdf2image import convert_from_bytes

            if settings.TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

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


def render_pdf_first_page_to_image(content: bytes) -> bytes | None:
    """Rend la 1re page d'un PDF en PNG, pour l'analyse vision de l'IA.

    Permet de classifier un PDF par image (et non seulement par texte OCR) —
    indispensable pour une photo d'identité enregistrée en PDF. Retourne None
    si la conversion échoue (PDF corrompu, poppler absent…), l'appelant retombe
    alors sur la classification par texte OCR.
    """
    try:
        from pdf2image import convert_from_bytes

        pages = convert_from_bytes(content, dpi=200, first_page=1, last_page=1)
        if not pages:
            return None
        buffer = io.BytesIO()
        pages[0].save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as e:
        logger.warning("Impossible de rendre la 1re page du PDF en image: %s", e)
        return None


_ocr_service: OCRService | None = None


def get_ocr_service() -> OCRService:
    """Retourne le singleton OCRService."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service
