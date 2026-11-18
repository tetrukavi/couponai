# couponAI

A small, mobile-first web app for the moment you're at a store checkout in Israel:
type the store name (Hebrew or English) and see **which of your coupons and gift cards work
there**, plus what the store's own website says about accepted gift cards and current deals.

It's a personal project, built for a household (two people sharing one coupon wallet).

> 🔒 **Read-only by design.** couponAI never buys, redeems, tops up or pays for anything.
> It only retrieves, compares and tells you where to buy or use something.

## What it does

- **Your coupons, instantly.** Search your coupons for a store in milliseconds, including
  multi-store gift cards (BuyMe, Max, תו הזהב…) that the store accepts.
- **Add coupons in seconds:** a short form, or *fill from screenshot* (Claude reads a photo
  of the voucher; you check the fields before saving).
- **Online check, only when you approve it.** A web-search agent reads the store's site for
  accepted gift cards and current promotions. Before anything paid runs you see an estimate
  ("up to ~$0.07"); results are then saved for the rest of the calendar month, so repeat
  searches are free. Every API call's real cost is tracked.
- **Best option.** A recommendation that spends coupons you already own first (soonest expiry
  first), then the cheapest way to pay.
- **Household login:** each person has their own login; coupons show who added and who used them.

## How it works

```
browser (HTML + Tailwind + Alpine, no build step)
   │  Server-Sent Events: results stream in as each source finishes
   ▼
FastAPI ── orchestrator ──┬─ local coupons (SQLite + optional Excel/CSV, read-only)   free, ~ms
                          ├─ store web search agent (Claude + web_search/web_fetch)   paid, ~15 s
                          ├─ club agents (disabled for now; pluggable connectors)
                          └─ recommendation (rules instantly; Claude refines after a paid search)
```

- Code-driven workflow, not a free-running agent: Python decides the plan, parallelism and
  timeouts; Claude only does the web research and the final comparison.
- Guardrails: agents get retrieval-only tools (`web_search`, `web_fetch`), every system prompt
  starts with a read-only preamble, connectors expose no buy/redeem methods, and coupon codes are
  never sent to the model (except a screenshot you explicitly choose to have read).
- Models: `claude-haiku-5-5` for web research, `claude-opus-5-5` for the comparison and for
  reading screenshots.

## Run locally

Requires Python 3.12+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add ANTHROPIC_API_KEY to enable online search
uvicorn app.main:app --reload # http://127.0.0.1:8000
pytest -q
```

Locally there's no login, and the app shows the dummy coupons in
`data/vouchers/my_vouchers.xlsx` so you can try it right away.

## Deploy (Railway)

The repo includes a `Dockerfile` and `railway.json`.

1. Create a Railway project from this GitHub repo.
2. Add a **volume** mounted at `/data`. It holds the coupon database and saved search results.
3. Make the login settings: `python scripts/hash_password.py` (asks for names + passwords and
   prints `COUPONAI_USERS` and `COUPONAI_SECRET_KEY`).
4. Set the service variables: `ANTHROPIC_API_KEY`, `COUPONAI_USERS`, `COUPONAI_SECRET_KEY`.
5. Generate a public domain under Networking. Pushing to `main` redeploys.

The image refuses to start without users configured, never includes the sample Excel file,
and keeps all private data on the volume.

## Configuration

| File | Purpose |
|---|---|
| `data/store_aliases.yaml` | store names in Hebrew/English for matching |
| `data/card_networks.yaml` | which stores multi-store gift cards cover (seed data, checked online) |
| `data/clubs.yaml` | loyalty clubs to search (`enabled: true/false`) |
| `.env` / service variables | API key, login, models, timeouts (see `app/config.py`) |

## License

MIT
