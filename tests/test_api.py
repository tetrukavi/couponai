import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_index():
    h = client.get("/api/health").json()
    assert h["ok"] and h["voucher_count"] == 9 and h["llm_enabled"] is False
    assert "couponAI" in client.get("/").text
    assert "Foot Locker" in client.get("/api/stores").json()


def test_search_stream_sse():
    with client.stream("GET", "/api/search/stream", params={"q": "פוט לוקר"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
    assert events[0] == "normalized" and events[-1] == "done"
    assert "local" in events
    # ask mode: only the free "cards you could buy" source runs; nothing paid
    sources = [
        json.loads(line[6:])["source"]
        for line, prev in zip(body.splitlines()[1:], body.splitlines()[:-1], strict=True)
        if prev == "event: source"
    ]
    assert sources == ["cards"]
    assert "online" in events and '"status": "unavailable"' in body
