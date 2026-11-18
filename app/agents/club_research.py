"""Club Research Agent: one public-web lookup per loyalty club."""

from app import llm
from app.agents.research_common import output_format, parse_offers
from app.models import NormalizedStore, SourceResult

TASK = (
    "You check ONE Israeli loyalty / credit-card benefits club's publicly accessible benefit "
    "catalog for discounts, discounted vouchers or gift cards usable at a given store. "
    "Also count discounted multi-store gift cards (e.g. BuyMe, Max gift card) sold in the club "
    "if this store accepts them. You cannot log in; say so if the benefit is only visible "
    "to logged-in members."
)


async def run(store: NormalizedStore, club: dict) -> SourceResult:
    names = ", ".join([store.canonical, *store.aliases])
    prompt = (
        f"Club: {club['name']} (search hint: {club.get('search_hint', club['name'])})\n"
        f"Store: {names}, in Israel.\n"
        f"What does this club currently offer that saves money at this store?\n\n"
        f"{output_format()}"
    )
    source = f"club:{club['id']}"
    text, urls, usd = await llm.research(TASK, prompt)
    data = llm.extract_json(text)
    offers = parse_offers(data, source)
    return SourceResult(
        source=source,
        label=club["name"],
        status="ok" if offers else "empty",
        offers=offers,
        note=data.get("summary"),
        cost_usd=usd,
        extra={"searched_urls": urls[:8]},
    )
