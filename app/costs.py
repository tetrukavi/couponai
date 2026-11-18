"""API cost accounting: price table, per-call cost from `usage`, monthly ledger."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.cache import get_cache

TZ = ZoneInfo("Asia/Jerusalem")

# USD per 1M tokens (input, output). Anthropic list prices as of 2026-11; update if they change.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}
WEB_SEARCH_USD = 0.01  # per search ($10 / 1,000); web fetch has no extra charge
CACHE_READ_FACTOR, CACHE_WRITE_FACTOR = 0.1, 1.25


def now() -> datetime:
    return datetime.now(TZ)


def month_key(dt: datetime | None = None) -> str:
    return (dt or now()).strftime("%Y-%m")


def cost_of(usage: Any, model: str) -> float:
    """USD for one API response, from its `usage` block."""
    if usage is None:
        return 0.0
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])  # unknown model: assume pricier
    g = lambda name: getattr(usage, name, 0) or 0  # noqa: E731
    tokens_in = (
        g("input_tokens")
        + CACHE_READ_FACTOR * g("cache_read_input_tokens")
        + CACHE_WRITE_FACTOR * g("cache_creation_input_tokens")
    )
    usd = tokens_in * pin / 1e6 + g("output_tokens") * pout / 1e6
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        usd += (getattr(stu, "web_search_requests", 0) or 0) * WEB_SEARCH_USD
    return usd


def record(usd: float) -> None:
    """Add to this month's running total (kept in the local cache db)."""
    if usd <= 0:
        return
    key = f"spend:{month_key()}"
    c = get_cache()
    c.set(key, round((c.get(key, ttl_s=10**9) or 0.0) + usd, 6))


def month_total() -> float:
    return get_cache().get(f"spend:{month_key()}", ttl_s=10**9) or 0.0
