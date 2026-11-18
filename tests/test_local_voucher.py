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
