"""Orchestrateur du chatbot admin — traduit le langage naturel en appels d'outils.

Conception :
  - La logique d'orchestration (dispatch, confirmation dry_run, sessions,
    scoping multi-tenant) est pure et testable sans appeler le LLM.
  - L'appel au modèle est isolé derrière une interface `LLMClient` (méthode
    `run`) → mockable en test. L'implémentation réelle utilise Gemini
    (function calling), car la clé Anthropic ne fonctionne pas dans ce projet.

Sécurité :
  - university_id injecté dans CHAQUE appel d'outil (jamais fourni par le LLM).
  - Les outils d'écriture passent toujours par un dry_run + confirmation
    explicite (pending_action) avant exécution réelle.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.models.university import University
from app.services import admin_chat_tools as tools

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5
HISTORY_TURNS = 3  # nombre de tours conservés pour le contexte de suivi


# ----------------------------------------------------------------------
# Spécification des outils (déclaration LLM + allowlist des paramètres)
# ----------------------------------------------------------------------
# kind : "read" (exécuté directement) | "write" (dry_run + confirmation)
TOOL_SPECS: dict[str, dict] = {
    "search_applications": {
        "kind": "read",
        "func": tools.search_applications,
        "description": "Recherche et filtre les candidatures, classées du meilleur au moins bon.",
        "params": {
            "program_name": "Nom du programme (optionnel)",
            "status": "Statut : CHOOSING_PROGRAM, COLLECTING_DOCUMENTS, VALIDATED, "
                      "SENT_TO_UNIVERSITY, ACCEPTED, REJECTED, etc. (optionnel)",
            "min_score": "Score IA minimum 0.0–1.0 (optionnel)",
            "min_average": "Moyenne académique minimum sur 20 (optionnel)",
            "days_since_last_update": "Inactives depuis N jours (optionnel)",
            "limit": "Nombre max de résultats (défaut 20, max 100)",
        },
    },
    "get_application_detail": {
        "kind": "read",
        "func": tools.get_application_detail,
        "description": "Détail complet d'une candidature (champs, documents, scores).",
        "params": {"application_id": "UUID de la candidature"},
    },
    "get_stats": {
        "kind": "read",
        "func": tools.get_stats,
        "description": "Métriques agrégées par période et/ou regroupement.",
        "params": {
            "period": "today | week | month | all",
            "group_by": "program | status (optionnel)",
        },
    },
    "get_program_criteria": {
        "kind": "read",
        "func": tools.get_program_criteria,
        "description": "Critères d'admission configurés pour un programme.",
        "params": {"program_name": "Nom du programme"},
    },
    "set_program_criteria": {
        "kind": "write",
        "func": tools.set_program_criteria,
        "description": "Crée/met à jour les prérequis et critères d'admission d'un programme.",
        "params": {
            "program_name": "Nom exact du programme",
            "prerequisites": "Liste de prérequis textuels",
            "min_average": "Moyenne minimale requise sur 20",
            "required_degree": "Niveau de diplôme requis (ex : Bac+3)",
            "accepted_specialties": "Liste de spécialités acceptées",
            "additional_notes": "Notes complémentaires",
            "whatsapp_display": "Afficher dans le bot WhatsApp (bool)",
        },
    },
    "bulk_decide": {
        "kind": "write",
        "func": tools.bulk_decide,
        "description": "Applique une décision ACCEPTED/REJECTED à un ensemble de candidatures filtrées.",
        "params": {
            "decision": "ACCEPTED | REJECTED",
            "reason": "Motif transmis à l'étudiant (optionnel)",
            "program_name": "Filtre programme (optionnel)",
            "status": "Filtre statut (optionnel)",
            "min_score": "Filtre score IA minimum (optionnel)",
            "min_average": "Filtre moyenne minimum (optionnel)",
        },
    },
    "send_whatsapp_notification": {
        "kind": "write",
        "func": tools.send_whatsapp_notification,
        "description": "Envoie un message WhatsApp personnalisé aux candidatures filtrées.",
        "params": {
            "message_template": "Message avec variables {student_name}, {program_name}, {status}",
            "program_name": "Filtre programme (optionnel)",
            "status": "Filtre statut (optionnel)",
            "min_score": "Filtre score IA minimum (optionnel)",
            "min_average": "Filtre moyenne minimum (optionnel)",
        },
    },
}

READ_TOOLS = {n for n, s in TOOL_SPECS.items() if s["kind"] == "read"}
WRITE_TOOLS = {n for n, s in TOOL_SPECS.items() if s["kind"] == "write"}


def _filter_args(name: str, args: dict) -> dict:
    """Ne garde que les paramètres déclarés pour cet outil (LLM parfois bruité)."""
    allowed = set(TOOL_SPECS[name]["params"].keys())
    return {k: v for k, v in (args or {}).items() if k in allowed}


def dispatch_read(db: Session, university_id: uuid.UUID, name: str, args: dict) -> dict:
    """Exécute un outil de lecture (university_id injecté)."""
    func = TOOL_SPECS[name]["func"]
    return func(db, university_id, **_filter_args(name, args))


def dispatch_write(
    db: Session, university_id: uuid.UUID, name: str, args: dict, *, dry_run: bool
) -> dict:
    """Exécute un outil d'écriture en mode aperçu (dry_run) ou réel."""
    func = TOOL_SPECS[name]["func"]
    return func(db, university_id, dry_run=dry_run, **_filter_args(name, args))


# ----------------------------------------------------------------------
# Rendu lisible
# ----------------------------------------------------------------------
def render_preview(name: str, args: dict, preview: dict) -> str:
    """Message de confirmation présenté avant exécution d'une écriture."""
    if "error" in preview:
        return f"⚠️ {preview['error']}"
    if name == "bulk_decide":
        count = preview.get("count", 0)
        decision = preview.get("decision", "")
        verbe = "accepter" if decision == "ACCEPTED" else "refuser"
        return (
            f"Je vais **{verbe} {count} candidature(s)**. Confirmez-vous ?"
            if count
            else "Aucune candidature ne correspond à ces critères."
        )
    if name == "send_whatsapp_notification":
        count = preview.get("count", 0)
        return (
            f"Je vais envoyer ce message à **{count} étudiant(s)**. Confirmez-vous ?"
            if count
            else "Aucun destinataire ne correspond à ces critères."
        )
    if name == "set_program_criteria":
        would = preview.get("would_set", {})
        return (
            f"Je vais définir les critères du programme **{would.get('program', '')}** "
            f"(moyenne min : {would.get('min_average')}, "
            f"prérequis : {', '.join(would.get('prerequisites') or []) or '—'}). Confirmez-vous ?"
        )
    return "Confirmez-vous cette action ?"


def render_action_result(name: str, result: dict) -> str:
    """Message après exécution réelle d'une écriture."""
    if "error" in result:
        return f"⚠️ {result['error']}"
    if name == "bulk_decide":
        return f"✅ {result.get('decided', 0)} candidature(s) {result.get('decision', '')}."
    if name == "send_whatsapp_notification":
        return f"✅ Message envoyé à {result.get('sent', 0)} étudiant(s)."
    if name == "set_program_criteria":
        return "✅ Critères d'admission enregistrés."
    return "✅ Action effectuée."


# ----------------------------------------------------------------------
# Sessions (Redis avec repli mémoire)
# ----------------------------------------------------------------------
_MEMORY_SESSIONS: dict[str, dict] = {}
SESSION_TTL_SECONDS = 2 * 60 * 60  # 2h


def _redis_client():
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        client.ping()
        return client
    except Exception:
        return None


def load_session(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(f"admin_chat:{session_id}")
            return json.loads(raw) if raw else None
        except Exception:
            logger.warning("Lecture session Redis échouée", exc_info=True)
    return _MEMORY_SESSIONS.get(session_id)


def save_session(session: dict) -> None:
    session_id = session["session_id"]
    # On ne garde que les derniers tours pour limiter le coût LLM.
    session["history"] = session.get("history", [])[-(HISTORY_TURNS * 2):]
    client = _redis_client()
    if client is not None:
        try:
            client.setex(
                f"admin_chat:{session_id}", SESSION_TTL_SECONDS, json.dumps(session)
            )
            return
        except Exception:
            logger.warning("Écriture session Redis échouée", exc_info=True)
    _MEMORY_SESSIONS[session_id] = session


# ----------------------------------------------------------------------
# Résultat d'orchestration
# ----------------------------------------------------------------------
@dataclass
class ChatResult:
    session_id: str
    message: str
    data_table: list | None = None
    pending_action: dict | None = None
    tool_used: str | None = None


# ----------------------------------------------------------------------
# Interface LLM (mockable) + résultat normalisé
# ----------------------------------------------------------------------
@dataclass
class LLMResult:
    text: str | None = None
    tool_calls: list = field(default_factory=list)  # [{"name", "args"}]


class LLMClient:
    """Interface minimale. `run` renvoie un LLMResult (texte OU appels d'outils)."""

    def run(self, system_prompt: str, history: list, tool_specs: dict) -> LLMResult:  # pragma: no cover
        raise NotImplementedError


def build_system_prompt(university: University, program_names: list[str]) -> str:
    from datetime import date

    programs = ", ".join(program_names) if program_names else "(aucun)"
    return (
        "Tu es l'assistant administratif de la plateforme Admission WhatsApp API. "
        "Tu aides l'administrateur à piloter sa base de candidatures en langage naturel.\n"
        "Contraintes : tu ne réponds QU'AUX questions liées aux candidatures, programmes, "
        "critères d'admission et statistiques. Pas d'informations hors périmètre.\n"
        "Pour toute action modifiant des données, un mécanisme de confirmation gère le dry_run : "
        "décris simplement l'action souhaitée via l'outil approprié.\n"
        "Réponds en français. Pour les listes de candidats, sois concis (le tableau est affiché à part).\n"
        f"Contexte — Université : {university.name} | Programmes : {programs} | Date : {date.today().isoformat()}"
    )


# ----------------------------------------------------------------------
# Orchestrateur principal
# ----------------------------------------------------------------------
class AdminChatOrchestrator:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    def _get_llm(self) -> LLMClient:
        if self._llm is None:
            from app.services.admin_chat_gemini import GeminiLLMClient

            self._llm = GeminiLLMClient()
        return self._llm

    def process(
        self,
        db: Session,
        university: University,
        message: str,
        *,
        session_id: str | None = None,
        confirm_action: bool = False,
    ) -> ChatResult:
        session = load_session(session_id)
        if session is None or session.get("university_id") != str(university.id):
            # Nouvelle session (ou session d'une autre université → on n'y touche pas)
            session = {
                "session_id": str(uuid.uuid4()),
                "university_id": str(university.id),
                "history": [],
                "pending_action": None,
            }
        sid = session["session_id"]

        # 1) Confirmation d'une action en attente
        if confirm_action and session.get("pending_action"):
            pa = session["pending_action"]
            result = dispatch_write(db, university.id, pa["tool"], pa["args"], dry_run=False)
            session["pending_action"] = None
            msg = render_action_result(pa["tool"], result)
            session["history"].append({"role": "model", "content": msg})
            save_session(session)
            return ChatResult(session_id=sid, message=msg, tool_used=pa["tool"])

        # 2) Tour normal — boucle d'orchestration LLM
        session["history"].append({"role": "user", "content": message})
        program_names = _program_names(db, university.id)
        system_prompt = build_system_prompt(university, program_names)
        llm = self._get_llm()
        data_table = None
        tool_used = None

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                res = llm.run(system_prompt, session["history"], TOOL_SPECS)
            except Exception:
                logger.exception("Erreur de l'orchestrateur LLM")
                save_session(session)
                return ChatResult(
                    session_id=sid,
                    message="Désolé, l'assistant est momentanément indisponible. Réessayez.",
                )

            if res.tool_calls:
                call = res.tool_calls[0]
                name = call.get("name")
                args = call.get("args") or {}
                if name not in TOOL_SPECS:
                    session["history"].append(
                        {"role": "tool", "name": name, "content": {"error": "Outil inconnu."}}
                    )
                    continue
                tool_used = name

                if name in WRITE_TOOLS:
                    # Aperçu (dry_run) + demande de confirmation, on s'arrête là.
                    preview = dispatch_write(db, university.id, name, args, dry_run=True)
                    summary = render_preview(name, args, preview)
                    session["pending_action"] = {"tool": name, "args": args}
                    session["history"].append({"role": "model", "content": summary})
                    save_session(session)
                    return ChatResult(
                        session_id=sid,
                        message=summary,
                        pending_action={
                            "type": name,
                            "summary": summary,
                            "count": preview.get("count"),
                        },
                        tool_used=name,
                    )

                # Outil de lecture → exécuté, résultat renvoyé au LLM
                session["history"].append(
                    {"role": "model", "tool_calls": [{"name": name, "args": args}]}
                )
                result = dispatch_read(db, university.id, name, args)
                if isinstance(result, dict) and "applications" in result:
                    data_table = result["applications"]
                session["history"].append({"role": "tool", "name": name, "content": result})
                continue

            # Réponse finale en texte
            final = res.text or "Je n'ai pas de réponse."
            session["history"].append({"role": "model", "content": final})
            save_session(session)
            return ChatResult(
                session_id=sid, message=final, data_table=data_table, tool_used=tool_used
            )

        save_session(session)
        return ChatResult(
            session_id=sid,
            message="Je n'ai pas pu aboutir. Pouvez-vous reformuler ?",
            data_table=data_table,
            tool_used=tool_used,
        )


def _program_names(db: Session, university_id: uuid.UUID) -> list[str]:
    from sqlalchemy import select

    from app.models.program import Program

    rows = db.execute(
        select(Program.name)
        .where(Program.university_id == university_id)
        .where(Program.is_active.is_(True))
        .limit(100)
    ).scalars().all()
    return list(rows)
