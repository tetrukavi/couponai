import pytest

from app.agents.normalizer import key, normalize
from app.config import get_settings


@pytest.mark.parametrize(
    "q,expected,method",
    [
        ("Foot Locker", "Foot Locker", "exact"),
        ("פוטלוקר", "Foot Locker", "exact"),
        ("foot-locker", "Foot Locker", "exact"),
        ("Foot Lockr", "Foot Locker", "fuzzy"),
        ("סופר פארם", "Super-Pharm", "exact"),
        ("ksp.co.il", "KSP", "exact"),
        ("Totally Unknown Brand", "Totally Unknown Brand", "raw"),
    ],
)
async def test_normalize(q, expected, method):
    s = await normalize(q)
    assert (s.canonical, s.method) == (expected, method)


def test_key_hebrew_final_letters():
    assert key("סופר-פארם") == key("סופר פארמ")


# ---- guard against the model mapping a name onto a different known store ----


def test_plausible_alias():
    from app.agents.normalizer import plausible_alias

    castro = ["Castro", "קסטרו", "castro.com"]
    assert not plausible_alias("Selas", castro)
    assert not plausible_alias("סלס", castro)
    assert plausible_alias("Castro Israel", castro)
    assert plausible_alias("קסטרו", castro)
    assert plausible_alias("Kastro", castro)


async def test_llm_mapping_to_a_different_known_store_is_rejected(monkeypatch):
    from app.agents import normalizer
    from app.cache import get_cache

    async def fake_llm(*a, **k):
        return {"canonical": "Castro", "aliases": ["Castro", "Selas", "סלס", "קסטרו"],
                "domain": "castro.com", "category": "fashion", "is_store": True}  # fmt: skip

    monkeypatch.setenv("COUPONAI_DISABLE_LLM", "0")
    monkeypatch.setenv("COUPONAI_FORCE_LLM", "1")
    get_settings.cache_clear()
    from app import llm

    monkeypatch.setattr(llm, "structured", fake_llm)
    s = await normalizer.normalize("Selas")
    assert (s.canonical, s.method) == ("Selas", "raw")
    assert not get_cache().items_with_prefix("alias:")  # nothing learned


async def test_bad_learned_alias_is_ignored():
    """Heals data learned before the guard existed (e.g. on the server)."""
    from app.cache import get_cache

    get_cache().set("alias:castro", {"canonical": "Castro", "aliases": ["Castro", "Selas", "סלס"],
                                     "domain": "castro.com", "category": "fashion"})  # fmt: skip
    s = await normalize("Selas", allow_llm=False)
    assert s.canonical == "Selas" and s.method == "raw"
    assert (await normalize("קסטרו", allow_llm=False)).canonical == "Castro"


async def test_llm_learns_a_genuinely_new_store(monkeypatch):
    from app import llm
    from app.cache import get_cache

    async def fake_llm(*a, **k):
        return {"canonical": "Selas", "aliases": ["סלאס"], "domain": "", "category": "shoes",
                "is_store": True}  # fmt: skip

    monkeypatch.setenv("COUPONAI_DISABLE_LLM", "0")
    monkeypatch.setenv("COUPONAI_FORCE_LLM", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "structured", fake_llm)
    s = await normalize("סלאס")
    assert (s.canonical, s.method) == ("Selas", "llm")
    assert get_cache().items_with_prefix("alias:")
    assert (await normalize("סלאס", allow_llm=False)).canonical == "Selas"  # remembered
