# couponAI

Checkout-time voucher & club-discount finder for Israel. Given a store name (Hebrew or English),
it checks local voucher spreadsheets and public club/store pages **in parallel**, then recommends
the best way to pay. Built for speed: the user is standing at a checkout counter.

## 🚫 Hard safety rules (never violate)

1. **Read-only.** The app and its agents NEVER purchase vouchers, redeem codes, submit payment,
   fill checkout forms, or perform any transaction. They only retrieve, compare, and tell the user
   *where* to buy or use something.
2. **No write-capable tools for agents.** Agents may use only: Claude `web_search` / `web_fetch`
   (GET-only) and the local read-only voucher reader. Do not add tools that POST, click, log in,
   or write files without the user's explicit approval in this chat.
3. **Every agent system prompt starts with `guardrails.READ_ONLY_PREAMBLE`.** Don't remove or weaken it.
4. **Never write to `data/vouchers/`.** Open those Excel/CSV files read-only. Coupons the user
   adds in the app go ONLY to `data/coupons.sqlite` via `app/coupon_store.py` (gitignored).
   Only `scripts/make_sample_vouchers.py` may generate the *sample* xlsx, and only when asked.
5. **Authenticated club portals (SMS OTP)** go only through `app/connectors/` behind the
   read-only `ClubConnector` interface. The v1 connector is a mock. A future Playwright connector
   must block non-GET requests except the explicit OTP login flow, and must never store card numbers.
6. **Secrets & personal data:** `ANTHROPIC_API_KEY` lives in `.env` (gitignored). Real voucher
   codes stay local. Never send voucher codes or balances to the LLM or to web searches. Only
   store names, issuers, and card networks may leave the machine. **One exception:** the
   "Fill from screenshot" feature sends the image the user picked to Claude, only when the user
   explicitly taps it, and nothing is saved until the user confirms the form. Images are not stored.

7. **Ask before paying.** A normal search (`mode=ask`) must never make a paid API call: no web
   search, no LLM store-name normalization, no LLM comparison. Paid work runs only after the user
   taps "Search online" (`mode=run`) or "Search again" (`mode=refresh`). Paid online results are
   saved per calendar month (Asia/Jerusalem) in `data/cache.sqlite`; every API call's cost is
   added to the monthly total (`app/costs.py`). Keep `PRICES` in `app/costs.py` current.

8. **Public repo: no personal data in git.** Never commit `.env`, `data/*.sqlite*`, real
   voucher files, real names, emails, coupon codes, or the server URL. Only the dummy sample xlsx
   is tracked. Real coupons live only on the server volume (`/data`).
9. **Login is mandatory on the server.** The Docker image sets `COUPONAI_REQUIRE_LOGIN=true`
   and refuses to start without `COUPONAI_USERS` + `COUPONAI_SECRET_KEY`. Every `/api/*` route
   except `/api/login` and `/api/health` requires a session (see `app/auth.py`). Don't weaken this.

10. **Outside data fetches are read-only and polite.** The only non-AI outside requests are the
    gift-card merchant lists (`app/catalogs.py`): plain GET of public pages allowed by the site's
    robots.txt, honest User-Agent, at most once per network per calendar month, only for networks
    we hold coupons for. Never send user data to these sites. If a list fails, keep last month's.

## Stack

- Python 3.12, FastAPI + uvicorn, asyncio fan-out with per-worker timeouts, SSE streaming
- `anthropic` SDK (AsyncAnthropic). Workers: `claude-haiku-5-5`. Synthesizer: `claude-opus-5-5`
  (adaptive thinking, effort `low`, `fallbacks: "default"`). Online research uses the server tools
  `web_search_20260209` / `web_fetch_20260209`.
- pandas + openpyxl for `.xlsx` / `.csv`; rapidfuzz for store-name matching
- Frontend: `web/index.html` + `web/app.js`. Tailwind + Alpine.js from pinned CDN versions,
  no build step (local Node is v8, so don't add a JS toolchain).

## Layout

- `app/agents/`: orchestrator, normalizer, local_voucher, web_research, club_research, synthesizer
- `app/connectors/`: `ClubConnector` protocol, public-web connector, mock authenticated connector
- `app/guardrails.py`: read-only preamble, tool allowlist
- `data/vouchers/`: the user's `.xlsx` / `.csv` files (sample: `my_vouchers.xlsx`)
- `data/store_aliases.yaml`, `data/card_networks.yaml`: normalization & card-network seeds
- `data/clubs.yaml`: which clubs to check and which connector each uses
- `app/catalogs.py`: official merchant lists of gift-card networks (BuyMe…), refreshed monthly
- `app/coupon_store.py`: app-added coupons (SQLite); `app/agents/coupon_reader.py`: screenshot → form draft
- Haiku 5.5 workers use the basic web tools (`web_search_20250305` / `web_fetch_20250910`);
  the `*_20260209` versions only work on Opus/Sonnet 4.6+ (configurable in `app/config.py`)

## Deployment

- GitHub (public): code only. Railway builds the `Dockerfile` on every push to `main`.
- Railway volume at `/data` = `COUPONAI_STATE_DIR` (coupon db + saved results).
- Server variables: `ANTHROPIC_API_KEY`, `COUPONAI_USERS`, `COUPONAI_SECRET_KEY`
  (make them with `python scripts/hash_password.py`), optional `COUPONAI_API_TOKEN`.
- Local dev has no login and uses `data/` (dummy sample xlsx). It's a separate database
  from the server's.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                        # then set ANTHROPIC_API_KEY
python scripts/make_sample_vouchers.py      # regenerate the sample xlsx
uvicorn app.main:app --reload               # http://127.0.0.1:8000
pytest -q                                   # tests; LLM calls are mocked
ruff check . && ruff format .
```

## Conventions

- Pydantic models in `app/models.py` are the contract between agents, API, and UI.
  Every source returns `list[Offer]`.
- Each source runs as its own asyncio task with a hard timeout. One failing or slow source must
  never block or break the stream. Emit an error/timeout card instead.
- The local voucher result must stream first (it needs no network).
- Tests must not hit the network or the real API. Mock `app/llm.py`.
- Live API smoke runs cost money, so ask before running one.

## Voucher spreadsheet schema

`voucher_id, issuer, card_network, valid_stores, code, face_value, paid_amount, remaining_balance,
currency, purchase_date, expiry_date, status, purchased_from, notes`

- `valid_stores`: `;`-separated store names (Hebrew or English). Empty means "see `card_network`".
- `card_network`: multi-store card (e.g. `BuyMe ALL`, `Max Gift Card`), resolved via
  `data/card_networks.yaml` + online check.
- `status`: `unused` | `partial` | `used`.
