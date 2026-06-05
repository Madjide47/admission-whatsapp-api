"""Tests du service de validation de candidature et du pipeline complet."""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.application import Application, ApplicationStatus
from app.models.document import Document, DocumentType
from app.models.program import AdmissionForm, Program, RequiredDocument
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
        status=ApplicationStatus.COLLECTING_FIELDS,
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
    assert result.status == ApplicationStatus.COLLECTING_DOCUMENTS
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


# ---------------------------------------------------------------------------
# Validator dynamique — chargement depuis required_documents
# ---------------------------------------------------------------------------


@pytest.fixture()
def program_with_requirements(db_session, university) -> tuple[Program, list[RequiredDocument]]:
    """Programme avec 2 documents requis seulement : DIPLOME + CARTE_IDENTITE."""
    program = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name="Licence Droit",  # même nom que base_application.program
        is_active=True,
    )
    db_session.add(program)
    db_session.flush()

    form = AdmissionForm(
        id=uuid.uuid4(),
        program_id=program.id,
        is_published=True,
    )
    db_session.add(form)
    db_session.flush()

    req_docs = []
    for i, (doc_type, label) in enumerate([
        (DocumentType.DIPLOME, "Diplôme du bac"),
        (DocumentType.CARTE_IDENTITE, "Carte d'identité"),
    ]):
        rd = RequiredDocument(
            id=uuid.uuid4(),
            form_id=form.id,
            document_type=doc_type.value,
            is_required=True,
            label=label,
            order=i,
        )
        db_session.add(rd)
        req_docs.append(rd)

    db_session.commit()
    return program, req_docs


def test_dynamic_validator_uses_required_documents(db_session, base_application, program_with_requirements):
    """Quand required_documents existe, seuls ces types sont exigés (pas le set hardcodé)."""
    _, req_docs = program_with_requirements
    # Ajouter seulement DIPLOME et CARTE_IDENTITE (les 2 types configurés)
    for doc_type in [DocumentType.DIPLOME, DocumentType.CARTE_IDENTITE]:
        _add_valid_doc(db_session, base_application, doc_type)
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    is_complete, score, reasons = validator.validate(base_application)

    assert is_complete is True, f"Devrait être complet. Raisons : {reasons}"
    assert reasons == []


def test_dynamic_validator_ignores_unconfigured_types(db_session, base_application, program_with_requirements):
    """RELEVE_NOTES et PHOTO ne sont PAS dans required_documents → non exigés."""
    _add_valid_doc(db_session, base_application, DocumentType.DIPLOME)
    _add_valid_doc(db_session, base_application, DocumentType.CARTE_IDENTITE)
    # On n'ajoute PAS RELEVE_NOTES ni PHOTO
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    is_complete, _, reasons = validator.validate(base_application)

    assert is_complete is True
    assert not any("RELEVE_NOTES" in r for r in reasons)
    assert not any("PHOTO" in r for r in reasons)


def test_dynamic_validator_detects_missing_required_doc(db_session, base_application, program_with_requirements):
    """DIPLOME fourni mais CARTE_IDENTITE manquante → dossier incomplet."""
    _add_valid_doc(db_session, base_application, DocumentType.DIPLOME)
    # CARTE_IDENTITE manquante
    db_session.refresh(base_application)

    validator = ApplicationValidator(db_session)
    is_complete, _, reasons = validator.validate(base_application)

    assert is_complete is False
    assert any("CARTE_IDENTITE" in r for r in reasons)


def test_dynamic_validator_fallback_when_no_program_in_db(db_session, university):
    """Aucun Program en base → fallback sur les 4 types hardcodés."""
    app = Application(
        id=uuid.uuid4(),
        university_id=university.id,
        student_phone="+22890999999",
        student_name="Test Fallback",
        program="Programme Inexistant en Base",
        status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state="COLLECT_DOCS",
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)

    validator = ApplicationValidator(db_session)
    is_complete, _, reasons = validator.validate(app)

    # Les 4 types hardcodés doivent être exigés
    assert is_complete is False
    assert any("DIPLOME" in r for r in reasons)
    assert any("RELEVE_NOTES" in r for r in reasons)
    assert any("CARTE_IDENTITE" in r for r in reasons)
    assert any("PHOTO" in r for r in reasons)


def test_dynamic_validator_fallback_when_no_required_docs_configured(db_session, university):
    """Programme en base mais aucun RequiredDocument → fallback hardcodé."""
    program = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name="Master Vide",
        is_active=True,
    )
    db_session.add(program)
    app = Application(
        id=uuid.uuid4(),
        university_id=university.id,
        student_phone="+22890888888",
        student_name="Test Vide",
        program="Master Vide",
        status=ApplicationStatus.COLLECTING_DOCUMENTS,
        conversation_state="COLLECT_DOCS",
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)

    validator = ApplicationValidator(db_session)
    _, _, reasons = validator.validate(app)

    # Fallback : les 4 types hardcodés sont exigés
    assert any("DIPLOME" in r for r in reasons)
    assert any("RELEVE_NOTES" in r for r in reasons)


def test_dynamic_validator_respects_order(db_session, base_application, program_with_requirements):
    """get_required_doc_types retourne les types dans l'ordre 'order' configuré."""
    _, req_docs = program_with_requirements  # order 0=DIPLOME, 1=CARTE_IDENTITE

    validator = ApplicationValidator(db_session)
    types = validator.get_required_doc_types(base_application)

    assert types[0] == DocumentType.DIPLOME
    assert types[1] == DocumentType.CARTE_IDENTITE
    assert len(types) == 2


def test_get_next_required_document_uses_dynamic_list():
    """get_next_required_document avec required_types personnalisé."""
    from app.services.whatsapp_bot import get_next_required_document
    from app.models.application import Application

    # Application mock minimale — pas besoin de DB ici
    mock_app = MagicMock(spec=Application)
    mock_app.documents = []  # aucun doc fourni

    custom_types = [DocumentType.CARTE_IDENTITE, DocumentType.DIPLOME]
    result = get_next_required_document(mock_app, required_types=custom_types)

    # Le premier dans la liste custom est CARTE_IDENTITE
    assert result == DocumentType.CARTE_IDENTITE
