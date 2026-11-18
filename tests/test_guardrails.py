import inspect
import json

import pytest

from app import llm
from app.agents import club_research, local_voucher, synthesizer, web_research
from app.agents.normalizer import normalize
from app.connectors import KINDS, build_connectors
from app.connectors.base import FORBIDDEN_METHODS, ClubConnector
from app.guardrails import READ_ONLY_PREAMBLE, GuardrailViolation, check_tools, system_prompt


def test_only_retrieval_tools_allowed():
    tools = llm.web_tools()
    assert {t["name"] for t in tools} == {"web_search", "web_fetch"}
    for bad in (
        {"name": "buy_voucher", "input_schema": {}},
        {"type": "computer_20251124", "name": "computer"},
        {"type": "bash_20250124", "name": "bash"},
        {"type": "web_search_20250305", "name": "web_search", "input_schema": {}},
    ):
        with pytest.raises(GuardrailViolation):
            check_tools([bad])


def test_every_system_prompt_has_read_only_preamble():
    for task in (web_research.TASK, club_research.TASK, synthesizer.TASK):
        assert system_prompt(task).startswith(READ_ONLY_PREAMBLE)
    assert "NEVER purchase" in READ_ONLY_PREAMBLE


def test_connectors_have_no_transaction_methods():
    for kind in KINDS.values():
        members = {n.lower() for n, _ in inspect.getmembers(kind)}
        assert not any(f in m for m in members for f in FORBIDDEN_METHODS), kind
    for c in build_connectors(
        [
            {"id": "a", "name": "A", "connector": "mock_authenticated"},
            {"id": "b", "name": "B", "connector": "public_web"},
        ]
    ):
        assert isinstance(c, ClubConnector)


async def test_voucher_codes_never_reach_llm(monkeypatch):
    sent = {}

    async def fake_structured(task, prompt, schema, **kw):
        sent["prompt"] = prompt
        return {
            "best_title": "x",
            "best_source": "local",
            "reason": "r",
            "steps": [],
            "ranked": [],
            "caveats": [],
        }

    monkeypatch.setattr(llm, "structured", fake_structured)
    store = await normalize("Foot Locker")
    local = local_voucher.search(store)
    assert any(v.code for v in local.vouchers)
    await synthesizer.llm_recommendation(store, [local])
    for v in local.vouchers:
        assert v.code not in sent["prompt"]
    json.loads(sent["prompt"])  # valid JSON payload


async def test_mock_connector_is_read_only_placeholder():
    c = build_connectors([{"id": "p", "name": "Portal", "connector": "mock_authenticated"}])[0]
    res = await c.search_benefits(await normalize("Nike"))
    assert res.status == "mock" and res.offers == []
