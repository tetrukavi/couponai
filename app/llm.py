"""Thin wrapper around the Anthropic SDK: one shared async client + research/JSON helpers.

Every call's cost is computed from the response `usage` and added to the monthly ledger.
"""

import json
import logging
import re
from typing import Any

import anthropic

from app import costs
from app.config import get_settings
from app.guardrails import check_tools, system_prompt

log = logging.getLogger(__name__)
_client: anthropic.AsyncAnthropic | None = None


class LLMUnavailable(RuntimeError):
    pass


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    s = get_settings()
    if not s.llm_enabled:
        raise LLMUnavailable("LLM disabled (no ANTHROPIC_API_KEY)")
    if _client is None:
        kwargs: dict[str, Any] = {"max_retries": 1}
        if s.anthropic_api_key:
            kwargs["api_key"] = s.anthropic_api_key
        _client = anthropic.AsyncAnthropic(**kwargs)
    return _client


def web_tools() -> list[dict[str, Any]]:
    s = get_settings()
    return check_tools(
        [
            {
                "type": s.web_search_tool,
                "name": "web_search",
                "max_uses": s.worker_max_searches,
                # no user_location: the API rejects country "IL"; prompts say "in Israel"
            },
            {"type": s.web_fetch_tool, "name": "web_fetch", "max_uses": s.worker_max_fetches},
        ]
    )


def _text_of(content: list[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text")


def _charge(resp: Any, model: str) -> float:
    usd = costs.cost_of(getattr(resp, "usage", None), getattr(resp, "model", None) or model)
    costs.record(usd)
    return usd


async def research(
    task: str, prompt: str, *, max_tokens: int = 4000
) -> tuple[str, list[str], float]:
    """Run a worker with web search/fetch. Returns (final text, cited URLs, cost in USD)."""
    s = get_settings()
    client = get_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    urls: list[str] = []
    usd = 0.0
    for _ in range(3):  # resume server-tool pauses a couple of times at most
        resp = await client.messages.create(
            model=s.worker_model,
            max_tokens=max_tokens,
            system=system_prompt(task),
            tools=web_tools(),
            output_config={"effort": s.worker_effort},
            messages=messages,
        )
        usd += _charge(resp, s.worker_model)
        for b in resp.content:
            if getattr(b, "type", None) == "web_search_tool_result" and isinstance(b.content, list):
                urls += [r.url for r in b.content if getattr(r, "url", None)]
        if resp.stop_reason == "refusal":
            raise RuntimeError("model declined this request")
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    return _text_of(resp.content), urls, usd


async def structured(
    task: str,
    prompt: str | list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    model: str,
    effort: str,
    max_tokens: int = 8000,
    fallbacks: bool = False,
    cost_out: list[float] | None = None,
) -> dict[str, Any]:
    """No-tool call with a JSON-schema constrained answer."""
    client = get_client()
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt(task),
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if fallbacks:
        resp = await client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    else:
        resp = await client.messages.create(**kwargs)
    usd = _charge(resp, model)
    if cost_out is not None:
        cost_out.append(usd)
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined this request")
    return json.loads(_text_of(resp.content))


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a worker's free-text answer."""
    m = _JSON_BLOCK.search(text)
    candidates = [m.group(1)] if m else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object in model output")
