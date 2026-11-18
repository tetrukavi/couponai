import pytest

from app.agents.normalizer import key, normalize


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
