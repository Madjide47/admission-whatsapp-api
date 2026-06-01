"""Initial schema — universities, applications, documents, webhook_deliveries

Revision ID: 0001
Revises:
Create Date: 2026-05-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # ---------- universities ----------
    op.create_table(
        "universities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(255), nullable=False),
        sa.Column("api_secret_hash", sa.String(255), nullable=False),
        sa.Column("api_key_prefix", sa.String(20), nullable=False),
        sa.Column("webhook_url", sa.String(500), nullable=True),
        sa.Column("webhook_secret", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    op.create_index("ix_universities_name", "universities", ["name"])
    op.create_index("ix_universities_email", "universities", ["email"], unique=True)
    op.create_index(
        "ix_universities_api_key_hash", "universities", ["api_key_hash"], unique=True
    )
    op.create_index("ix_universities_api_key_prefix", "universities", ["api_key_prefix"])

    # ---------- applications ----------
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "university_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("universities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("student_phone", sa.String(32), nullable=False),
        sa.Column("student_name", sa.String(255), nullable=True),
        sa.Column("student_email", sa.String(255), nullable=True),
        sa.Column("program", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "COLLECTING",
                "VALIDATING",
                "VALIDATED",
                "SENT_TO_UNIVERSITY",
                "ACCEPTED",
                "REJECTED",
                name="application_status_enum",
            ),
            nullable=False,
            server_default="COLLECTING",
        ),
        sa.Column("validation_score", sa.Float(), nullable=True),
        sa.Column("ai_notes", sa.Text(), nullable=True),
        sa.Column("conversation_state", sa.String(50), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_applications_university_id", "applications", ["university_id"])
    op.create_index("ix_applications_student_phone", "applications", ["student_phone"])
    op.create_index("ix_applications_program", "applications", ["program"])
    op.create_index("ix_applications_status", "applications", ["status"])
    op.create_index("ix_applications_created_at", "applications", ["created_at"])

    # ---------- documents ----------
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_type",
            sa.Enum(
                "DIPLOME",
                "RELEVE_NOTES",
                "CARTE_IDENTITE",
                "PHOTO",
                "AUTRE",
                name="document_type_enum",
            ),
            nullable=False,
            server_default="AUTRE",
        ),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("gcs_path", sa.String(500), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("classification_result", postgresql.JSONB(), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_application_id", "documents", ["application_id"])
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index("ix_documents_uploaded_at", "documents", ["uploaded_at"])

    # ---------- webhook_deliveries ----------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "university_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("universities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SUCCESS",
                "FAILED",
                name="webhook_status_enum",
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body_snippet", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
    op.create_index(
        "ix_webhook_deliveries_university_id", "webhook_deliveries", ["university_id"]
    )
    op.create_index(
        "ix_webhook_deliveries_application_id", "webhook_deliveries", ["application_id"]
    )
    op.create_index(
        "ix_webhook_deliveries_event_type", "webhook_deliveries", ["event_type"]
    )
    op.create_index(
        "ix_webhook_deliveries_idempotency_key",
        "webhook_deliveries",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_webhook_deliveries_status", "webhook_deliveries", ["status"]
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("documents")
    op.drop_table("applications")
    op.drop_table("universities")
    op.execute("DROP TYPE IF EXISTS webhook_status_enum")
    op.execute("DROP TYPE IF EXISTS document_type_enum")
    op.execute("DROP TYPE IF EXISTS application_status_enum")
