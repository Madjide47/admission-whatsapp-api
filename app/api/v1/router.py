"""Routeur principal /api/v1 — regroupe tous les sous-routeurs."""
from fastapi import APIRouter

from app.api.v1 import applications, decisions, documents, webhooks
from app.api.v1 import admin_chat, admin_programs, admin_seed

api_v1_router = APIRouter()

api_v1_router.include_router(
    applications.router, prefix="/applications", tags=["Applications"]
)
api_v1_router.include_router(
    documents.router, prefix="/applications", tags=["Documents"]
)
api_v1_router.include_router(
    decisions.router, prefix="/applications", tags=["Decisions"]
)
api_v1_router.include_router(
    webhooks.router, prefix="/webhooks", tags=["Webhooks"]
)
api_v1_router.include_router(
    admin_programs.router, prefix="/admin", tags=["Admin"]
)
api_v1_router.include_router(
    admin_seed.router, prefix="/admin", tags=["Admin"]
)
api_v1_router.include_router(
    admin_chat.router, prefix="/admin", tags=["Admin Chatbot"]
)
