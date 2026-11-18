import asyncio

import pytest
from fastapi.testclient import TestClient

from app import coupon_store
from app.agents import coupon_reader, local_voucher
from app.agents.normalizer import normalize
from app.main import app

client = TestClient(app)


def add(**kw):
    r = client.post("/api/coupons", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def test_add_store_coupon_then_search_finds_it():
    c = add(
        valid_stores=["זארה"],
        code="ZR-1",
        face_value=200,
        paid_amount=170,
        expiry_date="2099-01-01",
        purchased_from="Gift",
    )
    assert c["voucher_id"].startswith("C") and c["remaining_balance"] == 200  # defaults to value
    res = local_voucher.search(asyncio.run(normalize("Zara")))
    assert [v.voucher_id for v in res.vouchers] == [c["voucher_id"]]


def test_add_network_coupon():
    c = add(card_network="BuyMe ALL", remaining_balance=80)
    assert c["issuer"] == "BuyMe ALL" and c["valid_stores"] == []
    res = local_voucher.search(asyncio.run(normalize("Foot Locker")))
    assert any(v.voucher_id == c["voucher_id"] and v.match == "network" for v in res.vouchers)


def test_validation():
    r = client.post("/api/coupons", json={"code": "X"})
    assert r.status_code == 422 and "store" in r.text
    r = client.post("/api/coupons", json={"valid_stores": ["Nike"], "face_value": -5})
    assert r.status_code == 422


def test_list_update_mark_used_delete():
    c = add(valid_stores=["Nike"], face_value=100)
    vid = c["voucher_id"]
    listed = {x["voucher_id"]: x for x in client.get("/api/coupons").json()}
    assert listed[vid]["editable"] and not listed["V001"]["editable"]  # Excel rows read-only

    r = client.patch(f"/api/coupons/{vid}", json={"remaining_balance": 30})
    assert r.json()["status"] == "partial" and r.json()["remaining_balance"] == 30
    r = client.patch(f"/api/coupons/{vid}", json={"remaining_balance": 0})
    assert r.json()["status"] == "used"
    res = local_voucher.search(asyncio.run(normalize("Nike")))
    assert vid not in {v.voucher_id for v in res.vouchers}  # used: hidden from search

    assert client.delete(f"/api/coupons/{vid}").status_code == 204
    assert client.delete(f"/api/coupons/{vid}").status_code == 404


def test_excel_coupons_cannot_be_edited():
    assert client.patch("/api/coupons/V001", json={"remaining_balance": 1}).status_code == 404
    assert client.delete("/api/coupons/V001").status_code == 404


def test_export_csv_contains_both_sources():
    add(valid_stores=["Golf"], face_value=50)
    r = client.get("/api/coupons/export.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert "FL-DEMO-1001" in r.text and "Golf" in r.text


def test_extract_needs_llm():
    r = client.post("/api/coupons/extract", json={"media_type": "image/jpeg", "data": "aGk="})
    assert r.status_code == 503


def test_extract_with_mocked_llm(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("COUPONAI_DISABLE_LLM", "0")
    monkeypatch.setenv("COUPONAI_FORCE_LLM", "1")
    get_settings.cache_clear()
    seen = {}

    async def fake_structured(task, content, schema, **kw):
        seen["content"] = content
        return {
            "is_coupon": True,
            "issuer": "BuyMe",
            "card_network": "BuyMe ALL",
            "valid_stores": [],
            "code": "1234",
            "face_value": 250,
            "paid_amount": None,
            "remaining_balance": 250,
            "currency": "ILS",
            "purchase_date": None,
            "expiry_date": "2030-01-01",
            "purchased_from": "BuyMe",
            "notes": None,
            "unsure_fields": [],
        }

    monkeypatch.setattr(coupon_reader.llm, "structured", fake_structured)
    r = client.post("/api/coupons/extract", json={"media_type": "image/jpeg", "data": "aGk="})
    assert r.status_code == 200 and r.json()["card_network"] == "BuyMe ALL"
    assert seen["content"][0]["type"] == "image"
    # extraction alone saves nothing
    assert coupon_store.list_coupons() == []


@pytest.mark.parametrize("media_type,data", [("image/tiff", "aGk="), ("image/jpeg", "not base64!")])
def test_extract_rejects_bad_images(media_type, data):
    with pytest.raises(ValueError):
        coupon_reader.decode_image(data, media_type)
