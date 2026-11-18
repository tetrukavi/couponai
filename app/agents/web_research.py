"""Web Research Agent (store side): which gift cards / clubs does the store accept, any promos."""

from app import llm
from app.agents.research_common import output_format, parse_offers
from app.models import NormalizedStore, SourceResult

SOURCE_ID, LABEL = "store_site", "Store website"

TASK = (
    "You check an Israeli store's own website and reliable public sources to find which "
    "gift cards, voucher networks and credit-card/loyalty clubs the store accepts, and any "
    "current store-wide promotions."
)

EXTRA = """,
  "accepted_networks": [
    {"name": "BuyMe ALL", "accepted": "yes | no | unknown", "evidence_url": "https://..."}
  ]"""


async def run(
    store: NormalizedStore, held_networks: list[str], buyable: list[dict] | None = None
) -> SourceResult:
    names = ", ".join([store.canonical, *store.aliases])
    hint = f" (website hint: {store.domain})" if store.domain else ""
    held = ", ".join(held_networks) or "none"
    buyable = buyable or []
    to_buy = ", ".join(n["name"] for n in buyable) or "none"
    prompt = (
        f"Store: {names}{hint}, in Israel.\n"
        f"1. Which gift cards / voucher networks does this store accept? Explicitly check each of "
        f"these, which the user owns: {held}.\n"
        f"   Also check these popular gift cards the user could buy: {to_buy}.\n"
        f"   In accepted_networks, use exactly these names (one entry each).\n"
        f"2. Any current promotions or coupon codes on the store's own site?\n\n"
        f"{output_format(EXTRA)}"
    )
    text, urls, usd = await llm.research(TASK, prompt)
    data = llm.extract_json(text)
    offers = parse_offers(data, SOURCE_ID)
    accepted = [n for n in data.get("accepted_networks") or [] if isinstance(n, dict)]
    # cards the user doesn't hold but the store accepts -> "you could buy it" offers
    from app.agents.buyable import buy_offer
    from app.agents.normalizer import key

    by_key = {key(n["name"]): n for n in buyable}
    for a in accepted:
        net = by_key.get(key(str(a.get("name", ""))))
        if net and str(a.get("accepted", "")).lower() == "yes":
            offers.append(buy_offer(net, SOURCE_ID, evidence=a.get("evidence_url")))
    return SourceResult(
        source=SOURCE_ID,
        label=LABEL,
        status="ok" if offers or accepted else "empty",
        offers=offers,
        note=data.get("summary"),
        cost_usd=usd,
        extra={"accepted_networks": accepted, "searched_urls": urls[:8]},
    )
