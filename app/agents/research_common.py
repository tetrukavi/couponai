"""Shared bits for the web-research workers: output format + parsing into Offers."""

import logging
from typing import Any

from pydantic import ValidationError

from app.models import Offer

log = logging.getLogger(__name__)

OUTPUT_FORMAT = """\
Be fast: a few targeted searches (Hebrew and English), then answer. Prefer official pages from
the last 12 months. Do not invent numbers: if a discount isn't stated, use null.

Reply with ONE fenced ```json block and nothing after it, shaped like:
{
  "summary": "one short sentence",
  "offers": [
    {
      "kind": "club_discount | gift_card | store_promo | other",
      "title": "short, e.g. '15% off Foot Locker vouchers in <club>'",
      "discount_pct": 15,
      "effective_cost_per_100": 85,
      "where": "where the user buys or uses it (site/app/section name)",
      "url": "https://... page where it is offered",
      "terms": "key terms, limits, min purchase, validity",
      "exclusions": "excluded items/branches/sale items, or null",
      "confidence": "low | medium | high",
      "evidence_url": "https://... page that proves it"
    }
  ]EXTRA
}
effective_cost_per_100 = shekels paid out of pocket to get ₪100 of goods at this store.
Return "offers": [] if you find nothing relevant."""


def output_format(extra: str = "") -> str:
    return OUTPUT_FORMAT.replace("EXTRA", extra)


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("%", "").replace("₪", "").strip())
    except ValueError:
        return None


def parse_offers(data: dict[str, Any], source: str) -> list[Offer]:
    out: list[Offer] = []
    for raw in data.get("offers") or []:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        raw = {**raw, "source": source}
        raw["discount_pct"] = _num(raw.get("discount_pct"))
        raw["effective_cost_per_100"] = _num(raw.get("effective_cost_per_100"))
        if raw["effective_cost_per_100"] is None and raw["discount_pct"] is not None:
            raw["effective_cost_per_100"] = round(100 - raw["discount_pct"], 2)
        if raw.get("kind") not in ("club_discount", "gift_card", "store_promo", "other"):
            raw["kind"] = "other"
        if raw.get("confidence") not in ("low", "medium", "high"):
            raw["confidence"] = "low"
        try:
            out.append(Offer.model_validate(raw))
        except ValidationError as e:
            log.info("dropping malformed offer from %s: %s", source, e)
    return out
