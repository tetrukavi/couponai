"""Store-name normalization: alias table -> fuzzy match -> LLM fallback -> raw."""

import asyncio
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from app.cache import get_cache
from app.config import get_settings
from app.models import NormalizedStore

log = logging.getLogger(__name__)

_FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
_STRIP = re.compile(r"[\s\-_'\".,&()/]+")


def key(s: str) -> str:
    """Comparison key: case/space/punctuation-insensitive, Hebrew final letters unified."""
    s = unicodedata.normalize("NFKC", s).lower().translate(_FINALS)
    s = re.sub(r"^(www\.)|(\.co\.il|\.com|\.co)$", "", s.strip())
    return _STRIP.sub("", s)


class StoreIndex:
    def __init__(self, entries: list[dict]):
        self.entries = entries
        self.by_key: dict[str, dict] = {}
        for e in entries:
            for name in [e["canonical"], *e.get("aliases", []), e.get("domain") or ""]:
                if name:
                    self.by_key.setdefault(key(name), e)

    def to_store(self, query: str, e: dict, method: str) -> NormalizedStore:
        return NormalizedStore(
            query=query,
            canonical=e["canonical"],
            aliases=e.get("aliases", []),
            domain=e.get("domain"),
            category=e.get("category"),
            method=method,
        )

    def lookup(self, query: str) -> NormalizedStore | None:
        k = key(query)
        if not k:
            return None
        if k in self.by_key:
            return self.to_store(query, self.by_key[k], "exact")
        best = process.extractOne(k, list(self.by_key), scorer=fuzz.WRatio, score_cutoff=86)
        if best:
            return self.to_store(query, self.by_key[best[0]], "fuzzy")
        return None


@lru_cache
def _load(path: Path, mtime: float) -> StoreIndex:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StoreIndex(data.get("stores", []))


def store_index() -> StoreIndex:
    p = get_settings().data_dir / "store_aliases.yaml"
    learned = list(get_cache().items_with_prefix("alias:").values())
    base = _load(p, p.stat().st_mtime)
    if not learned:
        return base
    return StoreIndex(base.entries + learned)


_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "canonical": {"type": "string", "description": "Official store/brand name in English"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "domain": {"type": "string", "description": "Israeli website domain, or empty"},
        "category": {"type": "string"},
        "is_store": {"type": "boolean"},
    },
    "required": ["canonical", "aliases", "domain", "category", "is_store"],
    "additionalProperties": False,
}


async def _llm_normalize(query: str) -> NormalizedStore | None:
    from app import llm

    s = get_settings()
    data = await asyncio.wait_for(
        llm.structured(
            "Normalize an Israeli retail store / brand name the user typed (Hebrew or English).",
            f"User typed: {query!r}\nReturn the canonical English name, Hebrew and English "
            "spellings as aliases, the Israeli website domain if you know it, and a category.",
            _LLM_SCHEMA,
            model=s.worker_model,
            effort="low",
            max_tokens=1000,
        ),
        timeout=s.normalize_llm_timeout_s,
    )
    if not data.get("is_store") or not data.get("canonical"):
        return None
    entry = {
        "canonical": data["canonical"].strip(),
        "aliases": sorted({query.strip(), *[a.strip() for a in data["aliases"] if a.strip()]}),
        "domain": data.get("domain") or None,
        "category": data.get("category") or None,
    }
    get_cache().set(f"alias:{key(entry['canonical'])}", entry)  # learned alias
    return StoreIndex([entry]).to_store(query, entry, "llm")


async def normalize(query: str, *, allow_llm: bool = True) -> NormalizedStore:
    """allow_llm=False keeps this free (no API call) for searches the user hasn't paid for."""
    query = query.strip()
    hit = store_index().lookup(query)
    if hit:
        return hit
    if allow_llm and get_settings().llm_enabled:
        try:
            got = await _llm_normalize(query)
            if got:
                return got
        except Exception as e:  # noqa: BLE001 - normalization must never fail the search
            log.warning("LLM normalization failed for %r: %s", query, e)
    return NormalizedStore(query=query, canonical=query, method="raw")
