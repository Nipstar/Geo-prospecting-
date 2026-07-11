"""Single LLM client wrapper around OpenRouter (OpenAI-compatible).

Every module calls ``llm.complete(task, ...)`` where ``task`` keys into
``config.MODELS`` so models swap in one place. The only exception is the
ChatGPT visibility probe, which hits OpenAI directly (see visibility/probes.py).
"""
from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from . import config

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        _client = OpenAI(
            api_key=config.OPENROUTER_API_KEY,
            base_url=config.OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://antekautomation.com",
                "X-Title": "geo-outreach",
            },
        )
    return _client


def model_for(task: str) -> str:
    if task not in config.MODELS:
        raise KeyError(f"Unknown LLM task '{task}'. Add it to config.MODELS.")
    return config.MODELS[task]


def complete(
    task: str,
    system: str | None = None,
    user: str | None = None,
    messages: list[dict[str, str]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    json_mode: bool = False,
    retries: int = 3,
) -> str:
    """Run a chat completion for the given task. Returns the text content.

    Pass either ``messages`` or a ``system``/``user`` pair. With ``json_mode``
    the model is asked for a JSON object (use parse_json on the result).
    """
    client = _get_client()
    model = model_for(task)

    if messages is None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user or ""})

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - surface after retries
            last_err = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed for task '{task}': {last_err}")


def parse_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response.

    Handles fenced code blocks and leading/trailing prose the model sometimes
    adds even in JSON mode.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    # Grab the outermost object/array if there's surrounding prose.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(text)  # raises with a clear message if truly malformed
