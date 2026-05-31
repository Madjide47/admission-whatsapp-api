"""Tests du service de validation de candidature et du pipeline complet."""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.university import University
from app.services.validator import ApplicationValidator, REQUIRED_DOCUMENT_TYPES


@pytest.fixture()
def university(db_session) -> University:
    univ = University(
        id=uuid.uuid4(),
        name="Univ Validator Test",
        email=f"val-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix="val_test",
        webhook_url="https://example.test/wh",
        webhook_secret="sec",
        is_active=True,
    )
    db_session.add(univ)
    db_session.commit()
    db_session.refresh(univ)
    return univ


@pytest.fixture()
def base_application(db_session, university) -> Application:
    app = Application(
        id=uuid.uuid4(),
        university_id=university.id,
        student_phone="+22890111111",
        student_name="Aminata Diallo",
        program="Licence Droit",
        status=ApplicationStatus.COLLECTING,
        conversation_state="COLLECT_DOCS",
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


def _add_valid_doc(db_session, application, doc_type: DocumentType, confidence: float = 0.9):
    doc = Document(
        id=uuid.uuid4(),
        application_id=application.id,
        document_type=doc_type,
        gcs_path=f"gs://bucket/{doc_type.value}",
        is_valid=True,
        classification_result={"confidence": confidence},
    )
    db_session.add(doc)
    db_session.commit()
    return doc


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_incomplete_no_docs(db_session, base_application):
    validator = ApplicationValidator(db_session)
    is_complete, score, reasons = validator.validate(base_application)
    assert is_complete is False
    assert score == 0.0
    assert len(reasons) == len(REQUIRED_DOCUMENT_TYPES)


def test_validate_missing_student_name(db_session, base_application):
    base_application.student_name = None
    db_session.add(base_application)
    db_session.commit()

    validator = ApplicationValidator(db_session)
    is_complete, _, reasons = validator.validate(base_application)
    assert is_complete is False
    assert any("Nom" in r for r in reasons)


def test_validate_complete_with_all_docs(db_session, base_application):
    for doc_type in REQUIRED_DOCUMENT_TYPES:
        _add_valid_doc(db_session, base_application, doc_type, confidence=0.9)
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    is_complete, score, reasons = validator.validate(base_application)
    assert is_complete is True
    assert reasons == []
    assert score == pytest.approx(0.9)


def test_validate_score_averages_confidences(db_session, base_application):
    confidences = [0.8, 0.9, 0.7, 1.0]
    for doc_type, conf in zip(REQUIRED_DOCUMENT_TYPES, confidences):
        _add_valid_doc(db_session, base_application, doc_type, confidence=conf)
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    _, score, _ = validator.validate(base_application)
    expected = sum(confidences) / len(confidences)
    assert score == pytest.approx(round(expected, 3))


def test_validate_invalid_doc_type_counted_as_missing(db_session, base_application):
    """Un document invalide (is_valid=False) ne compte pas comme présent."""
    doc = Document(
        id=uuid.uuid4(),
        application_id=base_application.id,
        document_type=DocumentType.DIPLOME,
        gcs_path="gs://bucket/diplome",
        is_valid=False,
        classification_result={"confidence": 0.2},
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    is_complete, _, reasons = validator.validate(base_application)
    assert is_complete is False
    assert any("DIPLOME" in r for r in reasons)


# ---------------------------------------------------------------------------
# apply_validation()
# ---------------------------------------------------------------------------


def test_apply_validation_sets_status_validated(db_session, base_application):
    for doc_type in REQUIRED_DOCUMENT_TYPES:
        _add_valid_doc(db_session, base_application, doc_type)
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    result = validator.apply_validation(base_application)
    assert result.status == ApplicationStatus.VALIDATED
    assert result.validation_score > 0


def test_apply_validation_stays_collecting_if_incomplete(db_session, base_application):
    # Aucun document ajouté
    validator = ApplicationValidator(db_session)
    result = validator.apply_validation(base_application)
    assert result.status == ApplicationStatus.COLLECTING
    assert "Problèmes" in result.ai_notes


# ---------------------------------------------------------------------------
# Pipeline: check_application_completion_task
# ---------------------------------------------------------------------------


def test_pipeline_triggers_webhook_when_validated(db_session, base_application, monkeypatch):
    """Quand le dossier passe VALIDATED, la tâche webhook doit être déclenchée."""
    for doc_type in REQUIRED_DOCUMENT_TYPES:
        _add_valid_doc(db_session, base_application, doc_type)
    db_session.refresh(base_application)

    # Injecter la session de test dans la tâche Celery
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    # Empêcher la tâche de fermer la session de test
    monkeypatch.setattr(db_session, "close", lambda: None)

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_delay:
        from app.workers.ai_tasks import check_application_completion_task

        check_application_completion_task.run(str(base_application.id))

    mock_delay.assert_called_once_with(str(base_application.id))


def test_pipeline_no_webhook_when_incomplete(db_session, base_application, monkeypatch):
    """Sans documents, le webhook ne doit PAS être déclenché."""
    monkeypatch.setattr("app.workers.ai_tasks.get_db_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    with patch("app.workers.webhook_tasks.dispatch_validated_application_task.delay") as mock_delay:
        from app.workers.ai_tasks import check_application_completion_task

        check_application_completion_task.run(str(base_application.id))

    mock_delay.assert_not_called()
