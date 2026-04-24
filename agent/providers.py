"""Wire-API adapters for ``tb agent``.

Each provider knows how to turn a ``(agent dict, messages list)`` into a
single string of response text. Adding a new wire protocol means writing
one adapter function and registering it in ``PROVIDERS``.

Agents are dicts (not classes) because they come straight out of
``agent_store.py``'s JSON; we keep the same shape end-to-end.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from lib.errors import AuthError, StateError, UsageError


@dataclass
class ProviderResult:
    """Structured response from a provider adapter.

    ``content`` is the text the caller consumes.  ``usage`` carries
    token counts when the provider includes them (keys vary by wire API
    but typically ``prompt_tokens``, ``completion_tokens``, ``total_tokens``).
    ``raw_model`` is the model string the provider echoed back, if any.
    """

    content: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw_model: str = ""


ProviderFn = Callable[
    [dict[str, Any], list[dict[str, str]], float], ProviderResult
]


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any],
               *, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        if e.code in (401, 403):
            raise AuthError(detail or "provider rejected API key")
        raise StateError(f"provider HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise StateError(f"provider request failed: {e.reason}")


def _text_from_openai_content(content: Any) -> str:
    """OpenAI chat completions: content is either a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content)


def _text_from_anthropic_content(content: Any) -> str:
    """Anthropic messages: content is a list of content blocks with type 'text'."""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return _text_from_openai_content(content)


def _anthropic_messages_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _is_minimax_openai(agent: dict[str, Any]) -> bool:
    provider = str(agent.get("provider") or "").strip().lower()
    base_url = str(agent.get("base_url") or "").lower()
    model = str(agent.get("model") or "")
    return (
        provider == "minimax"
        or "minimax" in base_url
        or model.startswith("MiniMax-")
    )


def openai_chat(agent: dict[str, Any], messages: list[dict[str, str]],
                timeout: float) -> ProviderResult:
    base_url = agent["base_url"].rstrip("/")
    payload = {
        "model": agent["model"],
        "messages": messages,
        "temperature": 0.1,
    }
    if _is_minimax_openai(agent):
        # MiniMax reasoning models emit <think> in content by default.
        # reasoning_split keeps the reasoning in reasoning_details so content
        # stays suitable for strict JSON-only agent loops.
        payload["reasoning_split"] = True
    headers = {
        "Authorization": f"Bearer {agent['api_key']}",
        "Content-Type": "application/json",
    }
    data = _post_json(f"{base_url}/chat/completions", headers, payload, timeout=timeout)
    try:
        text = _text_from_openai_content(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise StateError(f"unexpected provider response shape: {e}")
    usage = data.get("usage") or {}
    raw_model = str(data.get("model") or "")
    return ProviderResult(content=text, usage=dict(usage), raw_model=raw_model)


def anthropic_messages(agent: dict[str, Any], messages: list[dict[str, str]],
                       timeout: float) -> ProviderResult:
    base_url = agent["base_url"].rstrip("/")
    system_parts: list[str] = []
    convo: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            raise UsageError(f"unsupported Anthropic message role: {role}")
        convo.append({"role": role, "content": content})
    payload: dict[str, Any] = {
        "model": agent["model"],
        "max_tokens": 1200,
        "messages": convo,
        "temperature": 0.1,
    }
    if system_parts:
        payload["system"] = "\n\n".join(p for p in system_parts if p.strip())
    headers = {
        "x-api-key": agent["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    data = _post_json(_anthropic_messages_url(base_url), headers, payload, timeout=timeout)
    try:
        text = _text_from_anthropic_content(data["content"])
    except KeyError as e:
        raise StateError(f"unexpected provider response shape: {e}")
    usage = data.get("usage") or {}
    raw_model = str(data.get("model") or "")
    return ProviderResult(content=text, usage=dict(usage), raw_model=raw_model)


PROVIDERS: dict[str, ProviderFn] = {
    "openai-chat": openai_chat,
    "anthropic-messages": anthropic_messages,
}


def complete(agent: dict[str, Any], messages: list[dict[str, str]],
             *, timeout: float) -> ProviderResult:
    """Dispatch to the provider matching ``agent['wire_api']``."""
    wire_api = str(agent.get("wire_api") or "openai-chat")
    provider = PROVIDERS.get(wire_api)
    if provider is None:
        raise UsageError(f"unsupported wire API: {wire_api}")
    return provider(agent, messages, timeout)
