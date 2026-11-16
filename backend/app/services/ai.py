import json
from typing import Any

import httpx
from fastapi import HTTPException, status

from backend.app.core.config import settings
from backend.app.services.model_router import ModelRoute


LANGUAGE_NAMES = {"el": "Greek", "en": "English"}


def language_directive(language: str | None) -> str:
    """Return the system-prompt instruction that pins the answer language."""
    name = LANGUAGE_NAMES.get((language or "el").strip().lower(), "Greek")
    return f"Answer in {name}."


class DeepSeekClient:
    def __init__(self, route: ModelRoute | None = None) -> None:
        # When a route is supplied (unified copilot, #13) the client targets that
        # backend — typically the in-region EU model for PHI-bearing requests.
        # With no route it defaults to the external DeepSeek API as before.
        if route is not None:
            self.provider = route.provider
            self.api_key = route.api_key
            self.base_url = route.base_url.rstrip("/")
            self.model = route.model
        else:
            self.provider = "deepseek"
            self.api_key = settings.deepseek_api_key
            self.base_url = settings.deepseek_base_url.rstrip("/")
            self.model = settings.deepseek_model

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No API key is configured for model provider '{self.provider}'.",
            )

    async def ask(self, question: str, context: dict[str, Any], language: str = "el") -> dict[str, Any]:
        self.ensure_configured()
        system_prompt = (
            f"You are an IONM clinical knowledge assistant. {language_directive(language)} "
            "Use only the provided selected context and retrieved knowledge evidence. If the context is insufficient, say so clearly. "
            "Do not invent thresholds, protocols, sources, or patient facts. "
            "When retrieved knowledge is present, mention the most relevant source titles briefly. "
            "For Academy tutor context, explicitly correct learner placement, waveform or case mistakes using lesson context, protocols, retrieved KB evidence and PubMed bibliography when provided. "
            "Always include a concise clinical-caution sentence that final decisions require qualified clinical judgement."
        )
        user_prompt = {
            "question": question,
            "selected_context": context,
        }
        data = await self.chat_completion(system_prompt, user_prompt, temperature=0.2)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        return {"model": self.model, "answer": content, "raw_usage": data.get("usage")}

    async def ask_structured_json(self, task: str, context: dict[str, Any], language: str = "el", focus_directive: str | None = None) -> dict[str, Any]:
        self.ensure_configured()
        system_prompt = (
            f"You are a senior IONM clinical copilot. {language_directive(language)} "
            "Use only the selected context, case timeline, protocols, muscles, clinical plan and retrieved evidence provided. "
            "Do not invent patient facts, thresholds, citations, or protocol details. "
            "Return only one valid JSON object and no markdown. "
            "The object must match this schema exactly: "
            "{\"summary\": string, \"confidence\": \"low|medium|high\", "
            "\"next_checks\": [{\"priority\": \"now|next|watch\", \"action\": string, \"rationale\": string}], "
            "\"surgeon_communication\": [{\"when\": string, \"message\": string}], "
            "\"evidence_used\": [{\"title\": string, \"why_it_matters\": string}], "
            "\"missing_data\": [string], \"watch_items\": [string], \"clinical_caution\": string}. "
            "If context is insufficient, keep confidence low and state the missing data explicitly."
        )
        if focus_directive:
            system_prompt = f"{system_prompt} {focus_directive}"
        user_prompt = {
            "task": task,
            "selected_context": context,
        }
        data = await self.chat_completion(system_prompt, user_prompt, temperature=0.1)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        return {"model": self.model, "provider": self.provider, "data": parse_json_object(content), "raw_usage": data.get("usage")}

    async def ask_case_debrief_json(self, task: str, context: dict[str, Any], language: str = "el") -> dict[str, Any]:
        self.ensure_configured()
        system_prompt = (
            f"You are a senior IONM case debrief assistant. {language_directive(language)} "
            "Use only the selected context, case timeline, protocols, muscles, clinical plan and retrieved evidence provided. "
            "Do not invent patient facts, undocumented events, thresholds, citations, or protocol details. "
            "Return only one valid JSON object and no markdown. "
            "The object must match this schema exactly: "
            "{\"case_summary\": string, \"confidence\": \"low|medium|high\", "
            "\"timeline_replay\": [{\"event\": string, \"interpretation\": string, \"risk_level\": \"low|medium|high|unknown\"}], "
            "\"decision_points\": [{\"moment\": string, \"recommended_action\": string, \"rationale\": string}], "
            "\"documentation_gaps\": [string], "
            "\"academy_learning_points\": [{\"topic\": string, \"why_it_matters\": string, \"practice_prompt\": string}], "
            "\"evidence_used\": [{\"title\": string, \"why_it_matters\": string}], "
            "\"follow_up\": [string], \"clinical_caution\": string}. "
            "If the timeline is sparse or absent, keep confidence low and focus on documentation gaps and safe follow-up."
        )
        user_prompt = {
            "task": task,
            "selected_context": context,
        }
        data = await self.chat_completion(system_prompt, user_prompt, temperature=0.1)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        return {"model": self.model, "data": parse_json_object(content), "raw_usage": data.get("usage")}

    async def chat_completion(self, system_prompt: str, user_prompt: dict[str, Any], temperature: float) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False, default=str)},
                    ],
                    "temperature": temperature,
                },
            )
        if response.status_code >= 400:
            if response.status_code in {401, 403}:
                detail = "DeepSeek authentication failed. Verify DEEPSEEK_API_KEY in the local .env file."
            else:
                detail = f"DeepSeek request failed with provider status {response.status_code}."
            raise HTTPException(status_code=response.status_code, detail=detail)
        return response.json()


def parse_json_object(content: str | None) -> dict[str, Any]:
    if not content or not content.strip():
        raise ValueError("AI response did not include JSON content.")
    candidate = strip_markdown_fence(content.strip())
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI response did not contain a JSON object.")
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI JSON response must be an object.")
    return parsed


def strip_markdown_fence(content: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
