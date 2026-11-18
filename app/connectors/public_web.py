"""v1 connector: public benefit pages via the club research agent (web search, GET only)."""

from app.agents import club_research
from app.models import NormalizedStore, SourceResult


class PublicWebConnector:
    needs_llm = True

    def __init__(self, club: dict):
        self.club = club
        self.id = club["id"]
        self.name = club["name"]

    async def search_benefits(self, store: NormalizedStore) -> SourceResult:
        return await club_research.run(store, self.club)
