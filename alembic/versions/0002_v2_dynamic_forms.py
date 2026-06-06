"""Schéma v2 — programmes, formulaires dynamiques, valeurs de champs

Ajoute :
  - tables : programs, admission_forms, form_fields, required_documents,
             application_field_values
  - colonne applications.program_id (FK -> programs, SET NULL)
  - nouvelles valeurs de l'enum application_status_enum (flux v2)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Nouvelles valeurs ajoutées à l'enum statut (le flux v2 a remplacé COLLECTING)
_NEW_STATUS_VALUES = (
    "CHOOSING_UNIVERSITY",
    "CHOOSING_PROGRAM",
    "COLLECTING_FIELDS",
    "COLLECTING_DOCUMENTS",
    "PENDING_ENROLLMENT",
)


def upgrade() -> None:
    # ---------- enum : nouvelles valeurs de statut ----------
    # ADD VALUE IF NOT EXISTS est supporté par PostgreSQL 9.6+ et peut tourner
    # dans une transaction depuis PG 12 (la version cible du projet est 15+).
    for value in _NEW_STATUS_VALUES:
        op.execute(
            f"ALTER TYPE application_status_enum ADD VALUE IF NOT EXISTS '{value}'"
        )

    # ---------- programs ----------
    op.create_table(
        "programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "university_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("universities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("enrollment_start", sa.Date(), nullable=True),
        sa.Column("enrollment_end", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_programs_university_id", "programs", ["university_id"])
    op.create_index("ix_programs_domain", "programs", ["domain"])

    # ---------- admission_forms ----------
    op.create_table(
        "admission_forms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "program_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_admission_forms_program_id", "admission_forms", ["program_id"])

    # ---------- form_fields ----------
    op.create_table(
        "form_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "form_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admission_forms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("field_type", sa.String(50), nullable=False, server_default="text"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("validation_regex", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_form_fields_form_id", "form_fields", ["form_id"])

    # ---------- required_documents ----------
    op.create_table(
        "required_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "form_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admission_forms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index(
        "ix_required_documents_form_id", "required_documents", ["form_id"]
    )

    # ---------- applications.program_id ----------
    op.add_column(
        "applications",
        sa.Column(
            "program_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_applications_program_id", "applications", ["program_id"])

    # ---------- application_field_values ----------
    op.create_table(
        "application_field_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "field_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("form_fields.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_application_field_values_application_id",
        "application_field_values",
        ["application_id"],
    )
    op.create_index(
        "ix_application_field_values_field_id",
        "application_field_values",
        ["field_id"],
    )


def downgrade() -> None:
    op.drop_table("application_field_values")
    op.drop_index("ix_applications_program_id", table_name="applications")
    op.drop_column("applications", "program_id")
    op.drop_table("required_documents")
    op.drop_table("form_fields")
    op.drop_table("admission_forms")
    op.drop_table("programs")
    # Note : PostgreSQL ne permet pas de retirer une valeur d'un enum.
    # Les valeurs ajoutées à application_status_enum restent en place.
