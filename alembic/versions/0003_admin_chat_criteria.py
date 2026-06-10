"""Module Admin Chatbot — critères d'admission + sessions de chat

Ajoute :
  - table program_criteria (prérequis et critères d'admission par programme)
  - table admin_chat_sessions (historique/audit du chatbot admin)
  - colonne applications.average (moyenne académique normalisée, filtrage chatbot)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- program_criteria ----------
    op.create_table(
        "program_criteria",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "program_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("prerequisites", postgresql.JSONB(), nullable=True),
        sa.Column("min_average", sa.Numeric(4, 2), nullable=True),
        sa.Column("required_degree", sa.String(100), nullable=True),
        sa.Column("accepted_specialties", postgresql.JSONB(), nullable=True),
        sa.Column("additional_notes", sa.Text(), nullable=True),
        sa.Column(
            "whatsapp_display", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("updated_by_admin", sa.String(100), nullable=True),
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
        "ix_program_criteria_program_id", "program_criteria", ["program_id"]
    )

    # ---------- admin_chat_sessions ----------
    op.create_table(
        "admin_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "university_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("universities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("admin_identifier", sa.String(100), nullable=True),
        sa.Column("messages", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_admin_chat_sessions_university_id",
        "admin_chat_sessions",
        ["university_id"],
    )

    # ---------- applications.average ----------
    op.add_column(
        "applications", sa.Column("average", sa.Float(), nullable=True)
    )
    op.create_index("ix_applications_average", "applications", ["average"])


def downgrade() -> None:
    op.drop_index("ix_applications_average", table_name="applications")
    op.drop_column("applications", "average")
    op.drop_index(
        "ix_admin_chat_sessions_university_id", table_name="admin_chat_sessions"
    )
    op.drop_table("admin_chat_sessions")
    op.drop_index("ix_program_criteria_program_id", table_name="program_criteria")
    op.drop_table("program_criteria")
