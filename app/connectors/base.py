"""Club connector interface. Deliberately read-only: there is no buy/redeem/submit method."""

from typing import Protocol, runtime_checkable

from app.models import NormalizedStore, SourceResult


@runtime_checkable
class ClubConnector(Protocol):
    id: str
    name: str
    needs_llm: bool

    async def search_benefits(self, store: NormalizedStore) -> SourceResult:
        """Return offers usable at `store`. Must not perform any transaction."""
        ...


# Methods a connector must never expose. A test enforces this on every registered connector.
FORBIDDEN_METHODS = ("buy", "purchase", "order", "redeem", "checkout", "pay", "submit", "top_up")
