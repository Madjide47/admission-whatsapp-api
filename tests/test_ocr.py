"""Tests du service OCR — uniquement la logique de routage (image vs PDF).

Les appels réels à Tesseract sont monkey-patchés : on ne dépend pas
d'une installation locale de Tesseract pour passer ces tests.
"""
import io

import pytest
from PIL import Image

from app.services.ocr_service import OCRService


@pytest.fixture()
def ocr_service():
    return OCRService(lang="eng")


def _make_png_bytes(text_hint: str = "test") -> bytes:
    """Crée un PNG 100x40 blanc (sans texte réel — l'OCR sera mocké)."""
    buf = io.BytesIO()
    Image.new("RGB", (100, 40), color="white").save(buf, format="PNG")
    return buf.getvalue()


def test_extract_from_image_returns_string(ocr_service, monkeypatch):
    """Une image PNG est routée vers _extract_from_image."""
    monkeypatch.setattr(
        "pytesseract.image_to_string",
        lambda img, lang: "Texte simulé extrait par OCR",
    )
    text = ocr_service.extract_text(_make_png_bytes(), mime_type="image/png")
    assert "Texte simulé" in text


def test_extract_from_pdf_routes_to_pdf_handler(ocr_service, monkeypatch):
    """Le mime application/pdf doit déclencher la conversion PDF→image."""
    captured = {}

    def fake_convert(content, **kwargs):
        captured["called"] = True
        return [Image.new("RGB", (50, 50), color="white")]

    monkeypatch.setattr("pdf2image.convert_from_bytes", fake_convert)
    monkeypatch.setattr(
        "pytesseract.image_to_string",
        lambda img, lang: "Page de PDF",
    )

    text = ocr_service.extract_text(b"%PDF-1.4 fake content", mime_type="application/pdf")
    assert captured.get("called") is True
    assert "Page de PDF" in text


def test_extract_empty_on_corrupted_file(ocr_service, monkeypatch):
    """Un fichier corrompu ne doit pas lever — retour string vide."""
    text = ocr_service.extract_text(b"pas une image", mime_type="image/png")
    assert text == ""


def test_extract_image_converts_palette_to_rgb(ocr_service, monkeypatch):
    """Image en mode palette (P) est convertie en RGB avant l'OCR."""
    monkeypatch.setattr(
        "pytesseract.image_to_string",
        lambda img, lang: "texte ok",
    )
    buf = io.BytesIO()
    Image.new("P", (100, 40)).save(buf, format="PNG")
    text = ocr_service.extract_text(buf.getvalue(), mime_type="image/png")
    assert text == "texte ok"


def test_extract_pdf_conversion_error_returns_empty(ocr_service, monkeypatch):
    """Erreur lors de la conversion PDF→images → retourne '' sans crash."""
    monkeypatch.setattr(
        "pdf2image.convert_from_bytes",
        lambda *a, **kw: (_ for _ in ()).throw(Exception("Poppler absent")),
    )
    text = ocr_service.extract_text(b"%PDF-1.4 fake", mime_type="application/pdf")
    assert text == ""


def test_extract_autodetects_pdf_by_magic_bytes(ocr_service, monkeypatch):
    """Les octets %PDF déclenchent le chemin PDF même sans mime_type."""
    captured = {}

    def fake_convert(content, **kwargs):
        captured["called"] = True
        return [Image.new("RGB", (50, 50), color="white")]

    monkeypatch.setattr("pdf2image.convert_from_bytes", fake_convert)
    monkeypatch.setattr(
        "pytesseract.image_to_string",
        lambda img, lang: "contenu",
    )
    ocr_service.extract_text(b"%PDF-1.4 content", mime_type=None)
    assert captured.get("called") is True


def test_get_ocr_service_singleton():
    """get_ocr_service retourne toujours la même instance."""
    import importlib
    import app.services.ocr_service as mod

    mod._ocr_service = None  # reset singleton
    s1 = mod.get_ocr_service()
    s2 = mod.get_ocr_service()
    assert s1 is s2
