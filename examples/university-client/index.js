const express = require('express');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 3000;

// Le secret vous a été donné lors de la configuration de votre université via l'API.
// IMPORTANT: Ne le mettez jamais en dur en production.
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || 'votre_secret_webhook_ici';
const TOLERANCE_SECONDS = 300;

// Un Set simple pour gérer l'idempotence (en mémoire pour l'exemple)
// En production, utilisez une vraie base de données ou Redis
const processedEvents = new Set();

/**
 * Fonction pour vérifier la signature HMAC du webhook
 */
function verifyWebhook(rawBody, signatureHeader, timestampHeader) {
    if (!signatureHeader || !signatureHeader.startsWith('sha256=')) {
        return false;
    }
    
    const signature = signatureHeader.slice(7);
    const ts = parseInt(timestampHeader, 10);
    
    // Protection anti-replay (vérifie que le message n'est pas trop vieux)
    if (Math.abs(Date.now() / 1000 - ts) > TOLERANCE_SECONDS) {
        console.warn('⚠️ Webhook expiré (timestamp trop ancien ou trop dans le futur)');
        return false;
    }

    // Le message signé par le serveur Admission est : "timestamp.rawBody"
    const message = Buffer.concat([Buffer.from(`${ts}.`), rawBody]);
    
    // Calcul du hash attendu
    const expected = crypto
        .createHmac('sha256', WEBHOOK_SECRET)
        .update(message)
        .digest('hex');

    // Comparaison en temps constant pour éviter les attaques par timing
    return crypto.timingSafeEqual(
        Buffer.from(expected, 'hex'),
        Buffer.from(signature, 'hex')
    );
}

// Nous devons parser le corps de la requête en RAW (Buffer) pour vérifier la signature
app.post('/webhooks/admission', express.raw({ type: '*/*' }), (req, res) => {
    const signatureHeader = req.header('X-Webhook-Signature');
    const timestampHeader = req.header('X-Webhook-Timestamp');
    const eventId = req.header('X-Webhook-Event-Id');

    console.log(`\n--- 📥 Réception d'un webhook (ID: ${eventId}) ---`);

    // 1. Vérification de la signature
    const isValid = verifyWebhook(req.body, signatureHeader, timestampHeader);
    if (!isValid) {
        console.error('❌ Signature invalide ! Requête rejetée.');
        return res.status(403).json({ error: 'Signature invalide' });
    }
    console.log('✅ Signature vérifiée avec succès.');

    // 2. Gestion de l'idempotence (Anti-doublons)
    if (processedEvents.has(eventId)) {
        console.log(`⚠️ Événement ${eventId} déjà traité. On ignore et on renvoie 200 OK.`);
        return res.status(200).send('OK');
    }
    processedEvents.add(eventId);

    // 3. Traitement du payload
    try {
        const payload = JSON.parse(req.body.toString('utf8'));
        console.log(`📌 Événement : ${payload.event}`);
        
        if (payload.event === 'application.validated') {
            const application = payload.data;
            console.log(`👨‍🎓 Étudiant : ${application.student.name} (${application.student.phone})`);
            console.log(`📚 Programme : ${application.program}`);
            console.log(`🤖 Score IA  : ${application.validation_score * 100}%`);
            
            // SIMULATION: Logique métier de l'université
            // Ici, l'université analyse le dossier et envoie sa décision à l'API Admission.
            // (Voir la doc pour l'endpoint POST /api/v1/applications/{id}/decision)
            console.log(`⏳ L'université a enregistré la candidature. Décision en cours...`);
        }

        // On renvoie un 200 OK pour accuser réception (sinon le serveur Admission va retry)
        res.status(200).json({ received: true });
        
    } catch (e) {
        console.error('Erreur lors du parsing JSON:', e);
        res.status(400).send('Bad Request');
    }
});

app.listen(PORT, () => {
    console.log(`🚀 Serveur universitaire de test en écoute sur le port ${PORT}`);
    console.log(`Configurez l'URL de votre webhook vers : http://localhost:${PORT}/webhooks/admission`);
});
