"""Coupons added through the app, stored in a local SQLite file (data/coupons.sqlite).

Your Excel/CSV files in data/vouchers/ stay read-only; this is the only place the app writes
coupon data.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import get_settings
from app.models import Voucher

_lock = threading.Lock()

_COLUMNS = (
    "issuer", "card_network", "valid_stores", "code", "face_value", "paid_amount",
    "remaining_balance", "currency", "purchase_date", "expiry_date", "status",
    "purchased_from", "notes", "link",
)  # fmt: skip

NOTES_MAX, LINK_MAX = 2000, 500


def clean_link(v: str | None) -> str | None:
    """Only real web links (http/https): never javascript:, data: or other schemes."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if "://" not in v and "." in v.split("/")[0]:  # "buyme.co.il" -> "https://buyme.co.il"
        v = "https://" + v
    u = urlparse(v)
    if u.scheme not in ("http", "https") or not u.netloc or any(c.isspace() for c in v):
        raise ValueError("link must be a web address starting with https://")
    return v


def _blank(v):
    if isinstance(v, str):
        v = v.strip()
    return v or None


class CouponIn(BaseModel):
    """What the "Add coupon" form sends."""

    issuer: str | None = Field(default=None, max_length=80)
    card_network: str | None = Field(default=None, max_length=80)
    valid_stores: list[str] = Field(default_factory=list, max_length=50)
    code: str | None = Field(default=None, max_length=200)
    face_value: float | None = Field(default=None, ge=0)
    paid_amount: float | None = Field(default=None, ge=0)
    remaining_balance: float | None = Field(default=None, ge=0)
    currency: str = "ILS"
    purchase_date: date | None = None
    expiry_date: date | None = None
    status: str = "unused"
    purchased_from: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=NOTES_MAX)
    link: str | None = Field(default=None, max_length=LINK_MAX)

    @field_validator("issuer", "card_network", "code", "purchased_from", "notes", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return _blank(v)

    @field_validator("link", mode="before")
    @classmethod
    def _link(cls, v):
        return clean_link(v)

    @field_validator("valid_stores", mode="before")
    @classmethod
    def _split_stores(cls, v):
        if isinstance(v, str):
            v = v.split(";")
        return [s.strip() for s in v or [] if s and s.strip()]

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in ("unused", "partial", "used"):
            raise ValueError("status must be unused, partial or used")
        return v

    @model_validator(mode="after")
    def _defaults(self):
        if not (self.issuer or self.card_network or self.valid_stores):
            raise ValueError("enter a store or a gift-card network")
        if not self.issuer:
            self.issuer = self.card_network or self.valid_stores[0]
        if self.card_network is None and not self.valid_stores:
            self.valid_stores = [self.issuer]
        if self.remaining_balance is None:
            self.remaining_balance = self.face_value
        return self


class CouponPatch(BaseModel):
    """Partial update. For notes/link/code an empty string clears the field."""

    remaining_balance: float | None = Field(default=None, ge=0)
    status: str | None = None
    expiry_date: date | None = None
    notes: str | None = Field(default=None, max_length=NOTES_MAX)
    link: str | None = Field(default=None, max_length=LINK_MAX)
    code: str | None = Field(default=None, max_length=200)

    @field_validator("notes", "code", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return _blank(v)

    @field_validator("link", mode="before")
    @classmethod
    def _link(cls, v):
        return clean_link(v)


@contextmanager
def _db(path: Path | None = None):
    p = path or get_settings().coupon_db_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS coupons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issuer TEXT NOT NULL, card_network TEXT, valid_stores TEXT, code TEXT,
                    face_value REAL, paid_amount REAL, remaining_balance REAL,
                    currency TEXT DEFAULT 'ILS', purchase_date TEXT, expiry_date TEXT,
                    status TEXT DEFAULT 'unused', purchased_from TEXT, notes TEXT,
                    created_at TEXT, updated_at TEXT)"""
            )
            have = {r[1] for r in conn.execute("PRAGMA table_info(coupons)")}
            for col in ("added_by", "used_by", "used_at", "link"):  # upgrade older databases
                if col not in have:
                    conn.execute(f"ALTER TABLE coupons ADD COLUMN {col} TEXT")
            yield conn
            conn.commit()
        finally:
            conn.close()


def _to_voucher(row: sqlite3.Row) -> Voucher:
    d = dict(row)
    return Voucher(
        voucher_id=f"C{d['id']}",
        issuer=d["issuer"],
        card_network=d["card_network"],
        valid_stores=[s for s in (d["valid_stores"] or "").split(";") if s],
        code=d["code"],
        face_value=d["face_value"],
        paid_amount=d["paid_amount"],
        remaining_balance=d["remaining_balance"],
        currency=d["currency"] or "ILS",
        purchase_date=d["purchase_date"],
        expiry_date=d["expiry_date"],
        status=d["status"] or "unused",
        purchased_from=d["purchased_from"],
        notes=d["notes"],
        link=d.get("link"),
        source_file="app",
        added_by=d.get("added_by"),
        used_by=d.get("used_by"),
    )


def _row_id(voucher_id: str) -> int:
    if not voucher_id.startswith("C") or not voucher_id[1:].isdigit():
        raise KeyError(voucher_id)
    return int(voucher_id[1:])


def list_coupons() -> list[Voucher]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
    return [_to_voucher(r) for r in rows]


def add_coupon(c: CouponIn, user: str | None = None) -> Voucher:
    now = datetime.now().isoformat(timespec="seconds")
    vals = c.model_dump()
    vals["valid_stores"] = ";".join(c.valid_stores)
    for k in ("purchase_date", "expiry_date"):
        vals[k] = vals[k].isoformat() if vals[k] else None
    with _db() as conn:
        cur = conn.execute(
            f"INSERT INTO coupons ({', '.join(_COLUMNS)}, created_at, updated_at, added_by) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))}, ?, ?, ?)",
            [vals[k] for k in _COLUMNS] + [now, now, user],
        )
        row = conn.execute("SELECT * FROM coupons WHERE id=?", (cur.lastrowid,)).fetchone()
    return _to_voucher(row)


def update_coupon(voucher_id: str, patch: CouponPatch, user: str | None = None) -> Voucher:
    rid = _row_id(voucher_id)
    changes = patch.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] not in ("unused", "partial", "used"):
        raise ValueError("status must be unused, partial or used")
    if changes.get("expiry_date"):
        changes["expiry_date"] = changes["expiry_date"].isoformat()
    # spending part of the balance makes it "partial"; zero makes it "used"
    if "remaining_balance" in changes and "status" not in changes:
        changes["status"] = "used" if changes["remaining_balance"] == 0 else "partial"
    changes["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if changes.get("status") in ("partial", "used") and user:  # someone spent from it
        changes["used_by"], changes["used_at"] = user, changes["updated_at"]
    with _db() as conn:
        sets = ", ".join(f"{k}=?" for k in changes)
        cur = conn.execute(f"UPDATE coupons SET {sets} WHERE id=?", [*changes.values(), rid])
        if cur.rowcount == 0:
            raise KeyError(voucher_id)
        row = conn.execute("SELECT * FROM coupons WHERE id=?", (rid,)).fetchone()
    return _to_voucher(row)


def delete_coupon(voucher_id: str) -> None:
    rid = _row_id(voucher_id)
    with _db() as conn:
        if conn.execute("DELETE FROM coupons WHERE id=?", (rid,)).rowcount == 0:
            raise KeyError(voucher_id)
