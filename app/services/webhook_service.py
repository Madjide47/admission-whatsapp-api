"""Service d'envoi de webhooks signés HMAC vers les universités."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.university import University
from app.models.webhook_log import WebhookDelivery, WebhookStatus
from app.utils.hmac_signer import sign_payload

logger = logging.getLogger(__name__)


class WebhookService:
    """Construit, signe et envoie des webhooks aux universités clientes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------------- Création / persistance ----------------
    def create_delivery(
        self,
        university: University,
        event_type: str,
        data: dict[str, Any],
        application_id: uuid.UUID | None = None,
    ) -> WebhookDelivery:
        """Crée un log WebhookDelivery en statut PENDING.

        L'envoi effectif est délégué au worker Celery.
        """
        idempotency_key = uuid.uuid4().hex

        envelope = {
            "event": event_type,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
            "idempotency_key": idempotency_key,
            "data": data,
        }

        delivery = WebhookDelivery(
            university_id=university.id,
            application_id=application_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=envelope,
            status=WebhookStatus.PENDING,
        )
        self.db.add(delivery)
        self.db.commit()
        self.db.refresh(delivery)
        return delivery

    # ---------------- Envoi HTTP ----------------
    def send(self, delivery: WebhookDelivery) -> bool:
        """Envoie effectivement le webhook au endpoint configuré.

        Retourne True en cas de succès (2xx), False sinon.
        Met à jour le log de tentatives en base.
        """
        university = self.db.get(University, delivery.university_id)
        if university is None or not university.webhook_url or not university.webhook_secret:
            self._mark_failed(delivery, error="Webhook non configuré pour cette université.")
            return False

        signature, ts, body = sign_payload(
            secret=university.webhook_secret,
            payload=delivery.payload,
            timestamp=delivery.payload.get("timestamp"),
        )

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "AdmissionWhatsApp-Webhook/1.0",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Webhook-Timestamp": str(ts),
            "X-Webhook-Event": delivery.event_type,
            "X-Webhook-Event-Id": delivery.idempotency_key,
        }

        delivery.attempt_count += 1
        delivery.last_attempt_at = datetime.now(timezone.utc)

        try:
            with httpx.Client(timeout=settings.WEBHOOK_TIMEOUT) as client:
                response = client.post(university.webhook_url, content=body, headers=headers)
            delivery.response_status_code = response.status_code
            delivery.response_body_snippet = response.text[:500]

            if 200 <= response.status_code < 300:
                delivery.status = WebhookStatus.SUCCESS
                delivery.last_error = None
                self.db.add(delivery)
                self.db.commit()
                logger.info(
                    "Webhook %s livré à %s (event=%s, attempt=%d)",
                    delivery.id,
                    university.webhook_url,
                    delivery.event_type,
                    delivery.attempt_count,
                )
                return True

            delivery.last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            self.db.add(delivery)
            self.db.commit()
            logger.warning(
                "Webhook %s a échoué (HTTP %d) — tentative %d",
                delivery.id,
                response.status_code,
                delivery.attempt_count,
            )
            return False

        except httpx.HTTPError as e:
            delivery.last_error = f"Erreur réseau : {e}"
            self.db.add(delivery)
            self.db.commit()
            logger.exception("Erreur réseau webhook %s: %s", delivery.id, e)
            return False

    def _mark_failed(self, delivery: WebhookDelivery, error: str) -> None:
        delivery.status = WebhookStatus.FAILED
        delivery.last_error = error
        delivery.last_attempt_at = datetime.now(timezone.utc)
        self.db.add(delivery)
        self.db.commit()


def get_webhook_service(db: Session) -> WebhookService:
    return WebhookService(db)
