"""Local Voucher Agent: searches the user's .xlsx/.csv files (read-only, no network)."""

import logging
import math
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from rapidfuzz import fuzz

from app.agents.normalizer import key
from app.config import get_settings
from app.models import NormalizedStore, Offer, SourceResult, Voucher

log = logging.getLogger(__name__)

SOURCE_ID, LABEL = "local", "My vouchers"


def _clean(v: Any) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _to_date(v: Any) -> date | None:
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, datetime | pd.Timestamp):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(str(v), dayfirst=True).date()
    except (ValueError, TypeError):
        return None


def _to_float(v: Any) -> float | None:
    v = _clean(v)
    if v is None:
        return None
    try:
        return float(str(v).replace("₪", "").replace(",", ""))
    except ValueError:
        return None


def _safe_link(v: Any) -> str | None:
    from app.coupon_store import clean_link

    try:
        return clean_link(str(v)) if v else None
    except ValueError:
        return None


def _row_to_voucher(row: dict[str, Any], source_file: str) -> Voucher | None:
    r = {str(k).strip().lower(): _clean(v) for k, v in row.items()}
    if not r.get("issuer") and not r.get("valid_stores") and not r.get("card_network"):
        return None
    stores = [s.strip() for s in str(r.get("valid_stores") or "").split(";") if s.strip()]
    return Voucher(
        voucher_id=str(r.get("voucher_id") or f"{source_file}:{id(row)}"),
        issuer=str(r.get("issuer") or r.get("card_network") or "?"),
        card_network=r.get("card_network"),
        valid_stores=stores,
        code=None if r.get("code") is None else str(r.get("code")),
        face_value=_to_float(r.get("face_value")),
        paid_amount=_to_float(r.get("paid_amount")),
        remaining_balance=_to_float(r.get("remaining_balance")),
        currency=str(r.get("currency") or "ILS"),
        purchase_date=_to_date(r.get("purchase_date")),
        expiry_date=_to_date(r.get("expiry_date")),
        status=str(r.get("status") or "unused").lower(),
        purchased_from=r.get("purchased_from"),
        notes=r.get("notes"),
        link=_safe_link(r.get("link")),
        source_file=source_file,
    )


@lru_cache(maxsize=32)
def _read_file(path: Path, mtime: float) -> tuple[Voucher, ...]:
    if path.suffix.lower() == ".csv":
        frames = [pd.read_csv(path, dtype=str)]
    else:
        frames = list(pd.read_excel(path, sheet_name=None).values())  # all sheets, read-only
    out = []
    for df in frames:
        for row in df.to_dict(orient="records"):
            v = _row_to_voucher(row, path.name)
            if v:
                out.append(v)
    return tuple(out)


def load_vouchers(voucher_dir: Path | None = None) -> list[Voucher]:
    d = voucher_dir or get_settings().voucher_dir
    out: list[Voucher] = []
    for p in sorted(d.glob("*")):
        if p.suffix.lower() not in (".xlsx", ".xls", ".csv") or p.name.startswith("~$"):
            continue
        try:
            out.extend(_read_file(p, p.stat().st_mtime))
        except Exception as e:  # noqa: BLE001 - one bad file must not hide the others
            log.warning("could not read %s: %s", p, e)
    if voucher_dir is None:  # plus the coupons added in the app
        from app import coupon_store

        out.extend(coupon_store.list_coupons())
    return out


@lru_cache
def _networks(path: Path, mtime: float) -> list[dict]:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("networks", [])


def card_networks() -> list[dict]:
    p = get_settings().data_dir / "card_networks.yaml"
    return _networks(p, p.stat().st_mtime) if p.exists() else []


def network_entry(name: str | None) -> dict | None:
    if not name:
        return None
    k = key(name)
    for n in card_networks():
        if k in {key(x) for x in [n["name"], *n.get("aliases", [])]}:
            return n
    return None


def is_usable(v: Voucher, today: date) -> bool:
    if v.status == "used":
        return False
    if v.remaining_balance is not None and v.remaining_balance <= 0:
        return False
    if v.expiry_date and v.expiry_date < today:
        return False
    return True


def network_keys(name: str | None) -> set[str]:
    """All comparison keys for a card network: its own name + aliases from card_networks.yaml."""
    if not name:
        return set()
    net = network_entry(name)
    names = [name, *([net["name"], *net.get("aliases", [])] if net else [])]
    return {key(x) for x in names} - {""}


def acceptance_map(accepted_networks: list[dict] | None) -> dict[str, str]:
    """Store-site answer [{name, accepted: yes|no|unknown}] -> {network key: status}."""
    out: dict[str, str] = {}
    for n in accepted_networks or []:
        status = str(n.get("accepted", "unknown")).strip().lower()
        if n.get("name") and status in ("yes", "no", "unknown"):
            for k in network_keys(str(n["name"])):
                out[k] = status
    return out


def match_voucher(
    v: Voucher, store: NormalizedStore, accepted: dict[str, str] | None = None
) -> tuple[str | None, str | None, dict]:
    """Returns (match, acceptance, extra). match: "direct" | "network" | None.

    For a gift-card network, the network's official merchant list (app/catalogs.py) decides
    when we have it. Otherwise: the store's website confirmed it ("yes"), or the seed list
    says the store takes it and the website didn't say "no".
    """
    store_keys = {key(x) for x in [store.canonical, *store.aliases]} - {""}
    for n in [*v.valid_stores, v.issuer]:
        nk = key(n)
        if nk in store_keys or any(fuzz.ratio(nk, sk) >= 92 for sk in store_keys):
            return "direct", None, {}
    if not v.card_network:
        return None, None, {}

    from app import catalogs

    has_list, place = catalogs.lookup(
        v.card_network, [store.canonical, *store.aliases, store.query], store.domain
    )
    if has_list:
        if place is None:
            return None, "not_listed", {}
        return (
            "network",
            "yes",
            {
                "accepted_by": "list",
                "place": place["title"],
                "online_redeem": place.get("online"),
                "place_note": place.get("note"),
            },
        )

    status = next(
        (accepted[k] for k in network_keys(v.card_network) if k in (accepted or {})), None
    )
    net = network_entry(v.card_network)
    seeded = bool(net) and key(store.canonical) in {key(s) for s in net.get("stores", [])}
    extra = {"accepted_by": "store site"} if status == "yes" else {}
    if status == "yes" or (seeded and status != "no"):
        return "network", status, extra
    return None, status, {}


def held_networks(today: date | None = None) -> list[str]:
    """Names of card networks the user holds with usable balance (safe to send online)."""
    today = today or date.today()
    return sorted(
        {v.card_network for v in load_vouchers() if v.card_network and is_usable(v, today)}
    )


def _offer(v: Voucher) -> Offer:
    bal = v.remaining_balance
    disc = None
    if v.face_value and v.paid_amount is not None and v.face_value > 0:
        disc = round((1 - v.paid_amount / v.face_value) * 100, 1)
    label = v.card_network or v.issuer
    title = f"{label}: ₪{bal:,.0f} left" if bal is not None else label
    return Offer(
        source=SOURCE_ID,
        kind="own_voucher",
        title=title,
        discount_pct=disc,
        effective_cost_per_100=0.0,  # already paid: no new money out of pocket
        where="Use at the register (you already own it)",
        terms=f"Expires {v.expiry_date.isoformat()}" if v.expiry_date else None,
        confidence="high" if v.match == "direct" or v.accepted == "yes" else "medium",
        voucher_id=v.voucher_id,
    )


def search(
    store: NormalizedStore,
    today: date | None = None,
    accepted_networks: list[dict] | None = None,
) -> SourceResult:
    """accepted_networks: the store-site agent's answer, used to confirm or rule out networks."""
    t0 = time.perf_counter()
    today = today or date.today()
    accepted = acceptance_map(accepted_networks)
    found: list[Voucher] = []
    skipped = rejected = not_listed = 0
    for v in load_vouchers():
        m, status, extra = match_voucher(v, store, accepted)
        if not m:
            rejected += status == "no" and is_usable(v, today)
            not_listed += status == "not_listed" and is_usable(v, today)
            continue
        if not is_usable(v, today):
            skipped += 1
            continue
        found.append(
            v.model_copy(
                update={
                    "match": m,
                    "accepted": status,
                    **extra,
                    "days_to_expiry": (v.expiry_date - today).days if v.expiry_date else None,
                }
            )
        )
    # direct first, then confirmed networks, then soonest expiry, then biggest balance
    found.sort(
        key=lambda v: (
            v.match != "direct",
            v.accepted != "yes",
            v.days_to_expiry or 99999,
            -(v.remaining_balance or 0),
        )
    )
    notes = []
    if skipped:
        notes.append(f"{skipped} matching voucher(s) hidden: expired or used up")
    if not_listed:
        notes.append(
            f"{not_listed} gift card(s) hidden: this store isn't on the card's official list"
        )
    if rejected:
        notes.append(f"{rejected} voucher(s) hidden: the store's website says it doesn't take them")
    if any(v.match == "network" and v.accepted != "yes" for v in found):
        notes.append("Gift cards marked 'Verify' aren't confirmed by the store yet")
    return SourceResult(
        source=SOURCE_ID,
        label=LABEL,
        status="ok" if found else "empty",
        offers=[_offer(v) for v in found],
        vouchers=found,
        note=". ".join(notes) or None,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )
