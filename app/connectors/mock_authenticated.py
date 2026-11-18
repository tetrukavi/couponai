"""Placeholder for logged-in club portals (SMS OTP).

Planned real implementation: a persistent Playwright browser profile the user logs into once
(OTP typed by the user), then read-only page reads. Hard requirements for that version:
- route-level block of every non-GET request except the explicit login/OTP endpoints;
- never click buy/checkout/redeem; never read or store card numbers;
- session files stay local (gitignored).
"""

from app.models import NormalizedStore, SourceResult


class MockAuthenticatedConnector:
    needs_llm = False

    def __init__(self, club: dict):
        self.club = club
        self.id = club["id"]
        self.name = club["name"]

    async def search_benefits(self, store: NormalizedStore) -> SourceResult:
        return SourceResult(
            source=f"club:{self.id}",
            label=self.name,
            status="mock",
            note="Logged-in portal not connected yet (mock connector)",
        )
