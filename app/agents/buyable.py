"""Cards you could buy: gift cards accepted at this store that you don't hold yet.

Free and instant: checks the store against every downloaded official list (app/catalogs.py).
Cards without a downloadable list are checked by the store-site AI search instead.
Read-only: it only tells you which card works here and where to buy it.
"""

from datetime import date

from app import catalogs
from app.agents.local_voucher import card_networks, held_networks, network_entry
from app.agents.normalizer import key
from app.models import NormalizedStore, Offer, SourceResult

SOURCE_ID, LABEL = "cards", "Cards you could buy"


def buy_offer(net: dict, source: str, place: dict | None = None, evidence: str | None = None):
    buy = net.get("buy") or {}
    disc = buy.get("discount_pct")
    terms = [t for t in (buy.get("note"), (place or {}).get("note")) if t]
    if place is not None and place.get("online") is not None:
        terms.append("can be used online" if place.get("online") else "in-store only")
    return Offer(
        source=source,
        kind="gift_card",
        title=f"{net['name']} works here"
        + (f" (as {place['title']})" if place and key(place["title"]) != key(net["name"]) else ""),
        discount_pct=disc,
        effective_cost_per_100=round(100 - disc, 2) if disc is not None else None,
        where=f"Buy at {buy['where']}" if buy.get("where") else None,
        url=buy.get("url"),
        terms="; ".join(terms) or None,
        confidence="high" if place is not None else "medium",
        evidence_url=evidence or (place or {}).get("site"),
    )


def unheld_networks() -> list[dict]:
    held = {key(n) for name in held_networks(date.today()) for n in [name]}
    held |= {key(network_entry(n)["name"]) for n in held_networks(date.today()) if network_entry(n)}
    return [n for n in card_networks() if key(n["name"]) not in held]


async def run(store: NormalizedStore) -> SourceResult:
    names = [store.canonical, *store.aliases, store.query]
    offers, checked = [], 0
    for net in unheld_networks():
        has_list, place = catalogs.lookup(net["name"], names, store.domain)
        if not has_list:
            continue
        checked += 1
        if place:
            offers.append(buy_offer(net, SOURCE_ID, place))
    offers.sort(key=lambda o: (o.effective_cost_per_100 is None, o.effective_cost_per_100 or 0))
    note = (
        f"Checked {checked} official card lists"
        if checked
        else "Card lists not downloaded yet (they load in the background)"
    )
    return SourceResult(
        source=SOURCE_ID,
        label=LABEL,
        status="ok" if offers else "empty",
        offers=offers,
        note=note,
        extra={"checked_lists": checked},
    )
