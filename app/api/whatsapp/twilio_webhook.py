"""Endpoint /whatsapp/incoming — recevoir les messages Twilio.

Twilio envoie un POST x-www-form-urlencoded avec :
  - From : numéro de l'expéditeur (whatsapp:+228...)
  - Body : texte du message
  - NumMedia : nombre de médias joints (0 ou 1+)
  - MediaUrl0 / MediaContentType0 : 1er média joint
  - To : numéro Twilio de destination (notre numéro WhatsApp)

On répond en TwiML vide — les réponses sont envoyées via l'API REST Twilio
depuis le service WhatsAppBot pour rester en contrôle de la conversation.
"""
import base64
import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services.whatsapp_bot import WhatsAppBot

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_twilio_signature(request: Request, params: dict[str, str]) -> bool:
    """Vérifie la signature X-Twilio-Signature (HMAC-SHA1).

    https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        return False

    # Twilio signe : URL complète + concaténation triée des paires clé=valeur
    url = str(request.url)
    sorted_pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    data = (url + sorted_pairs).encode("utf-8")
    expected = base64.b64encode(
        hmac.new(settings.TWILIO_AUTH_TOKEN.encode("utf-8"), data, hashlib.sha1).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature)


@router.post(
    "/incoming",
    summary="Endpoint Twilio WhatsApp inbound — appelé par Twilio à chaque message",
    response_class=Response,
)
async def twilio_incoming(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    From: Annotated[str, Form()],
    To: Annotated[str, Form()],
    Body: Annotated[str, Form()] = "",
    NumMedia: Annotated[str, Form()] = "0",
    MediaUrl0: Annotated[str | None, Form()] = None,
    MediaContentType0: Annotated[str | None, Form()] = None,
) -> Response:
    # Reconstruire les paramètres pour la vérif de signature
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    # En production, on REFUSE tout message dont la signature est invalide.
    # En dev, on accepte tout pour faciliter les tests.
    if settings.is_production and not _verify_twilio_signature(request, params):
        logger.warning("Signature Twilio invalide — message rejeté")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signature Twilio invalide.",
        )

    logger.info(
        "Message WhatsApp reçu de %s — body=%r media=%s",
        From,
        Body[:80],
        NumMedia,
    )

    bot = WhatsAppBot(db)
    bot.handle_incoming_message(
        from_number=From,
        message_body=Body,
        media_url=MediaUrl0 if int(NumMedia or 0) > 0 else None,
        media_content_type=MediaContentType0,
    )

    # Réponse TwiML vide — on a déjà répondu via l'API REST
    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=twiml, media_type="application/xml")
