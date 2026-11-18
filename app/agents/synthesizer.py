"""Synthesizer: rank everything found. Rules-based instantly; Opus 5.5 refines when available."""

import json

from app import llm
from app.config import get_settings
from app.models import NormalizedStore, Offer, RankedOption, Recommendation, SourceResult

CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def rules_recommendation(
    store: NormalizedStore, results: list[SourceResult], preliminary: bool
) -> Recommendation:
    local = next((r for r in results if r.source == "local"), None)
    vouchers = list(local.vouchers) if local else []  # already filtered by the store's answer

    online: list[Offer] = [o for r in results if r.source != "local" for o in r.offers]
    online.sort(
        key=lambda o: (
            o.effective_cost_per_100 if o.effective_cost_per_100 is not None else 999,
            CONF_RANK[o.confidence],
        )
    )

    ranked: list[RankedOption] = []
    for v in vouchers:
        label = v.card_network or v.issuer
        exp = f", expires in {v.days_to_expiry} days" if v.days_to_expiry is not None else ""
        verify = ""
        if v.match == "network":
            verify = (
                " (store confirms it accepts this card)"
                if v.accepted == "yes"
                else " (check the store accepts it)"
            )
        ranked.append(
            RankedOption(
                title=f"Your {label} voucher ("
                + ("ticket" if v.remaining_balance is None else f"₪{v.remaining_balance:,.0f} left")
                + ")",
                source="local",
                effective_cost_per_100=0,
                note=f"Already paid{exp}{verify}",
            )
        )
    for o in online:
        ranked.append(
            RankedOption(
                title=o.title,
                source=o.source,
                effective_cost_per_100=o.effective_cost_per_100,
                note=o.terms,
            )
        )

    if not ranked:
        return Recommendation(
            preliminary=preliminary,
            best_title="Nothing found yet",
            reason=(
                "Still checking clubs and the store site…"
                if preliminary
                else f"No vouchers or club deals found for {store.canonical}."
            ),
        )
    best = ranked[0]
    steps = []
    if best.source == "local":
        v = vouchers[0]
        steps = [
            f"Use your {v.card_network or v.issuer} voucher at the register",
            f"Bought via {v.purchased_from}" if v.purchased_from else "Find it in your vouchers",
        ]
        if len(vouchers) > 1:
            steps.append(
                f"You hold {len(vouchers)} usable vouchers here; you can combine them if "
                "the store allows"
            )
        reason = "You already own a voucher valid here: spend it before buying anything new."
    else:
        o = online[0]
        steps = [
            f"Buy/activate it at: {o.where or o.url or best.source}",
            "Then pay with it at the register",
        ]
        reason = f"Lowest out-of-pocket cost found ({best.effective_cost_per_100} per ₪100)."
    return Recommendation(
        preliminary=preliminary,
        best_title=best.title,
        best_source=best.source,
        reason=reason,
        steps=steps,
        ranked=ranked[:8],
        generated_by="rules",
    )


_SCHEMA = {
    "type": "object",
    "properties": {
        "best_title": {"type": "string"},
        "best_source": {"type": "string"},
        "reason": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "source": {"type": "string"},
                    "effective_cost_per_100": {"type": ["number", "null"]},
                    "note": {"type": "string"},
                },
                "required": ["title", "source", "effective_cost_per_100", "note"],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["best_title", "best_source", "reason", "steps", "ranked", "caveats"],
    "additionalProperties": False,
}

TASK = """\
You compare ways to pay at an Israeli store and pick the best one for a shopper standing at
the checkout. Rules:
- The user's own unexpired vouchers are money already spent: recommend using them first
  (sooner expiry first), unless the store clearly doesn't accept that card network.
- Otherwise rank by out-of-pocket cost per ₪100 of goods, then by confidence and how fast the
  user can act right now (an instant app purchase beats ordering a physical card).
- Flag exclusions (sale items, specific branches, online-only) and anything unverified.
- Be brief: titles under 70 characters, reason in one or two sentences, at most 4 steps.
- Use only facts in the input. Never tell the user you purchased anything."""


async def llm_recommendation(
    store: NormalizedStore, results: list[SourceResult], cost_out: list[float] | None = None
) -> Recommendation:
    s = get_settings()
    payload = {
        "store": store.canonical,
        "my_vouchers": [v.for_llm() for r in results if r.source == "local" for v in r.vouchers],
        "sources": [
            {
                "source": r.source,
                "label": r.label,
                "status": r.status,
                "note": r.note,
                "accepted_networks": r.extra.get("accepted_networks"),
                "offers": [
                    o.model_dump(exclude={"voucher_id"}, exclude_none=True) for o in r.offers
                ],
            }
            for r in results
            if r.source != "local"
        ],
    }
    data = await llm.structured(
        TASK,
        json.dumps(payload, ensure_ascii=False, default=str),
        _SCHEMA,
        model=s.synth_model,
        effort=s.synth_effort,
        fallbacks=True,
        cost_out=cost_out,
    )
    return Recommendation.model_validate({**data, "generated_by": "llm"})
