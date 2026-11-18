"""Connector registry built from data/clubs.yaml."""

from pathlib import Path

import yaml

from app.config import get_settings
from app.connectors.base import ClubConnector
from app.connectors.mock_authenticated import MockAuthenticatedConnector
from app.connectors.public_web import PublicWebConnector

KINDS = {"public_web": PublicWebConnector, "mock_authenticated": MockAuthenticatedConnector}


def load_clubs(path: Path | None = None) -> list[dict]:
    """Private list first: COUPONAI_CLUBS env -> data/clubs.local.yaml -> data/clubs.yaml."""
    s = get_settings()
    if path is None and s.clubs is not None:
        return s.clubs
    candidates = [path] if path else [s.data_dir / "clubs.local.yaml", s.data_dir / "clubs.yaml"]
    for p in candidates:
        if p.exists():
            return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("clubs") or []
    return []


def build_connectors(clubs: list[dict] | None = None) -> list[ClubConnector]:
    out = []
    for c in clubs if clubs is not None else load_clubs():
        if not c.get("enabled", True):
            continue
        kind = KINDS.get(c.get("connector", "public_web"))
        if kind is None:
            raise ValueError(f"unknown connector {c.get('connector')!r} for club {c.get('id')}")
        out.append(kind(c))
    return out
