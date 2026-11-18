import json
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings

SECRET = "x" * 40


@pytest.fixture
def login_on(monkeypatch):
    users = {"Dana": auth.hash_password("dana-secret-1", 1000),
             "Avi": auth.hash_password("avi-secret-12", 1000)}  # fmt: skip
    monkeypatch.setenv("COUPONAI_USERS", json.dumps(users))
    monkeypatch.setenv("COUPONAI_SECRET_KEY", SECRET)
    get_settings.cache_clear()
    auth._attempts.clear()
    from app.main import app

    return TestClient(app)


def log_in(c, name="Dana", pw="dana-secret-1"):
    return c.post("/api/login", json={"name": name, "password": pw})


def test_password_hashing():
    h = auth.hash_password("correct horse", 1000)
    assert auth.verify_password("correct horse", h)
    assert not auth.verify_password("wrong horse", h)
    assert not auth.verify_password("x", "garbage")


def test_everything_private_without_login(login_on):
    c = login_on
    for path in ("/api/coupons", "/api/stores", "/api/search/stream?q=Nike",
                 "/api/coupons/export.csv"):  # fmt: skip
        assert c.get(path).status_code == 401, path
    assert c.post("/api/coupons", json={"valid_stores": ["Nike"]}).status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/login").status_code == 200
    assert c.get("/api/health").json() == {"ok": True}  # reveals nothing


def test_login_flow_and_names(login_on):
    c = login_on
    assert log_in(c, "dana", "dana-secret-1").json() == {"user": "Dana"}  # case-insensitive
    cookie = c.cookies.get(auth.COOKIE)
    assert cookie
    h = c.get("/api/health").json()
    assert h["user"] == "Dana" and h["auth_enabled"]

    cid = c.post("/api/coupons", json={"valid_stores": ["Nike"], "face_value": 100}).json()
    assert cid["added_by"] == "Dana"
    c.post("/api/logout")
    c.cookies.clear()
    log_in(c, "Avi", "avi-secret-12")
    r = c.patch(f"/api/coupons/{cid['voucher_id']}", json={"remaining_balance": 20}).json()
    assert r["added_by"] == "Dana" and r["used_by"] == "Avi"


def test_wrong_password_and_lockout(login_on):
    c = login_on
    for _ in range(auth.MAX_FAILS):
        assert log_in(c, pw="nope").status_code == 401
    assert log_in(c).status_code == 429  # even the right password, while locked
    assert log_in(c, name="Nobody").status_code == 429


def test_forged_and_expired_sessions(login_on):
    good = auth.make_session("Dana")
    assert auth.read_session(good) == "Dana"
    payload, sig = good.rsplit(".", 1)
    assert auth.read_session(payload + "." + "0" * len(sig)) is None
    assert auth.read_session(auth.make_session("Mallory")) is None  # not a configured user
    old = auth.make_session("Dana", now=time.time() - (auth.SESSION_DAYS + 1) * 86400)
    assert auth.read_session(old) is None


def test_cookie_flags(login_on):
    r = log_in(login_on)
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc


def test_api_token(login_on, monkeypatch):
    monkeypatch.setenv("COUPONAI_API_TOKEN", "t" * 40)
    get_settings.cache_clear()
    c = login_on
    assert c.get("/api/coupons", headers={"Authorization": "Bearer " + "t" * 40}).status_code == 200
    assert c.get("/api/coupons", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_server_refuses_to_start_misconfigured(monkeypatch):
    monkeypatch.setenv("COUPONAI_REQUIRE_LOGIN", "true")
    get_settings.cache_clear()
    with pytest.raises(auth.ConfigError):
        auth.check_config()
    monkeypatch.setenv("COUPONAI_USERS", json.dumps({"A": "h"}))
    monkeypatch.setenv("COUPONAI_SECRET_KEY", "short")
    get_settings.cache_clear()
    with pytest.raises(auth.ConfigError):
        auth.check_config()
