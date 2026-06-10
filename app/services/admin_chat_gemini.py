"""Client LLM Gemini pour l'orchestrateur admin (mode JSON).

On n'utilise pas le function-calling « natif » (proto fragile, non testable sans
clé) mais le mode JSON de Gemini — déjà éprouvé dans ai_classifier : le modèle
répond par un JSON décrivant SOIT un appel d'outil, SOIT une réponse finale.
L'orchestrateur exécute l'outil (avec university_id) et rappelle le modèle.
"""
import json
import logging
import re

from app.config import settings
from app.services.admin_chat import LLMClient, LLMResult

logger = logging.getLogger(__name__)

_RESPONSE_INSTRUCTION = (
    "Réponds UNIQUEMENT par un objet JSON, sans texte autour, selon l'un de ces deux formats :\n"
    '- Pour appeler un outil : {"action": "tool", "tool": "<nom>", "args": { ... }}\n'
    '- Pour répondre à l\'admin : {"action": "final", "message": "<texte en français>"}\n'
    "N'invente jamais de données : utilise les outils pour interroger ou modifier la base. "
    "Quand tu as assez d'informations issues des résultats d'outils, renvoie action=final."
)


def _tool_catalog(tool_specs: dict) -> str:
    lines = ["Outils disponibles :"]
    for name, spec in tool_specs.items():
        params = "; ".join(f"{p} : {d}" for p, d in spec["params"].items())
        lines.append(f"- {name} : {spec['description']} | paramètres : {params}")
    return "\n".join(lines)


def _serialize_history(history: list) -> str:
    parts = []
    for entry in history:
        role = entry.get("role")
        if role == "user":
            parts.append(f"ADMIN : {entry.get('content', '')}")
        elif role == "model":
            if entry.get("tool_calls"):
                for tc in entry["tool_calls"]:
                    parts.append(f"ASSISTANT a appelé {tc.get('name')}({json.dumps(tc.get('args', {}), ensure_ascii=False)})")
            elif entry.get("content"):
                parts.append(f"ASSISTANT : {entry['content']}")
        elif role == "tool":
            raw = json.dumps(entry.get("content", {}), ensure_ascii=False)
            parts.append(f"RÉSULTAT {entry.get('name')} : {raw[:2000]}")
    return "\n".join(parts)


def _parse(raw: str) -> LLMResult:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Pas de JSON exploitable → on traite le texte brut comme réponse finale.
        return LLMResult(text=(raw or "").strip() or "Je n'ai pas de réponse.")

    if data.get("action") == "tool" and data.get("tool"):
        return LLMResult(tool_calls=[{"name": data["tool"], "args": data.get("args") or {}}])
    return LLMResult(text=str(data.get("message", "")) or "Je n'ai pas de réponse.")


class GeminiLLMClient(LLMClient):
    """Implémentation Gemini (mode JSON)."""

    def __init__(self) -> None:
        import google.generativeai as genai

        genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
        self._genai = genai
        self._model_name = settings.GOOGLE_AI_MODEL

    def run(self, system_prompt: str, history: list, tool_specs: dict) -> LLMResult:
        prompt = (
            f"{_tool_catalog(tool_specs)}\n\n"
            f"{_RESPONSE_INSTRUCTION}\n\n"
            f"--- CONVERSATION ---\n{_serialize_history(history)}\n--- FIN ---\n\n"
            "Quelle est ta prochaine action (JSON) ?"
        )
        model = self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system_prompt,
            generation_config=self._genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=2048,
            ),
        )
        response = model.generate_content(prompt)
        return _parse(response.text)
