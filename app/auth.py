"""Login for a small, fixed set of people (e.g. a household).

- Users come from the COUPONAI_USERS env var: {"Name": "<password hash>"}. No passwords in code.
- Password hashes: PBKDF2-SHA256 (stdlib), made with scripts/hash_password.py.
- Sessions: HMAC-signed cookie (HttpOnly, SameSite=Strict, Secure on HTTPS), 90 days.
  SameSite=Strict also stops other sites from triggering paid searches with your cookie.
- Optional Bearer API token for scripts (COUPONAI_API_TOKEN); acts as user "api".
- With no users configured, login is off and everyone is "me" (local development only).
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request

from app.config import get_settings

COOKIE = "couponai_session"
SESSION_DAYS = 90
ALGO, ITERATIONS = "pbkdf2_sha256", 600_000
LOCAL_USER = "me"
_DUMMY = f"{ALGO}${ITERATIONS}$00$00"


class ConfigError(RuntimeError):
    pass


# ---------- passwords ----------


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"{ALGO}${iterations}${salt}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt, digest = encoded.split("$")
        if algo != ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), digest)


# ---------- sessions ----------


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_session(user: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + SESSION_DAYS * 86400)
    payload = base64.urlsafe_b64encode(f"{user}|{exp}".encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(token: str | None, now: float | None = None) -> str | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        user, exp = base64.urlsafe_b64decode(payload.encode()).decode().rsplit("|", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    if int(exp) < (now or time.time()) or user not in get_settings().users:
        return None  # expired, or the user was removed
    return user


def token_fingerprint(token: str) -> str | None:
    """One-way fingerprint (8 hex of SHA-256): compare tokens without revealing them."""
    return hashlib.sha256(token.encode()).hexdigest()[:8] if token else None


# ---------- request -> user ----------


def check_config() -> None:
    s = get_settings()
    if s.require_login and not s.users:
        raise ConfigError("COUPONAI_REQUIRE_LOGIN is set but COUPONAI_USERS is empty")
    if s.users and len(s.secret_key) < 32:
        raise ConfigError("COUPONAI_SECRET_KEY must be set (32+ random characters)")


def user_for(request: Request) -> str | None:
    s = get_settings()
    if not s.auth_enabled:
        return LOCAL_USER
    auth = request.headers.get("authorization", "")
    if s.api_token and auth.startswith("Bearer "):
        return "api" if hmac.compare_digest(auth[7:], s.api_token) else None
    return read_session(request.cookies.get(COOKIE))


# ---------- login throttling (per client IP, in memory) ----------

_attempts: dict[str, deque[float]] = defaultdict(deque)
MAX_FAILS, WINDOW_S = 5, 15 * 60


def login_blocked(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    q = _attempts[ip]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    return len(q) >= MAX_FAILS


def record_failure(ip: str, now: float | None = None) -> None:
    _attempts[ip].append(now or time.time())


def check_login(name: str, password: str) -> str | None:
    """Returns the canonical user name, or None. Name match ignores case/spaces."""
    users = get_settings().users
    match = next((u for u in users if u.strip().lower() == name.strip().lower()), None)
    # always run one hash so timing doesn't reveal which names exist
    ok = verify_password(password, users[match] if match else _DUMMY)
    return match if ok and match else None
