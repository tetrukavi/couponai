"""Gift-card merchant lists (app/catalogs.py). No network: BuyMe is faked with MockTransport."""

import asyncio
import json

import httpx
import pytest

from app import catalogs, costs
from app.agents import local_voucher, orchestrator
from app.agents.normalizer import normalize


def brand(id, title, terms, online=True, site=None, cat="אופנה ושופינג"):
    return {"id": id, "title": title, "searchTerms": json.dumps(terms, ensure_ascii=False),
            "online_redeem": "True" if online else "False", "siteWeb": site,
            "categories_on_brands": [{"categories_id": 1, "name": cat}]}  # fmt: skip


ALL = {
    "supplier": {"name": "BUYME ALL - test", "id": 13438757},
    "brands": [
        brand(1, "Foot Locker", ["פוטלוקר", "פוט לוקר", "קבוצת פוקס"],
              site="https://footlocker.co.il/?utm_source=buyme"),
        brand(2, "FOX", ["פוקס", "קבוצת פוקס"], site="https://www.fox.co.il/"),
        brand(3, "SELAS", ["sels", "מסעדת selas - סלאס"], online=False, cat="מסעדות וקולינריה"),
        brand(4, "MANGO", ["מנגו", "קבוצת פוקס"]),
    ],
    "categories": [], "regions": [],
}  # fmt: skip
FASHION = {"supplier": {"name": "FASHION"}, "brands": [brand(9, "CASTRO", ["קסטרו"])]}


def transport(calls, fail=False):
    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if fail:
            return httpx.Response(503)
        body = ALL if "/13438757/" in str(req.url) else FASHION
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def refresh(calls, fail=False):
    async def go():
        async with httpx.AsyncClient(transport=transport(calls, fail)) as c:
            return await catalogs.refresh_due(c)

    return asyncio.run(go())


def test_parse_buyme():
    d = catalogs.parse_buyme(ALL)
    selas = next(e for e in d["entries"] if e["title"] == "SELAS")
    assert "סלאס" in selas["terms"]  # split out of "מסעדת selas - סלאס"
    assert selas["online"] is False and selas["categories"] == ["מסעדות וקולינריה"]
    fl = next(e for e in d["entries"] if e["title"] == "Foot Locker")
    assert fl["site"] == "https://footlocker.co.il/"  # tracking parameters dropped


def test_index_ignores_group_terms():
    idx = catalogs.CatalogIndex("BuyMe ALL", catalogs.parse_buyme(ALL)["entries"])
    assert idx.find(["פוט לוקר"])["title"] == "Foot Locker"
    assert idx.find(["סלאס"])["title"] == "SELAS"
    assert idx.find(["קבוצת פוקס"]) is None  # shared by 3 businesses: not a name
    assert idx.find(["Zara"]) is None
    assert idx.find(["x"], domain="https://www.fox.co.il")["title"] == "FOX"


def test_monthly_refresh_only_held_networks():
    calls = []
    assert set(refresh(calls)) == {"BuyMe ALL", "BuyMe Fashion"}  # the sample holds these two
    assert len(calls) == 2
    assert refresh(calls) == [] and len(calls) == 2  # same month: nothing downloaded


def test_new_month_refreshes_and_failure_keeps_last_list(monkeypatch):
    calls = []
    refresh(calls)
    monkeypatch.setattr(costs, "month_key", lambda dt=None: "2099-01")
    assert catalogs.stored("BuyMe ALL")["stale"] is True  # last month's list still usable
    assert refresh(calls, fail=True) == []  # BuyMe down
    assert (
        catalogs.stored("BuyMe ALL")["stale"] is True
        and len(catalogs.stored("BuyMe ALL")["entries"]) == 4
    )
    assert set(refresh(calls)) == {"BuyMe ALL", "BuyMe Fashion"}
    assert catalogs.stored("BuyMe ALL")["stale"] is False


def ids(res):
    return {v.voucher_id: v for v in res.vouchers}


def test_list_decides_network_coupons():
    refresh([])
    # Foot Locker is on BuyMe ALL's list -> confirmed by the list
    fl = ids(local_voucher.search(asyncio.run(normalize("פוט לוקר"))))
    assert fl["V006"].accepted == "yes" and fl["V006"].accepted_by == "list"
    assert fl["V006"].place == "Foot Locker" and fl["V006"].online_redeem is True
    # Nike: the seed list says BuyMe ALL works, but the official list doesn't have it
    nike = local_voucher.search(asyncio.run(normalize("Nike")))
    assert "V006" not in ids(nike) and "official list" in nike.note
    # Castro via the BuyMe Fashion list
    assert ids(local_voucher.search(asyncio.run(normalize("Castro"))))["V009"].accepted_by == "list"


def test_store_names_from_lists_are_recognized():
    refresh([])
    s = asyncio.run(normalize("Selas", allow_llm=False))
    assert (s.canonical, s.method) == ("SELAS", "catalog")
    assert asyncio.run(normalize("סלאס", allow_llm=False)).canonical == "SELAS"


def test_store_site_agent_not_asked_about_listed_networks(monkeypatch):
    captured = {}

    async def fake_run(store, held):
        captured["held"] = held

    monkeypatch.setattr(orchestrator.web_research, "run", fake_run)
    spec, fn = orchestrator.default_workers()[0]
    asyncio.run(fn(asyncio.run(normalize("Nike"))))
    assert "BuyMe ALL" not in captured["held"] and "Max Gift Card" in captured["held"]


def test_status_endpoint():
    from fastapi.testclient import TestClient

    from app.main import app

    refresh([])
    st = {s["network"]: s for s in TestClient(app).get("/api/catalogs").json()}
    assert st["BuyMe ALL"]["places"] == 4 and st["BuyMe ALL"]["stale"] is False
    assert st["BuyMe Foody"]["places"] == 0  # not held -> never downloaded


@pytest.fixture(autouse=True)
def _clear_index_cache():
    catalogs._index_cache.clear()
    yield
    catalogs._index_cache.clear()


def test_sleeps_until_next_month():
    from datetime import datetime

    nov18 = datetime(2026, 11, 18, 17, 0, tzinfo=costs.TZ)
    assert round(catalogs.seconds_until_next_month(nov18) / 3600, 1) == round(
        (12 * 24 + 7 + 5 / 60), 1
    )
    dec31 = datetime(2026, 12, 31, 23, 0, tzinfo=costs.TZ)
    assert 3600 < catalogs.seconds_until_next_month(dec31) < 2 * 3600  # -> 1 Jan 2027


def test_dream_card_matches_fox_group_brands_as_verify():
    from app.agents.normalizer import normalize as norm

    store = asyncio.run(norm("פליינג טייגר", allow_llm=False))
    assert store.canonical == "Flying Tiger Copenhagen"
