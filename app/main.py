"""Point d'entrée FastAPI — API SaaS Admission WhatsApp.

Initialise l'application, monte les routes, configure middlewares,
rate limiting, monitoring, et gestionnaires d'erreurs globaux.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.v1.router import api_v1_router
from app.api.whatsapp.twilio_webhook import router as whatsapp_router
from app.config import settings
from app.openapi import build_openapi_schema, get_swagger_ui

# Importé pour ses effets de bord : instancier `celery_app` le définit comme
# application Celery courante, si bien que les `shared_task(...).delay()` lancés
# depuis l'API appliquent le routage (`task_routes`) et atterrissent dans les
# bonnes files (ocr/ai/webhooks). Sans ça, les tâches partent dans la file
# « celery » par défaut, que les workers n'écoutent pas.
from app.workers.celery_app import celery_app  # noqa: F401

# ---------------- Logging ----------------
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("admission.api")


# ---------------- Sentry ----------------
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )
    logger.info("Sentry monitoring activé")


# ---------------- Rate limiter ----------------
# En dev/test on utilise le stockage mémoire pour éviter de dépendre de Redis
_limiter_storage = settings.REDIS_URL if settings.is_production else "memory://"
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=_limiter_storage,
)


# ---------------- Lifespan ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Hooks de démarrage et d'arrêt de l'application."""
    logger.info(
        "Démarrage de l'API Admission WhatsApp — version %s, env=%s",
        __version__,
        settings.ENVIRONMENT,
    )
    yield
    logger.info("Arrêt de l'API Admission WhatsApp")


# ---------------- App ----------------
app = FastAPI(
    title="API SaaS Admission WhatsApp",
    description=(
        "API d'admission universitaire pilotée par WhatsApp. "
        "Collecte de candidatures, validation OCR + IA, "
        "et webhooks signés HMAC vers les universités clientes."
    ),
    version=__version__,
    docs_url=None,
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)


def custom_openapi():
    return build_openapi_schema(app)


app.openapi = custom_openapi  # type: ignore[method-assign]

# Attacher le rate limiter à l'app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS — strict en prod, permissif en dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


# ---------------- Handlers d'erreurs globaux ----------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Erreurs de validation Pydantic — retour structuré uniforme."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Les données envoyées sont invalides.",
                "details": exc.errors(),
            },
        },
    )


@app.exception_handler(SQLAlchemyError)
async def db_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Erreur DB — on log mais on n'expose pas les détails au client."""
    logger.exception("Erreur base de données: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "DATABASE_ERROR",
                "message": "Erreur interne du serveur. Veuillez réessayer.",
            },
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Filet de sécurité — toute exception non capturée arrive ici."""
    logger.exception("Exception non gérée: %s", exc)
    if settings.SENTRY_DSN:
        sentry_sdk.capture_exception(exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Une erreur inattendue s'est produite.",
            },
        },
    )


# ---------------- Routes ----------------
app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(whatsapp_router, prefix="/whatsapp", tags=["WhatsApp"])


if not settings.is_production:

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        """Swagger UI avec security schemes et credentials pré-remplis."""
        return get_swagger_ui()


@app.get("/health", tags=["System"])
async def health() -> dict:
    """Endpoint de santé — utilisé par les load balancers."""
    return {"status": "ok", "version": __version__}


# Tableau de bord interne (page statique qui consomme l'API — même origine, pas de CORS).
# Outil de suivi/démo ; les données ne s'affichent qu'avec une API Key + Secret valides.
_DASHBOARD_FILE = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_DASHBOARD_FILE, media_type="text/html")


# Liste des identifiants universités (DÉMO/DEV UNIQUEMENT — désactivé en production).
# Permet au tableau de bord de proposer un menu « Se connecter en tant que… ».
_DEV_CREDS_FILE = Path(__file__).parent / "static" / "dev_credentials.json"

if not settings.is_production:

    @app.get("/dev/credentials", include_in_schema=False)
    async def dev_credentials():
        if _DEV_CREDS_FILE.exists():
            return FileResponse(_DEV_CREDS_FILE, media_type="application/json")
        return JSONResponse([], status_code=200)


@app.get("/", tags=["system"], include_in_schema=False)
async def root() -> dict:
    return {
        "service": "admission-whatsapp-api",
        "version": __version__,
        "docs": "/docs" if not settings.is_production else None,
    }
