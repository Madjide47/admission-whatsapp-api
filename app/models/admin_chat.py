"""Modèle AdminChatSession — historique des sessions du chatbot admin.

Sert d'audit trail (chaque appel d'outil journalisé) et de contexte de
conversation persistant. Le contexte court vit aussi en Redis (TTL 2h) ; cette
table garde la trace durable.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, JsonB


class AdminChatSession(Base):
    """Une session de chat admin (échanges + appels d'outils journalisés)."""

    __tablename__ = "admin_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    university_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    admin_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Tableau des échanges : {role, content, tool_calls, timestamp}
    messages: Mapped[list | None] = mapped_column(JsonB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AdminChatSession id={self.id} university_id={self.university_id}>"
