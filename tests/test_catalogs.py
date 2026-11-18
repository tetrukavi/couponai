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


OTHER = {"supplier": {"name": "OTHER"}, "brands": [brand(99, "Some Other Place", ["other"])]}


def transport(calls, fail=False):
    def handler(req: httpx.Request):
        url = str(req.url)
        calls.append(url)
        if fail:
            return httpx.Response(503)
        if "htzone" in url:
            return httpx.Response(
                200,
                text=next_page(
                    {
                        "businesses": [
                            {
                                "id": 1,
                                "name": "מסעדת מארי",
                                "englishName": "MAREE",
                                "termsHtml": "בישיבה בלבד",
                                "honorOnline": False,
                                "categoryIds": [],
                            }
                        ]
                    }
                ),
            )
        body = ALL if "/13438757/" in url else FASHION if "/20620/" in url else OTHER
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def listed_networks() -> set[str]:
    return {n["name"] for n in catalogs.networks_to_fetch()}


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


def test_monthly_refresh_downloads_every_list_once():
    calls = []
    every = listed_networks()
    assert {"BuyMe ALL", "BuyMe Kosher", "chefZone", "All-in Zone"} <= every
    assert "נופשונית" not in every and "Isracard GiftCard" not in every  # no downloadable list
    assert set(refresh(calls)) == every and len(calls) == len(every)
    assert refresh(calls) == [] and len(calls) == len(every)  # same month: nothing downloaded


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
    assert set(refresh(calls)) == listed_networks()
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

    async def fake_run(store, held, buyable=None):
        captured["held"] = held
        captured["buyable"] = [n["name"] for n in buyable or []]

    monkeypatch.setattr(orchestrator.web_research, "run", fake_run)
    spec, fn = orchestrator.default_workers()[0]
    asyncio.run(fn(asyncio.run(normalize("Nike"))))
    assert "BuyMe ALL" not in captured["held"] and "Max Gift Card" in captured["held"]
    # cards you don't hold and that have no list are checked by the store-site search
    assert {"Isracard GiftCard", "ביחד בשבילך", "נופשונית"} <= set(captured["buyable"])
    assert "BuyMe Kosher" not in captured["buyable"]  # has a list: checked for free instead


def test_status_endpoint():
    from fastapi.testclient import TestClient

    from app.main import app

    refresh([])
    st = {s["network"]: s for s in TestClient(app).get("/api/catalogs").json()}
    assert st["BuyMe ALL"]["places"] == 4 and st["BuyMe ALL"]["stale"] is False
    assert st["BuyMe Kosher"]["places"] == 1  # downloaded even though no coupon is held
    assert "Isracard GiftCard" not in st  # no downloadable list


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


# ---- HitechZone and Swish pages (Next.js data embedded in the HTML) ----


def next_page(data: dict) -> str:
    """Build HTML the way Next.js embeds data: JSON split across __next_f.push chunks."""
    text = 'x:{"page":' + json.dumps(data, ensure_ascii=False) + "}"
    half = len(text) // 2
    return "".join(
        f"<script>self.__next_f.push([1,{json.dumps(part, ensure_ascii=False)}])</script>"
        for part in (text[:half], text[half:])
    )


def test_parse_htzone():
    page = next_page({
        "categories": [{"id": 15, "name": "מסעדות"}],
        "businesses": [
            {"id": 1, "name": "מסעדת מארי", "englishName": "MAREE Sky Lounge", "url": "https://maree.co.il/",
             "termsHtml": "<b>המימוש בישיבה</b> במסעדה בלבד", "honorOnline": False, "categoryIds": [15]},
            {"id": 2, "name": "ג'נספורט", "englishName": "$undefined", "url": "$undefined",
             "termsHtml": "", "honorOnline": True, "categoryIds": []},
        ],
    })  # fmt: skip
    d = catalogs.parse_htzone(page)
    m, j = d["entries"]
    assert m["title"] == "מסעדת מארי" and "MAREE Sky Lounge" in m["terms"]
    assert m["note"] == "המימוש בישיבה במסעדה בלבד" and m["categories"] == ["מסעדות"]
    assert j["online"] is True and j["site"] is None and j["terms"] == []
    idx = catalogs.CatalogIndex("chefZone", d["entries"])
    assert idx.find(["Maree Sky Lounge"])["title"] == "מסעדת מארי"


def test_parse_swish_merges_online_and_store_variants():
    page = next_page({"tags": [
        {"tagName": "אופנה", "chainsByWallet": [
            {"categoryNumber": 105379, "storeName": "שילב", "chainId": 1, "tagName": "אופנה",
             "searchKeyWords": "shilav", "webSite": "https://shilav.co.il", "mustToKnow": None},
            {"categoryNumber": 999, "storeName": "Other card only", "chainId": 9, "tagName": "אופנה"},
        ]},
        {"tagName": "רכישה אונליין", "chainsByWallet": [
            {"categoryNumber": 105379, "storeName": "שילב אונליין", "chainId": 2,
             "tagName": "רכישה אונליין", "mustToKnow": "<p>עד 1000 ש\"ח לעסקה</p>"},
            {"categoryNumber": 105379, "storeName": "פוט לוקר אונליין", "chainId": 3,
             "tagName": "רכישה אונליין"},
        ]},
    ]})  # fmt: skip
    d = catalogs.parse_swish(page, "105379")
    by = {e["title"]: e for e in d["entries"]}
    assert set(by) == {"שילב", "פוט לוקר"}  # other cards' rows ignored, "אונליין" stripped
    assert by["שילב"]["online"] and by["שילב"]["in_store"] and "shilav" in by["שילב"]["terms"]
    assert by["פוט לוקר"]["online"] and not by["פוט לוקר"]["in_store"]
    assert by["שילב"]["note"] == 'עד 1000 ש"ח לעסקה'


def test_htzone_list_used_for_my_coupons():
    from app import coupon_store

    coupon_store.add_coupon(coupon_store.CouponIn(card_network="chefZone", face_value=68))
    refresh([])
    maree = local_voucher.search(asyncio.run(normalize("MAREE", allow_llm=False)))
    [v] = [v for v in maree.vouchers if v.card_network == "chefZone"]
    assert v.accepted_by == "list" and v.place_note == "בישיבה בלבד"


def test_cards_you_could_buy():
    from app.agents import buyable

    refresh([])
    res = asyncio.run(buyable.run(asyncio.run(normalize("Selas", allow_llm=False))))
    titles = [o.title for o in res.offers]
    assert "BuyMe ALL works here" not in titles  # you already hold BuyMe ALL (sample V006)
    res = asyncio.run(buyable.run(asyncio.run(normalize("MAREE", allow_llm=False))))
    by = {o.title.split(" works")[0]: o for o in res.offers}
    assert "chefZone" in by and by["chefZone"].where == "Buy at HitechZone (members)"
    assert by["chefZone"].url == "https://www.htzone.co.il/voucher-zone/1"
    assert "בישיבה בלבד" in by["chefZone"].terms and by["chefZone"].confidence == "high"
    assert res.extra["checked_lists"] >= 10


def test_store_site_yes_for_unheld_card_becomes_buy_offer(monkeypatch):
    from app import llm
    from app.agents import web_research

    async def fake_research(task, prompt, **k):
        assert "Isracard GiftCard" in prompt
        return (
            '```json\n{"summary": "s", "offers": [], "accepted_networks": ['
            '{"name": "Isracard GiftCard", "accepted": "yes", "evidence_url": "https://zara.example/gift"}]}\n```',
            [],
            0.01,
        )

    monkeypatch.setattr(llm, "research", fake_research)
    nets = [n for n in local_voucher.card_networks() if n["name"] == "Isracard GiftCard"]
    res = asyncio.run(web_research.run(asyncio.run(normalize("Zara", allow_llm=False)), [], nets))
    [o] = res.offers
    assert o.title == "Isracard GiftCard works here" and o.where == "Buy at Isracard"
    assert o.evidence_url == "https://zara.example/gift"


def test_prefix_of_exactly_one_name_matches():
    entries = [
        {"title": "מסעדת מארי", "terms": ["MAREE Sky Lounge"], "site": None},
        {"title": "Maria Bar", "terms": [], "site": None},
        {"title": "Golf", "terms": [], "site": None},
        {"title": "Golfito", "terms": [], "site": None},
    ]
    idx = catalogs.CatalogIndex("x", entries)
    assert idx.find(["MAREE"])["title"] == "מסעדת מארי"
    assert idx.find(["Golf"])["title"] == "Golf"  # exact title still wins
    assert idx.find(["Golfi"])["title"] == "Golfito"
    assert idx.find(["Mar"]) is None  # too short / ambiguous


def test_failed_download_reason_is_recorded():
    refresh([], fail=True)
    st = {s["network"]: s for s in catalogs.status()}
    assert st["BuyMe ALL"]["last_error"]["error"] == "HTTP 503 from buyme.co.il"
    refresh([])
    st = {s["network"]: s for s in catalogs.status()}
    assert st["BuyMe ALL"]["last_error"] is None
