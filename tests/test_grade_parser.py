"""Tests du parseur de moyenne académique (normalisation sur 20)."""
import pytest

from app.services.grade_parser import extract_average_from_fields, parse_average


@pytest.mark.parametrize(
    "value,expected",
    [
        ("14.5/20", 14.5),
        ("14,5", 14.5),
        ("14.5", 14.5),
        (14.5, 14.5),
        (16, 16.0),
        ("70/100", 14.0),
        ("3.5/4", 17.5),
        ("Moyenne générale : 12.75/20", 12.75),
        ("", None),
        (None, None),
        ("aucune note", None),
        ("999", None),       # > 100 → hors échelle plausible
        ("25/20", None),     # normalisée > 20 → rejetée
    ],
)
def test_parse_average(value, expected):
    assert parse_average(value) == expected


def test_extract_from_additional():
    fields = {
        "student_name": "Kofi",
        "additional": {"moyenne": "15.2/20", "serie": "D"},
    }
    assert extract_average_from_fields(fields) == 15.2


def test_extract_from_top_level_key():
    fields = {"moyenne_generale": "13/20", "additional": {}}
    assert extract_average_from_fields(fields) == 13.0


def test_extract_none_when_absent():
    fields = {"student_name": "Kofi", "additional": {"serie": "D"}}
    assert extract_average_from_fields(fields) is None


def test_extract_empty():
    assert extract_average_from_fields(None) is None
    assert extract_average_from_fields({}) is None
