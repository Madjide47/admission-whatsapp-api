"""Configuration OpenAPI / Swagger UI — périmètre Dev 3.

- Security schemes API Key + Secret
- Tags groupés par domaine
- Pré-remplissage des credentials en dev (via .env)
"""
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from starlette.responses import HTMLResponse

from app.config import settings

OPENAPI_TAGS = [
    {
        "name": "Admin",
        "description": (
            "Configuration des programmes, formulaires dynamiques et seed de test. "
            "Endpoints livrés par Dev 1 (v2)."
        ),
    },
    {
        "name": "Applications",
        "description": "Création, consultation et mise à jour des candidatures.",
    },
    {
        "name": "Documents",
        "description": "Upload de documents pour une candidature (déclenche OCR + IA).",
    },
    {
        "name": "Decisions",
        "description": "Enregistrement des décisions d'admission (ACCEPTED / REJECTED).",
    },
    {
        "name": "Webhooks",
        "description": "Configuration et historique des livraisons webhook.",
    },
    {
        "name": "WhatsApp",
        "description": "Webhook entrant Twilio — appelé automatiquement par Twilio.",
    },
    {
        "name": "System",
        "description": "Santé et métadonnées du service.",
    },
]

SECURITY_SCHEMES = {
    "ApiKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Clé API université (format : `univ_<32 caractères>`).",
    },
    "ApiSecretAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Secret",
        "description": "Secret API université (format : `sk_<64 caractères>`).",
    },
}

# Chemins publics — pas de security scheme dans OpenAPI
_PUBLIC_PREFIXES = ("/health", "/whatsapp/", "/api/v1/admin/seed-university")


def _requires_auth(path: str) -> bool:
    if not path.startswith("/api/v1/"):
        return False
    return not any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def build_openapi_schema(app: FastAPI) -> dict:
    """Génère le schéma OpenAPI avec security schemes et tags ordonnés."""
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
    )

    components = schema.setdefault("components", {})
    components["securitySchemes"] = SECURITY_SCHEMES

    for path, path_item in schema.get("paths", {}).items():
        if not _requires_auth(path):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict) and "security" not in operation:
                operation["security"] = [{"ApiKeyAuth": [], "ApiSecretAuth": []}]

    app.openapi_schema = schema
    return schema


def _prefill_script() -> str:
    """Injecte un script qui pré-remplit Authorize dans Swagger UI."""
    key = settings.SWAGGER_DEMO_API_KEY.strip()
    secret = settings.SWAGGER_DEMO_API_SECRET.strip()
    if not key or not secret:
        return ""

    return f"""
<script>
(function () {{
  function preauthorize() {{
    if (!window.ui) return false;
    window.ui.preauthorizeApiKey("ApiKeyAuth", {key!r});
    window.ui.preauthorizeApiKey("ApiSecretAuth", {secret!r});
    return true;
  }}
  var attempts = 0;
  var timer = setInterval(function () {{
    if (preauthorize() || ++attempts > 50) clearInterval(timer);
  }}, 100);
}})();
</script>
"""


def get_swagger_ui() -> HTMLResponse:
    """Page Swagger UI avec persistance auth et pré-remplissage optionnel."""
    base_html = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{settings.ENVIRONMENT} — Admission WhatsApp API",
        swagger_ui_parameters={
            "persistAuthorization": True,
            "displayRequestDuration": True,
            "filter": True,
            "tryItOutEnabled": True,
        },
    )
    inject = _prefill_script()
    if inject:
        body = base_html.body.decode("utf-8").replace("</body>", inject + "</body>")
        return HTMLResponse(content=body)
    return HTMLResponse(content=base_html.body)
