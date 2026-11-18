import asyncio

import pytest

from app import costs
from app.agents import normalizer, orchestrator, synthesizer
from app.config import get_settings
from app.models import Offer, Recommendation, SourceResult, SourceSpec


def spec(i, needs_llm=True):
    return SourceSpec(id=i, label=i, kind="club", needs_llm=needs_llm)


async def collect(q, workers, mode="ask"):
    return [(e, d) async for e, d in orchestrator.search(q, workers, mode=mode)]


def of(events, name):
    return [d for e, d in events if e == name]


@pytest.fixture
def llm_on(monkeypatch):
    """Pretend an API key exists; fake the paid synthesizer so nothing hits the network."""
    monkeypatch.setenv("COUPONAI_DISABLE_LLM", "0")
    monkeypatch.setenv("COUPONAI_FORCE_LLM", "1")
    get_settings.cache_clear()
    calls = {"synth": 0}

    async def fake_synth(store, results, cost_out=None):
        calls["synth"] += 1
        if cost_out is not None:
            cost_out.append(0.02)
        return Recommendation(best_title="AI pick", reason="r", generated_by="llm")

    monkeypatch.setattr(synthesizer, "llm_recommendation", fake_synth)
    return calls


class Counter:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    async def __call__(self, store):
        self.calls += 1
        return await self.fn(store)


async def fast(store):
    return SourceResult(
        source="x", label="x", cost_usd=0.05,
        offers=[Offer(source="x", kind="club_discount", title="10% off", discount_pct=10)],
    )  # fmt: skip


async def slow(store):
    await asyncio.sleep(5)


async def boom(store):
    raise RuntimeError("site down")


async def test_ask_mode_never_pays(llm_on):
    w = Counter(fast)
    events = await collect("פוט לוקר", [(spec("club:a"), w)])
    names = [e for e, _ in events]
    assert names[:3] == ["normalized", "plan", "local"] and names[-1] == "done"
    assert w.calls == 0 and llm_on["synth"] == 0 and not of(events, "source")
    [online] = of(events, "online")
    assert online["status"] == "needs_approval" and online["estimate_usd"] > 0
    assert [m["id"] for m in online["missing"]] == ["club:a"]
    # the free answer is final, not "refining"
    assert of(events, "recommendation")[-1]["preliminary"] is False


async def test_ask_mode_without_key_is_unavailable():
    events = await collect("Nike", [(spec("store_site"), fast)])
    assert of(events, "online")[0]["status"] == "unavailable"


async def test_ask_mode_never_calls_llm_normalizer(monkeypatch, llm_on):
    async def no(*a, **k):
        raise AssertionError("paid call in ask mode")

    monkeypatch.setattr(normalizer, "_llm_normalize", no)
    events = await collect("Some Unknown Shop", [(spec("club:a"), fast)])
    assert of(events, "normalized")[0]["method"] == "raw"


async def test_run_mode_streams_and_survives_failures(llm_on, monkeypatch):
    monkeypatch.setenv("COUPONAI_WORKER_TIMEOUT_S", "0.3")
    get_settings.cache_clear()
    events = await collect(
        "פוט לוקר",
        [(spec("club:fast"), fast), (spec("club:slow"), slow), (spec("club:boom"), boom)],
        mode="run",
    )
    names = [e for e, _ in events]
    assert names.index("local") < names.index("source")
    src = {d["source"]: d for d in of(events, "source")}
    assert src["club:fast"]["status"] == "ok" and src["club:fast"]["saved_at"]
    assert src["club:slow"]["status"] == "timeout" and src["club:boom"]["status"] == "error"
    recs = of(events, "recommendation")
    assert recs[0]["preliminary"] and recs[-1]["generated_by"] == "llm"
    online = of(events, "online")[-1]
    assert online["status"] == "searched" and online["cost_usd"] == pytest.approx(0.07)


async def test_saved_for_the_month_then_free(llm_on, monkeypatch):
    w = Counter(fast)
    workers = [(spec("club:a"), w)]
    await collect("Nike", workers, mode="run")
    assert w.calls == 1 and llm_on["synth"] == 1

    events = await collect("Nike", workers)  # same month, ask mode
    assert w.calls == 1 and llm_on["synth"] == 1  # nothing paid again
    [src] = of(events, "source")
    assert src["cached"] and src["saved_at"]
    assert of(events, "online")[-1]["status"] == "saved"
    assert of(events, "recommendation")[-1]["generated_by"] == "llm"  # saved AI comparison

    monkeypatch.setattr(costs, "month_key", lambda dt=None: "2099-01")  # next month
    events = await collect("Nike", workers)
    assert of(events, "online")[-1]["status"] == "needs_approval"


async def test_failed_sources_are_not_saved(llm_on):
    await collect("Nike", [(spec("club:boom"), boom)], mode="run")
    events = await collect("Nike", [(spec("club:boom"), boom)])
    assert of(events, "online")[-1]["status"] == "needs_approval"


async def test_refresh_pays_again(llm_on):
    w = Counter(fast)
    await collect("Nike", [(spec("club:a"), w)], mode="run")
    await collect("Nike", [(spec("club:a"), w)], mode="refresh")
    assert w.calls == 2


async def test_free_sources_run_in_ask_mode():
    async def mock(store):
        return SourceResult(source="m", label="m", status="mock")

    events = await collect("Nike", [(spec("club:portal", needs_llm=False), mock)])
    assert of(events, "source")[0]["status"] == "mock"
    assert of(events, "online")[-1]["status"] == "saved"  # nothing needed paying


async def test_default_workers_store_and_cards_while_clubs_disabled():
    ids = [s.id for s, _ in orchestrator.default_workers()]
    assert ids == ["store_site", "cards"]


def test_clubs_can_be_enabled():
    from app.connectors import build_connectors

    built = build_connectors(
        [{"id": "a", "name": "A", "enabled": False}, {"id": "b", "name": "B", "enabled": True}]
    )
    assert [c.id for c in built] == ["b"]


def test_estimate_scales_with_sources():
    assert orchestrator.estimate_usd(4) > orchestrator.estimate_usd(1) > 0


def test_cost_of_usage():
    class STU:
        web_search_requests = 3

    class U:
        input_tokens, output_tokens = 1_000_000, 100_000
        cache_read_input_tokens = cache_creation_input_tokens = 0
        server_tool_use = STU()

    # haiku: $0.10 in + 0.1M*$0.50 out + 3 searches * $0.01
    assert costs.cost_of(U(), "claude-haiku-5-5") == pytest.approx(0.10 + 0.05 + 0.03)


async def test_store_answer_rematches_my_coupons_and_is_reused_free(llm_on):
    async def store_site(store):
        return SourceResult(
            source="store_site", label="Store website", cost_usd=0.04,
            extra={"accepted_networks": [{"name": "תו הזהב", "accepted": "yes"}]},
        )  # fmt: skip

    workers = [(spec("store_site"), Counter(store_site))]
    events = await collect("Zara", workers, mode="run")
    locals_ = of(events, "local")
    assert "V008" not in [v["voucher_id"] for v in locals_[0]["vouchers"]]
    assert [v["voucher_id"] for v in locals_[-1]["vouchers"]] == ["V008"]  # re-emitted
    assert locals_[-1]["vouchers"][0]["accepted"] == "yes"

    events = await collect("Zara", workers)  # next time: saved answer, free
    assert of(events, "online")[-1]["status"] == "saved"
    assert [v["voucher_id"] for v in of(events, "local")[-1]["vouchers"]] == ["V008"]
