"""App settings (env vars prefixed COUPONAI_, plus ANTHROPIC_API_KEY)."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COUPONAI_", env_file=ROOT / ".env", extra="ignore"
    )

    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    # Use the LLM even without ANTHROPIC_API_KEY (e.g. credentials from `ant auth login`).
    force_llm: bool = False
    # Turn all LLM/web calls off (tests, offline use).
    disable_llm: bool = False

    worker_model: str = "claude-haiku-5-5"
    synth_model: str = "claude-opus-5-5"
    # Haiku 5.5 uses the basic web tool versions; Opus/Sonnet 4.6+ could use *_20260209.
    web_search_tool: str = "web_search_20250305"
    web_fetch_tool: str = "web_fetch_20250910"
    worker_max_searches: int = 3  # each web search costs ~$0.01
    worker_max_fetches: int = 2
    worker_effort: str = "low"
    synth_effort: str = "low"

    worker_timeout_s: float = 25.0
    synth_timeout_s: float = 25.0
    normalize_llm_timeout_s: float = 5.0
    cache_ttl_s: int = 12 * 3600  # generic cache; online results are kept per calendar month
    # shown before a paid search: per-source cost cap estimate + final comparison
    est_tokens_usd_per_source: float = 0.01
    est_synth_usd: float = 0.03

    # data_dir: config shipped with the code (store aliases, card networks, clubs)
    data_dir: Path = ROOT / "data"
    # state_dir: private runtime data (coupon db, saved results). On the server: the volume.
    state_dir: Path = ROOT / "data"
    voucher_dir: Path = ROOT / "data" / "vouchers"  # read-only Excel/CSV files
    cache_path: Path | None = None  # default: state_dir / cache.sqlite
    coupon_db_path: Path | None = None  # default: state_dir / coupons.sqlite (coupons added in app)
    extract_model: str = "claude-opus-5-5"  # reads coupon screenshots; accuracy over speed

    # private club list as JSON (overrides data/clubs.local.yaml and data/clubs.yaml)
    clubs: list[dict] | None = None

    # download gift-card merchant lists monthly in the background (app/catalogs.py)
    catalog_refresh: bool = True

    # ---- login (see app/auth.py) ----
    # {"Name": "<hash from scripts/hash_password.py>", ...}. Empty = no login (local use only).
    users: dict[str, str] = Field(default_factory=dict)
    secret_key: str = ""  # signs login cookies; long random string
    api_token: str = ""  # optional Bearer token for scripts (acts as user "api")
    require_login: bool = False  # the Docker image sets this: refuse to start without users

    @model_validator(mode="after")
    def _derive_paths(self):
        if self.cache_path is None:
            self.cache_path = self.state_dir / "cache.sqlite"
        if self.coupon_db_path is None:
            self.coupon_db_path = self.state_dir / "coupons.sqlite"
        return self

    @property
    def auth_enabled(self) -> bool:
        return bool(self.users) or self.require_login

    @property
    def llm_enabled(self) -> bool:
        if self.disable_llm:
            return False
        return bool(
            self.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN") or self.force_llm
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
