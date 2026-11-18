import asyncio
from datetime import date

import pytest

from app.agents import local_voucher
from app.agents.normalizer import normalize


def ids(res):
    return {v.voucher_id: v.match for v in res.vouchers}


@pytest.mark.parametrize("q", ["Foot Locker", "פוט לוקר", "footlocker", "FOOT  LOCKER"])
def test_foot_locker_matches(q):
    res = local_voucher.search(asyncio.run(normalize(q)))
    got = ids(res)
    assert got["V001"] == "direct" and got["V002"] == "direct"
    assert got["V006"] == "network"  # BuyMe ALL via card_networks.yaml
    assert "V012" not in got  # expired
    assert res.status == "ok"
    # direct matches first
    assert [v.match for v in res.vouchers][:2] == ["direct", "direct"]


def test_expired_and_used_filtered():
    castro = local_voucher.search(asyncio.run(normalize("קסטרו")))
    assert "V004" not in ids(castro)  # expired Castro voucher
    pharm = local_voucher.search(asyncio.run(normalize("Super-Pharm")))
    assert "V005" not in ids(pharm)  # used up


def test_unknown_store_empty():
    res = local_voucher.search(asyncio.run(normalize("Some Random Kiosk")))
    assert res.status == "empty" and res.vouchers == []


def test_offers_are_already_paid():
    res = local_voucher.search(asyncio.run(normalize("Foot Locker")))
    assert all(o.kind == "own_voucher" and o.effective_cost_per_100 == 0 for o in res.offers)


def test_csv_files_supported(tmp_path, monkeypatch):
    (tmp_path / "extra.csv").write_text(
        "voucher_id,issuer,valid_stores,remaining_balance,expiry_date,status\n"
        "C1,Zara,Zara;זארה,120,2099-01-01,unused\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COUPONAI_VOUCHER_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    res = local_voucher.search(asyncio.run(normalize("זארה")), today=date(2026, 1, 1))
    assert ids(res) == {"C1": "direct"}


# ---- store-site confirmation of gift-card networks ----


def test_confirmed_network_appears_even_without_seed():
    store = asyncio.run(normalize("Zara"))  # no seed list covers Zara for תו הזהב
    assert "V008" not in ids(local_voucher.search(store))
    res = local_voucher.search(
        store, accepted_networks=[{"name": "Tav Hazahav", "accepted": "yes"}]
    )
    [v] = [v for v in res.vouchers if v.voucher_id == "V008"]
    assert v.match == "network" and v.accepted == "yes"  # alias "Tav Hazahav" -> תו הזהב


def test_store_saying_no_hides_seeded_network():
    store = asyncio.run(normalize("Foot Locker"))
    assert "V007" in ids(local_voucher.search(store))  # Max Gift Card, seeded
    res = local_voucher.search(
        store, accepted_networks=[{"name": "max gift card", "accepted": "no"}]
    )
    assert "V007" not in ids(res)
    assert "doesn't take them" in res.note


def test_unknown_keeps_seeded_network_unconfirmed():
    store = asyncio.run(normalize("Foot Locker"))
    res = local_voucher.search(
        store, accepted_networks=[{"name": "BuyMe ALL", "accepted": "unknown"}]
    )
    [v] = [v for v in res.vouchers if v.voucher_id == "V006"]
    assert v.accepted == "unknown" and "Verify" in res.note


def test_confirmed_networks_sort_before_unconfirmed():
    store = asyncio.run(normalize("Foot Locker"))
    res = local_voucher.search(
        store, accepted_networks=[{"name": "BuyMe Fashion", "accepted": "yes"}]
    )
    order = [v.voucher_id for v in res.vouchers if v.match == "network"]
    assert order[0] == "V009"


def test_hebrew_english_network_aliases():
    assert local_voucher.network_keys("Dream Card") == local_voucher.network_keys("דרים קארד")
