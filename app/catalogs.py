"""Official merchant lists of gift-card networks ("where can I use this card?").

Some networks publish the full list of businesses that accept their card. couponAI downloads
those lists once per calendar month (Asia/Jerusalem) and keeps them in the cache db, so
checking "does Selas take BuyMe ALL?" is instant, exact and free (no AI involved).

- Configure per network in data/card_networks.yaml:  catalog: {provider: buyme, id: 13438757}
- Only networks you actually hold coupons for are downloaded.
- Read-only: plain GET of a public page, honest User-Agent, once a month per network.
  If a download fails, the previous month's list keeps being used.
"""

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

import httpx
from rapidfuzz import fuzz, process

from app import costs
from app.agents.normalizer import key
from app.cache import get_cache

log = logging.getLogger(__name__)

USER_AGENT = "couponAI/0.1 (personal use; fetches once a month)"
FOREVER = 10**9
REFRESH_EVERY_S = 6 * 3600  # the loop checks this often; downloads only when the month changed


# ---------- providers ----------


def _terms(raw) -> list[str]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str) and raw.strip().startswith("["):
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            items = re.findall(r'"([^"]+)"', raw)
    elif isinstance(raw, str):
        items = re.split(r"[,;|]", raw)
    else:
        items = []
    return [str(t).strip() for t in items if str(t).strip()]


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        h = urlparse(url if "://" in url else "https://" + url).hostname or ""
    except ValueError:
        return None
    return h.removeprefix("www.") or None


def _with_parts(terms: list[str]) -> list[str]:
    """Also index the pieces of phrases like "מסעדת selas - סלאס" (-> "selas", "סלאס")."""
    out = list(terms)
    for t in terms:
        parts = [x.strip() for x in re.split(r"\s[-–|/]\s|,", t) if x.strip()]
        if len(parts) > 1:
            out += parts
    return list(dict.fromkeys(out))


def parse_buyme(data: dict) -> dict:
    entries = []
    for b in data.get("brands") or []:
        title = (b.get("title") or "").strip()
        if not title:
            continue
        site = b.get("siteWeb") or b.get("siteLink")
        entries.append(
            {
                "id": str(b.get("id")),
                "title": title,
                "terms": _with_parts(_terms(b.get("searchTerms"))),
                "online": str(b.get("online_redeem")).lower() == "true",
                "site": site.split("?")[0] if isinstance(site, str) else None,
                "categories": sorted(
                    {
                        c["name"]
                        for c in b.get("categories_on_brands") or []
                        if isinstance(c, dict) and c.get("name")
                    }
                ),
            }
        )
    sup = data.get("supplier") or {}
    return {"supplier": sup.get("name"), "entries": entries}


async def fetch_buyme(network_id: str, client: httpx.AsyncClient) -> dict:
    url = f"https://buyme.co.il/brands/{network_id}/options"  # allowed by buyme robots.txt
    r = await client.get(url)
    r.raise_for_status()
    parsed = parse_buyme(r.json())
    if not parsed["entries"]:
        raise ValueError("empty merchant list")
    return {**parsed, "source_url": f"https://buyme.co.il/brands/{network_id}"}


PROVIDERS = {"buyme": fetch_buyme}


# ---------- storage ----------


def _cache_key(network: str, month: str) -> str:
    return f"catalog:{month}:{key(network)}"


def stored(network: str) -> dict | None:
    """This month's list, else the most recent one we have (marked stale)."""
    c = get_cache()
    hit = c.get(_cache_key(network, costs.month_key()), ttl_s=FOREVER)
    if hit:
        return {**hit, "stale": False}
    last = c.get(f"catalog:latest:{key(network)}", ttl_s=FOREVER)
    return {**last, "stale": True} if last else None


def _save(network: str, data: dict) -> None:
    c = get_cache()
    c.set(_cache_key(network, costs.month_key()), data)
    c.set(f"catalog:latest:{key(network)}", data)


# ---------- matching ----------


@dataclass
class CatalogIndex:
    network: str
    entries: list[dict]
    by_title: dict[str, dict] = field(default_factory=dict)
    by_term: dict[str, dict] = field(default_factory=dict)
    by_host: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        # A search term counts only if it names ONE business. Terms shared by several
        # (e.g. a company group like "פוקס" on Fox, Foot Locker and Mango) are ignored.
        counts = Counter(k for e in self.entries for k in {key(t) for t in e["terms"]})
        for e in self.entries:
            self.by_title.setdefault(key(e["title"]), e)
            for t in e["terms"]:
                k = key(t)
                if len(k) >= 3 and counts[k] == 1:
                    self.by_term.setdefault(k, e)
            if h := _host(e.get("site")):
                self.by_host.setdefault(h, e)

    def find(self, names: list[str], domain: str | None = None) -> dict | None:
        keys = [k for k in (key(n) for n in names) if k]
        for k in keys:
            if k in self.by_title:
                return self.by_title[k]
        for k in keys:
            if k in self.by_term:
                return self.by_term[k]
        if domain and (h := _host(domain)) and h in self.by_host:
            return self.by_host[h]
        for k in keys:  # close spelling of the business name itself
            if len(k) >= 4:
                hit = process.extractOne(k, list(self.by_title), scorer=fuzz.ratio, score_cutoff=92)
                if hit:
                    return self.by_title[hit[0]]
        return None


_index_cache: dict[tuple[str, str], CatalogIndex] = {}


def index_for(network: str) -> CatalogIndex | None:
    data = stored(network)
    if not data:
        return None
    ck = (key(network), str(data.get("fetched_at")))
    if ck not in _index_cache:
        if len(_index_cache) > 32:
            _index_cache.clear()
        _index_cache[ck] = CatalogIndex(network, data["entries"])
    return _index_cache[ck]


def catalog_config(network: str | None) -> dict | None:
    from app.agents.local_voucher import network_entry

    net = network_entry(network)
    cfg = (net or {}).get("catalog")
    return cfg if isinstance(cfg, dict) and cfg.get("provider") in PROVIDERS else None


def lookup(network: str | None, names: list[str], domain: str | None = None):
    """(has_list, entry). has_list False = this network has no downloaded list."""
    if not network or not catalog_config(network):
        return False, None
    from app.agents.local_voucher import network_entry

    idx = index_for(network_entry(network)["name"])
    if idx is None:
        return False, None
    return True, idx.find(names, domain)


def all_indexes() -> list[CatalogIndex]:
    from app.agents.local_voucher import card_networks

    out = []
    for n in card_networks():
        if catalog_config(n["name"]) and (idx := index_for(n["name"])):
            out.append(idx)
    return out


# ---------- monthly refresh ----------


def networks_to_fetch() -> list[dict]:
    """Networks with a catalog that you hold usable coupons for."""
    from app.agents.local_voucher import held_networks, network_entry

    out, seen = [], set()
    for name in held_networks(date.today()):
        net = network_entry(name)
        if net and catalog_config(net["name"]) and net["name"] not in seen:
            seen.add(net["name"])
            out.append(net)
    return out


async def refresh_due(client: httpx.AsyncClient | None = None) -> list[str]:
    """Download this month's list for every held network that doesn't have it yet."""

    def has_this_month(name: str) -> bool:
        d = stored(name)
        return bool(d) and not d["stale"]

    due = [n for n in networks_to_fetch() if not has_this_month(n["name"])]
    if not due:
        return []
    done = []
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        for n in due:
            cfg = n["catalog"]
            try:
                data = await PROVIDERS[cfg["provider"]](str(cfg["id"]), client)
                data["fetched_at"] = costs.now().isoformat(timespec="minutes")
                data["month"] = costs.month_key()
                _save(n["name"], data)
                done.append(n["name"])
                log.info("catalog %s: %d places", n["name"], len(data["entries"]))
            except Exception as e:  # noqa: BLE001 - keep last month's list
                log.warning("catalog %s refresh failed: %s", n["name"], e)
    finally:
        if own:
            await client.aclose()
    return done


async def refresh_loop() -> None:
    while True:
        try:
            await refresh_due()
        except Exception:  # noqa: BLE001
            log.exception("catalog refresh loop")
        await asyncio.sleep(REFRESH_EVERY_S)


def status() -> list[dict]:
    from app.agents.local_voucher import card_networks

    out = []
    for n in card_networks():
        if not catalog_config(n["name"]):
            continue
        d = stored(n["name"])
        out.append(
            {
                "network": n["name"],
                "places": len(d["entries"]) if d else 0,
                "fetched_at": d.get("fetched_at") if d else None,
                "stale": d.get("stale") if d else None,
                "source_url": d.get("source_url") if d else None,
            }
        )
    return out
