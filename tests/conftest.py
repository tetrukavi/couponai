import pytest

from app import cache
from app.config import get_settings


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No network, no real API, fresh in-memory cache for every test."""
    monkeypatch.setenv("COUPONAI_DISABLE_LLM", "1")
    monkeypatch.setenv("COUPONAI_WORKER_TIMEOUT_S", "0.5")
    monkeypatch.setenv("COUPONAI_COUPON_DB_PATH", str(tmp_path / "coupons.sqlite"))
    get_settings.cache_clear()
    cache.set_cache(cache.TTLCache(None, ttl_s=3600))
    yield
    get_settings.cache_clear()
    cache.set_cache(None)
