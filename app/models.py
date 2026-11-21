"""Pydantic contract shared by agents, API and UI."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

OfferKind = Literal["own_voucher", "club_discount", "gift_card", "store_promo", "other"]
Confidence = Literal["low", "medium", "high"]
SourceStatus = Literal["ok", "empty", "timeout", "error", "disabled", "mock"]


class NormalizedStore(BaseModel):
    query: str
    canonical: str
    aliases: list[str] = []
    domain: str | None = None
    category: str | None = None
    method: Literal["exact", "fuzzy", "catalog", "llm", "raw"] = "raw"


class Voucher(BaseModel):
    voucher_id: str
    issuer: str
    card_network: str | None = None
    valid_stores: list[str] = []
    code: str | None = None  # stays local: never sent to the LLM or web
    face_value: float | None = None
    paid_amount: float | None = None
    remaining_balance: float | None = None
    currency: str = "ILS"
    purchase_date: date | None = None
    expiry_date: date | None = None
    status: str = "unused"
    purchased_from: str | None = None
    notes: str | None = None
    link: str | None = None  # web page to use/redeem it
    source_file: str | None = None
    added_by: str | None = None
    used_by: str | None = None
    image: str | None = None  # file name of the coupon's photo (app coupons only); never sent out
    # filled by the matcher
    match: Literal["direct", "network"] | None = None
    # for network vouchers: "yes" | "no" | "unknown" | "not_listed"
    accepted: str | None = None
    accepted_by: str | None = None  # "list" (the card's official list) | "store site"
    place: str | None = None  # the business's name on the card's list
    online_redeem: bool | None = None
    place_note: str | None = None  # the list's redemption terms, e.g. "dine-in only"
    days_to_expiry: int | None = None

    def for_llm(self) -> dict[str, Any]:
        """Safe view for prompts: no code, no file path."""
        return self.model_dump(
            mode="json",
            exclude={
                "code",
                "link",
                "source_file",
                "notes",
                "voucher_id",
                "added_by",
                "used_by",
                "image",
            },  # fmt: skip
        )


class Offer(BaseModel):
    source: str
    kind: OfferKind = "other"
    title: str
    discount_pct: float | None = None
    # what you pay out of pocket for ₪100 of goods (lower is better)
    effective_cost_per_100: float | None = None
    where: str | None = None  # where to buy/use it
    url: str | None = None
    terms: str | None = None
    exclusions: str | None = None
    confidence: Confidence = "medium"
    evidence_url: str | None = None
    voucher_id: str | None = None


class SourceResult(BaseModel):
    source: str  # stable id, e.g. "local", "store_site", "club:<id>"
    label: str
    status: SourceStatus = "ok"
    offers: list[Offer] = []
    vouchers: list[Voucher] = []
    note: str | None = None
    extra: dict[str, Any] = {}
    elapsed_ms: int = 0
    cached: bool = False
    saved_at: str | None = None  # when this (saved) online result was fetched
    cost_usd: float = 0.0


class RankedOption(BaseModel):
    title: str
    source: str
    effective_cost_per_100: float | None = None
    note: str | None = None


class Recommendation(BaseModel):
    preliminary: bool = False
    best_title: str
    best_source: str | None = None
    reason: str
    steps: list[str] = []
    ranked: list[RankedOption] = []
    caveats: list[str] = []
    generated_by: Literal["rules", "llm"] = "rules"


class SourceSpec(BaseModel):
    id: str
    label: str
    kind: Literal["local", "store", "club"]
    needs_llm: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
