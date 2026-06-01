"""Routeur principal /api/v1 — regroupe tous les sous-routeurs."""
from fastapi import APIRouter

from app.api.v1 import applications, decisions, documents, webhooks

api_v1_router = APIRouter()

api_v1_router.include_router(
    applications.router, prefix="/applications", tags=["applications"]
)
api_v1_router.include_router(
    documents.router, prefix="/applications", tags=["documents"]
)
api_v1_router.include_router(
    decisions.router, prefix="/applications", tags=["decisions"]
)
api_v1_router.include_router(
    webhooks.router, prefix="/webhooks", tags=["webhooks"]
)
