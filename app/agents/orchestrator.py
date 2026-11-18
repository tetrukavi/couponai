"""Orchestrator: normalize -> local (instant, free) -> online sources -> recommendation.

Paid online results are saved per calendar month (Israel time). Modes:
  ask      (default) free only: local coupons + this month's saved online results. If any
           source has no saved result, emit `online: needs_approval` with a cost estimate and stop.
  run      the user approved paying: search the sources that have no saved result this month.
  refresh  the user approved paying to search every source again.

`search()` is an async generator of (event, payload) pairs, consumed by the SSE endpoint.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

from app import costs
from app.agents import local_voucher, synthesizer, web_research
from app.agents.normalizer import key, normalize
from app.cache import get_cache
from app.config import get_settings
from app.connectors import build_connectors
from app.models import NormalizedStore, Recommendation, SourceResult, SourceSpec

log = logging.getLogger(__name__)

Worker = Callable[[NormalizedStore], Awaitable[SourceResult]]
Mode = Literal["ask", "run", "refresh"]
FOREVER = 10**9  # month-keyed entries never expire by TTL; the month in the key does it


def default_workers() -> list[tuple[SourceSpec, Worker]]:
    held = local_voucher.held_networks()
    workers: list[tuple[SourceSpec, Worker]] = [
        (
            SourceSpec(
                id=web_research.SOURCE_ID, label=web_research.LABEL, kind="store", needs_llm=True
            ),
            lambda st: web_research.run(st, held),
        )
    ]
    for c in build_connectors():
        workers.append(
            (
                SourceSpec(id=f"club:{c.id}", label=c.name, kind="club", needs_llm=c.needs_llm),
                c.search_benefits,
            )
        )
    return workers


def _saved_key(store: NormalizedStore, source_id: str) -> str:
    return f"src:{costs.month_key()}:{key(store.canonical)}:{source_id}"


def get_saved(store: NormalizedStore, source_id: str) -> SourceResult | None:
    hit = get_cache().get(_saved_key(store, source_id), ttl_s=FOREVER)
    return SourceResult.model_validate({**hit, "cached": True}) if hit else None


def estimate_usd(n_sources: int) -> float:
    s = get_settings()
    per_source = s.worker_max_searches * costs.WEB_SEARCH_USD + s.est_tokens_usd_per_source
    return round(n_sources * per_source + s.est_synth_usd, 2)


async def _run_worker(spec: SourceSpec, fn: Worker, store: NormalizedStore) -> SourceResult:
    s = get_settings()
    if spec.needs_llm and not s.llm_enabled:
        return SourceResult(
            source=spec.id,
            label=spec.label,
            status="disabled",
            note="Online search is off: set ANTHROPIC_API_KEY in .env",
        )
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(fn(store), timeout=s.worker_timeout_s)
    except TimeoutError:
        res = SourceResult(
            source=spec.id,
            label=spec.label,
            status="timeout",
            note=f"No answer within {s.worker_timeout_s:.0f}s (not saved; you can retry)",
        )
    except Exception as e:  # noqa: BLE001 - one source must never break the stream
        log.exception("worker %s failed", spec.id)
        res = SourceResult(
            source=spec.id, label=spec.label, status="error", note=f"{type(e).__name__}: {e}"[:200]
        )
    res.source, res.label = spec.id, spec.label
    res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if res.status in ("ok", "empty") and spec.needs_llm:  # a paid answer: keep it all month
        res.saved_at = costs.now().isoformat(timespec="minutes")
        get_cache().set(_saved_key(store, spec.id), res.model_dump(mode="json"))
    return res


async def _recheck_local(
    store: NormalizedStore, results: list[SourceResult]
) -> SourceResult | None:
    """Re-match my coupons using what the store's website said about gift-card networks.

    Returns the updated local result, or None if nothing changed.
    """
    answers = [n for r in results[1:] for n in r.extra.get("accepted_networks", [])]
    if not answers:
        return None
    new = await asyncio.to_thread(local_voucher.search, store, None, answers)
    sig = lambda r: ([(v.voucher_id, v.accepted) for v in r.vouchers], r.note)  # noqa: E731
    if sig(new) == sig(results[0]):
        return None
    new.elapsed_ms = results[0].elapsed_ms
    return new


def _fingerprint(results: list[SourceResult]) -> str:
    """Changes whenever my coupons or the online results change."""
    local = results[0]
    data = [
        [(v.voucher_id, v.remaining_balance, v.status) for v in local.vouchers],
        sorted((r.source, r.saved_at or "") for r in results[1:]),
    ]
    return hashlib.sha1(json.dumps(data, default=str).encode()).hexdigest()[:16]


async def search(
    query: str, workers: list[tuple[SourceSpec, Worker]] | None = None, mode: Mode = "ask"
) -> AsyncIterator[tuple[str, Any]]:
    t0 = time.perf_counter()
    s = get_settings()
    paid = mode in ("run", "refresh")
    store = await normalize(query, allow_llm=paid)
    yield "normalized", store.model_dump()

    workers = default_workers() if workers is None else workers
    yield "plan", {"sources": [spec.model_dump() for spec, _ in workers]}

    local = await asyncio.to_thread(local_voucher.search, store)
    yield "local", local.model_dump(mode="json")
    results: list[SourceResult] = [local]

    # this month's saved online results (free)
    to_run: list[tuple[SourceSpec, Worker]] = []
    for spec, fn in workers:
        saved = None if mode == "refresh" or not spec.needs_llm else get_saved(store, spec.id)
        if saved:
            results.append(saved)
            yield "source", saved.model_dump(mode="json")
        else:
            to_run.append((spec, fn))
    if updated := await _recheck_local(store, results):  # saved store answer: free
        results[0] = updated
        yield "local", updated.model_dump(mode="json")

    needs_pay = [spec for spec, _ in to_run if spec.needs_llm]
    if not paid:
        # free sources run now; paid ones wait for the user's OK
        free = [(spec, fn) for spec, fn in to_run if not spec.needs_llm]
        for spec, fn in free:
            res = await _run_worker(spec, fn, store)
            results.append(res)
            yield "source", res.model_dump(mode="json")
        to_run = []

    yield (
        "recommendation",
        synthesizer.rules_recommendation(store, results, preliminary=bool(to_run)).model_dump(),
    )

    if not paid and needs_pay:
        saved_at = [r.saved_at for r in results[1:] if r.saved_at]
        yield (
            "online",
            {
                "status": "needs_approval" if s.llm_enabled else "unavailable",
                "missing": [spec.model_dump() for spec in needs_pay],
                "estimate_usd": estimate_usd(len(needs_pay)),
                "saved_at": min(saved_at) if saved_at else None,
                "month": costs.month_key(),
                "month_total_usd": round(costs.month_total(), 4),
            },
        )
        yield "done", {"elapsed_ms": int((time.perf_counter() - t0) * 1000)}
        return

    tasks = [asyncio.create_task(_run_worker(spec, fn, store)) for spec, fn in to_run]
    spent = 0.0
    try:
        for fut in asyncio.as_completed(tasks):
            res = await fut
            spent += res.cost_usd
            results.append(res)
            yield "source", res.model_dump(mode="json")
        if updated := await _recheck_local(store, results):
            results[0] = updated
            yield "local", updated.model_dump(mode="json")

        rec = synthesizer.rules_recommendation(store, results, False)
        rec_key = f"rec:{costs.month_key()}:{key(store.canonical)}:{_fingerprint(results)}"
        has_online = any(r.offers for r in results[1:])
        if (hit := get_cache().get(rec_key, ttl_s=FOREVER)) is not None:
            rec = Recommendation.model_validate(hit)  # same inputs as a paid comparison: free
        elif paid and s.llm_enabled and (has_online or len(local.vouchers) > 1):
            cost: list[float] = []
            try:
                rec = await asyncio.wait_for(
                    synthesizer.llm_recommendation(store, results, cost_out=cost),
                    timeout=s.synth_timeout_s,
                )
                get_cache().set(rec_key, rec.model_dump())
            except Exception as e:  # noqa: BLE001 - keep the rules-based answer
                log.warning("synthesizer failed, keeping rules result: %s", e)
            spent += sum(cost)
        yield "recommendation", rec.model_dump()
    finally:
        for t in tasks:
            t.cancel()  # client went away: stop paying for workers nobody reads

    saved_at = [r.saved_at for r in results[1:] if r.saved_at and r.cached]
    yield (
        "online",
        {
            "status": "searched" if paid else "saved",
            "cost_usd": round(spent, 4),
            "estimate_usd": estimate_usd(sum(spec.needs_llm for spec, _ in workers)),
            "saved_at": min(saved_at) if saved_at else None,
            "month": costs.month_key(),
            "month_total_usd": round(costs.month_total(), 4),
        },
    )
    yield "done", {"elapsed_ms": int((time.perf_counter() - t0) * 1000)}
