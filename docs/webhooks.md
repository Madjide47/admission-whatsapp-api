# Guide d'Intégration des Webhooks

Ce guide est destiné aux développeurs des universités clientes. Il explique comment sécuriser et traiter les webhooks envoyés par l'API SaaS Admission WhatsApp.

## 1. Introduction

L'API fonctionne de manière **asynchrone**. Au lieu que vous deviez interroger l'API constamment pour savoir si un étudiant a soumis son dossier, notre système vous envoie une requête HTTP `POST` (un webhook) dès qu'un dossier complet et validé est prêt à être examiné.

**URL de destination :** C'est l'URL de votre serveur (ex: `https://api.votre-universite.com/webhooks/admission`) que vous nous fournissez lors de votre inscription.

---

## 2. Sécurité : Vérifier la Signature HMAC

Pour garantir que la requête provient bien de notre API et non d'un acteur malveillant, **chaque webhook est signé cryptographiquement**.

### Comment ça marche ?
1. Lors de votre inscription, vous recevez un `WEBHOOK_SECRET` (à garder secret).
2. Nous créons une chaîne de caractères contenant le timestamp et le corps de la requête.
3. Nous signons cette chaîne avec HMAC-SHA256 en utilisant votre `WEBHOOK_SECRET`.
4. Nous plaçons la signature dans le header `X-Webhook-Signature`.

### Headers reçus
```http
Content-Type: application/json; charset=utf-8
X-Webhook-Signature: sha256=abcdef1234567890abcdef...
X-Webhook-Timestamp: 1716200000
X-Webhook-Event-Id: 550e8400-e29b-41d4-a716-446655440000
```

### Exemple d'implémentation (Node.js)

Consultez le projet d'exemple dans le dossier `examples/university-client/` de notre dépôt GitHub pour une implémentation complète en Node.js (Express).

```javascript
const crypto = require('crypto');

function verifyWebhook(rawBody, signatureHeader, timestampHeader, secret) {
    const signature = signatureHeader.slice(7); // Enlever 'sha256='
    const ts = parseInt(timestampHeader, 10);
    
    // Anti-replay (5 minutes de tolérance)
    if (Math.abs(Date.now() / 1000 - ts) > 300) return false;

    // Concaténer le timestamp et le RAW body (Buffer)
    const message = Buffer.concat([Buffer.from(`${ts}.`), rawBody]);
    
    const expected = crypto.createHmac('sha256', secret).update(message).digest('hex');
    
    // Comparaison en temps constant
    return crypto.timingSafeEqual(Buffer.from(expected, 'hex'), Buffer.from(signature, 'hex'));
}
```

> [!WARNING]
> **Attention :** Ne parsez pas le JSON avant d'avoir vérifié la signature. La vérification HMAC doit se faire sur le **Buffer brut** (`raw body`), sinon le calcul du hash sera faussé par des espaces ou des retours à la ligne.

---

## 3. Idempotence et Retries

Le web n'est pas infaillible. Si votre serveur est indisponible ou s'il met trop de temps à répondre (timeout), notre API considérera que l'envoi a échoué.

### Politique de Retry
Nous réessaierons d'envoyer le webhook selon ce calendrier :
- 1 minute après l'échec initial
- 5 minutes plus tard
- 15 minutes plus tard
- 1 heure plus tard
- 6 heures plus tard (tentative finale)

### Idempotence (Éviter les doublons)
À cause des retries ou de problèmes réseau, **vous pourriez recevoir le même webhook plusieurs fois**.
Chaque événement possède un identifiant unique envoyé dans le header `X-Webhook-Event-Id` (et dans le champ `idempotency_key` du payload JSON).

**Règle d'or :** Enregistrez chaque `X-Webhook-Event-Id` traité dans votre base de données. Si vous recevez une requête avec un ID déjà connu, ignorez-la et renvoyez un code `200 OK`.

---

## 4. Structure du Payload (L'événement)

### Événement : `application.validated`
Cet événement est déclenché lorsqu'un étudiant a terminé son inscription via WhatsApp, que tous les documents ont été lus par l'OCR et validés par l'IA.

```json
{
  "event": "application.validated",
  "timestamp": 1716134400,
  "idempotency_key": "8a3f4b9c7d1e2f0a8c6b5d4e3f2a1098",
  "data": {
    "application_id": "f3a1b2c4-d5e6-7890-1234-56789abcdef0",
    "student": {
      "phone": "whatsapp:+22890123456",
      "name": "Jean Dupont"
    },
    "program": "Licence Informatique",
    "validation_score": 0.95,
    "ai_notes": "Dossier conforme. Les relevés de notes sont lisibles.",
    "documents": [
      {
        "type": "DIPLOME",
        "gcs_url": "https://storage.googleapis.com/... (URL temporaire)",
        "ocr_text": "UNIVERSITE DE LOME..."
      }
    ]
  }
}
```

> [!TIP]
> **Important :** Les URLs de documents (`gcs_url`) sont des URLs signées qui **expirent après 60 minutes**. Si vous avez besoin de stocker les documents, téléchargez-les immédiatement lors de la réception du webhook sur vos propres serveurs.

---

## 5. Prendre une Décision

Une fois le webhook reçu, le dossier est dans l'état `SENT_TO_UNIVERSITY`. C'est à vous de jouer.
Dans votre propre logiciel, un agent de scolarité va lire le dossier et prendre une décision.

Vous devez ensuite appeler notre API pour clôturer le dossier et avertir automatiquement l'étudiant sur WhatsApp.

**Requête :**
```http
POST https://api.notre-domaine.com/api/v1/applications/f3a1b2c4-d5e6-7890-1234-56789abcdef0/decision
X-API-Key: univ_...
X-API-Secret: sk_...
Content-Type: application/json

{
  "decision": "ACCEPTED",
  "comment": "Bienvenue dans notre université ! Les frais de scolarité sont à payer sous 7 jours."
}
```

Les décisions possibles sont : `ACCEPTED` ou `REJECTED`. L'étudiant recevra instantanément un message WhatsApp personnalisé !
