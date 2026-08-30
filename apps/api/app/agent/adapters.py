from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.config import Settings

from .grounding import GenerationPrompt
from .schemas import AnswerDraft

_JSON_FENCE = re.compile(
    r"^\s*```(?:json)?\s*(?P<payload>.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL
)
_PLACEHOLDER_VALUES = {
    "change-me",
    "changeme",
    "replace-me",
    "replace-with-an-api-key",
    "replace-with-an-openai-compatible-model",
    "your-api-key",
    "your-model",
}


def _is_configured_value(value: str) -> bool:
    normalized = value.strip().casefold()
    return (
        bool(normalized)
        and normalized not in _PLACEHOLDER_VALUES
        and not normalized.startswith(("replace-with-", "your-"))
    )


def has_openai_chat_config(settings: Settings) -> bool:
    """Return true only for an explicit, non-placeholder LLM configuration."""

    return all(
        (
            _is_configured_value(settings.llm_base_url),
            _is_configured_value(settings.llm_api_key),
            _is_configured_value(settings.llm_model),
        )
    )


def _message_content(payload: Mapping[str, Any]) -> str | Mapping[str, Any]:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("chat completion response has no message content") from exc

    if isinstance(content, Mapping):
        return content
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, Mapping):
                continue
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        if text_parts:
            return "".join(text_parts)
    raise ValueError("chat completion message content is invalid")


def parse_openai_answer_draft(
    payload: Mapping[str, Any], *, allowed_citation_labels: Sequence[int]
) -> AnswerDraft:
    """Parse and constrain one OpenAI-compatible chat completion response."""

    content = _message_content(payload)
    if isinstance(content, Mapping):
        raw_draft: Any = dict(content)
    else:
        match = _JSON_FENCE.match(content)
        serialized = match.group("payload") if match else content.strip()
        try:
            raw_draft = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise ValueError("chat completion did not return valid JSON") from exc

    draft = AnswerDraft.model_validate(raw_draft)
    allowed = set(allowed_citation_labels)
    used = {label for claim in draft.claims for label in claim.citation_labels}
    if not used.issubset(allowed):
        raise ValueError("chat completion referenced a citation outside the prompt")
    return draft


class OpenAICompatibleChatAdapter:
    """Minimal async adapter for an OpenAI-compatible chat-completions API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not all(_is_configured_value(value) for value in (base_url, api_key, model)):
            raise ValueError("OpenAI-compatible chat configuration is incomplete")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        parsed_url = httpx.URL(base_url.strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        self.base_url = str(parsed_url).rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def generate(self, prompt: GenerationPrompt) -> AnswerDraft:
        system_instruction = prompt.system_instruction
        if prompt.grounding_feedback:
            feedback = json.dumps(
                list(prompt.grounding_feedback),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            system_instruction = (
                f"{system_instruction}\nThe previous draft failed grounding checks: "
                f"{feedback}. Correct those errors in the next JSON draft."
            )
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt.user_payload},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=request_payload,
            )
            response.raise_for_status()
            response_payload = response.json()
        if not isinstance(response_payload, Mapping):
            raise TypeError("chat completion response root must be an object")
        return parse_openai_answer_draft(
            response_payload,
            allowed_citation_labels=prompt.allowed_citation_labels,
        )


def build_openai_chat_adapter(
    settings: Settings,
) -> OpenAICompatibleChatAdapter | None:
    if not has_openai_chat_config(settings):
        return None
    return OpenAICompatibleChatAdapter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
