"""Tiny TTL key/value cache: in-memory front, sqlite behind (survives restarts)."""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class TTLCache:
    def __init__(self, path: Path | None, ttl_s: int):
        self.ttl_s = ttl_s
        self._mem: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, ts REAL, v TEXT)")

    def get(self, key: str, ttl_s: int | None = None) -> Any | None:
        ttl = self.ttl_s if ttl_s is None else ttl_s
        now = time.time()
        with self._lock:
            hit = self._mem.get(key)
            if hit is None and self._db is not None:
                row = self._db.execute("SELECT ts, v FROM kv WHERE k=?", (key,)).fetchone()
                if row:
                    hit = (row[0], json.loads(row[1]))
                    self._mem[key] = hit
        if hit and now - hit[0] < ttl:
            return hit[1]
        return None

    def set(self, key: str, value: Any) -> None:
        ts = time.time()
        with self._lock:
            self._mem[key] = (ts, value)
            if self._db is not None:
                self._db.execute(
                    "INSERT OR REPLACE INTO kv VALUES (?,?,?)",
                    (key, ts, json.dumps(value, ensure_ascii=False)),
                )
                self._db.commit()

    def items_with_prefix(self, prefix: str) -> dict[str, Any]:
        with self._lock:
            if self._db is None:
                return {
                    k[len(prefix) :]: v for k, (_, v) in self._mem.items() if k.startswith(prefix)
                }
            rows = self._db.execute(
                "SELECT k, v FROM kv WHERE k LIKE ?", (prefix + "%",)
            ).fetchall()
        return {k[len(prefix) :]: json.loads(v) for k, v in rows}


_cache: TTLCache | None = None


def get_cache() -> TTLCache:
    global _cache
    if _cache is None:
        from app.config import get_settings

        s = get_settings()
        _cache = TTLCache(s.cache_path, s.cache_ttl_s)
    return _cache


def set_cache(cache: TTLCache | None) -> None:
    global _cache
    _cache = cache
