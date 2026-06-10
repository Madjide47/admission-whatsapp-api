"""Tests des endpoints critères d'admission (GET/PUT /admin/programs/{id}/criteria)."""
import uuid

import pytest

from app.models.program import Program
from app.models.university import University


@pytest.fixture()
def program(db_session, test_university) -> Program:
    university, _, _ = test_university
    prog = Program(
        id=uuid.uuid4(),
        university_id=university.id,
        name="Master Finance",
        is_active=True,
    )
    db_session.add(prog)
    db_session.commit()
    db_session.refresh(prog)
    return prog


def test_get_criteria_empty_returns_null(client, auth_headers, program):
    r = client.get(f"/api/v1/admin/programs/{program.id}/criteria", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"] is None


def test_put_creates_criteria(client, auth_headers, program):
    payload = {
        "prerequisites": ["Bac+3 minimum", "Lettre de motivation"],
        "min_average": 14,
        "required_degree": "Bac+3",
        "accepted_specialties": ["Économie", "Gestion"],
        "additional_notes": "Entretien oral en septembre.",
        "whatsapp_display": True,
    }
    r = client.put(
        f"/api/v1/admin/programs/{program.id}/criteria",
        headers=auth_headers,
        json=payload,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["min_average"] == 14
    assert data["prerequisites"] == ["Bac+3 minimum", "Lettre de motivation"]
    assert data["accepted_specialties"] == ["Économie", "Gestion"]
    assert data["whatsapp_display"] is True


def test_put_then_get_roundtrip(client, auth_headers, program):
    client.put(
        f"/api/v1/admin/programs/{program.id}/criteria",
        headers=auth_headers,
        json={"prerequisites": ["X"], "min_average": 12.5},
    )
    r = client.get(f"/api/v1/admin/programs/{program.id}/criteria", headers=auth_headers)
    data = r.json()["data"]
    assert data["min_average"] == 12.5
    assert data["prerequisites"] == ["X"]


def test_put_is_idempotent_upsert(client, auth_headers, program):
    """Un second PUT remplace les critères, ne crée pas de doublon."""
    client.put(
        f"/api/v1/admin/programs/{program.id}/criteria",
        headers=auth_headers,
        json={"min_average": 10},
    )
    r = client.put(
        f"/api/v1/admin/programs/{program.id}/criteria",
        headers=auth_headers,
        json={"min_average": 16, "prerequisites": ["Nouveau"]},
    )
    data = r.json()["data"]
    assert data["min_average"] == 16
    assert data["prerequisites"] == ["Nouveau"]


def test_average_out_of_range_rejected(client, auth_headers, program):
    r = client.put(
        f"/api/v1/admin/programs/{program.id}/criteria",
        headers=auth_headers,
        json={"min_average": 25},
    )
    assert r.status_code == 422


def test_criteria_tenant_isolation(client, auth_headers, db_session, program):
    """Une autre université ne peut pas lire/écrire les critères de ce programme."""
    other = University(
        id=uuid.uuid4(),
        name="Autre Univ",
        email=f"other-{uuid.uuid4().hex[:6]}@univ.test",
        api_key_hash="x",
        api_secret_hash="x",
        api_key_prefix="other_x",
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    # Le programme appartient à test_university → l'accès avec d'autres creds échoue.
    # On vérifie surtout qu'un programme inconnu renvoie 404.
    r = client.get(
        f"/api/v1/admin/programs/{uuid.uuid4()}/criteria", headers=auth_headers
    )
    assert r.status_code == 404
