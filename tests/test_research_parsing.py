from app.agents.research_common import parse_offers
from app.llm import extract_json


def test_extract_json_from_fenced_block():
    text = 'Here you go:\n```json\n{"summary": "s", "offers": []}\n```'
    assert extract_json(text) == {"summary": "s", "offers": []}


def test_parse_offers_is_lenient():
    data = {
        "offers": [
            {"title": "15% off", "discount_pct": "15%", "kind": "weird", "confidence": "??"},
            {"no_title": True},
            "garbage",
        ]
    }
    [o] = parse_offers(data, "club:x")
    assert o.discount_pct == 15 and o.effective_cost_per_100 == 85
    assert o.kind == "other" and o.confidence == "low" and o.source == "club:x"
