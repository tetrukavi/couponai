"""FastAPI entry point: SSE search stream + static single-page UI."""

import asyncio
import base64
import binascii
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth, catalogs, costs, coupon_store
from app.agents import coupon_reader, local_voucher, orchestrator
from app.agents.local_voucher import card_networks, is_usable
from app.agents.normalizer import store_index
from app.config import ROOT, get_settings
from app.connectors import load_clubs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

auth.check_config()  # refuse to start a server that should have a login but doesn't

_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # gift-card merchant lists: download this month's on startup, then re-check every few hours
    if get_settings().catalog_refresh:
        _spawn(catalogs.refresh_loop())
    yield
    for t in list(_background):
        t.cancel()


app = FastAPI(title="couponAI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
WEB = ROOT / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")

PUBLIC_PATHS = {"/login", "/api/login", "/api/health"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    user = auth.user_for(request)
    request.state.user = user
    path = request.url.path
    if user or path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Please log in"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/login", include_in_schema=False)
async def login_page(request: Request):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return FileResponse(WEB / "login.html")


class LoginIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


@app.post("/api/login")
async def login(body: LoginIn, request: Request) -> JSONResponse:
    if not get_settings().auth_enabled:
        return JSONResponse({"user": auth.LOCAL_USER})
    ip = request.client.host if request.client else "?"
    if auth.login_blocked(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    user = auth.check_login(body.name, body.password)
    if not user:
        auth.record_failure(ip)
        raise HTTPException(401, "Wrong name or password")
    resp = JSONResponse({"user": user})
    resp.set_cookie(
        auth.COOKIE,
        auth.make_session(user),
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return resp


@app.post("/api/logout")
async def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    if not request.state.user:  # public: used by the host's health check, reveals nothing secret
        # one-way fingerprint so the owner can check the server got the right API token
        return JSONResponse(
            {"ok": True, "api_token": auth.token_fingerprint(get_settings().api_token)}
        )
    s = get_settings()
    vouchers = local_voucher.load_vouchers()
    return JSONResponse(
        {
            "ok": True,
            "llm_enabled": s.llm_enabled,
            "worker_model": s.worker_model,
            "synth_model": s.synth_model,
            "voucher_count": sum(is_usable(v, date.today()) for v in vouchers),
            "month": costs.month_key(),
            "month_total_usd": round(costs.month_total(), 4),
            "user": request.state.user,
            # quick-pick chips in the Add coupon form: your clubs + common sources
            "purchase_sources": [c["name"] for c in load_clubs()] + ["BuyMe", "Gift"],
            "auth_enabled": s.auth_enabled,
        }
    )


@app.get("/api/stores")
async def stores() -> JSONResponse:
    """Names for the search box autocomplete."""
    names: set[str] = set()
    for e in store_index().entries:
        names.add(e["canonical"])
        names.update(e.get("aliases", []))
    return JSONResponse(sorted(names, key=str.lower))


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.get("/api/search/stream")
async def search_stream(
    request: Request,
    q: str = Query(..., min_length=1, max_length=80),
    mode: Literal["ask", "run", "refresh"] = "ask",
) -> StreamingResponse:
    """mode=ask is free; run/refresh mean the user approved paying for online search."""

    async def gen():
        try:
            async for event, data in orchestrator.search(q, mode=mode):
                if await request.is_disconnected():
                    break
                yield _sse(event, data)
        except Exception as e:  # noqa: BLE001
            logging.exception("search failed")
            yield _sse("error", {"message": str(e)[:200]})
            yield _sse("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- My coupons ----------


@app.get("/api/coupons")
async def coupons() -> JSONResponse:
    """All coupons: app-added (editable) + Excel/CSV (read-only)."""
    today = date.today()
    out = []
    for v in local_voucher.load_vouchers():
        d = v.model_dump(mode="json")
        d["editable"] = v.source_file == "app"
        d["usable"] = is_usable(v, today)
        d["days_to_expiry"] = (v.expiry_date - today).days if v.expiry_date else None
        out.append(d)
    out.sort(
        key=lambda d: (
            not d["usable"],
            d["days_to_expiry"] if d["days_to_expiry"] is not None else 99999,
        )
    )
    return JSONResponse(out)


@app.get("/api/catalogs")
async def catalog_status() -> JSONResponse:
    """Which gift-card merchant lists are downloaded, how big, and when."""
    return JSONResponse(catalogs.status())


@app.get("/api/networks")
async def networks() -> JSONResponse:
    return JSONResponse([n["name"] for n in card_networks()])


@app.post("/api/coupons", status_code=201)
async def add_coupon(c: coupon_store.CouponIn, request: Request) -> JSONResponse:
    v = coupon_store.add_coupon(c, user=request.state.user)
    if v.card_network and get_settings().catalog_refresh:
        _spawn(catalogs.refresh_due())  # a new gift card: fetch its list now, not in 6 hours
    return JSONResponse(v.model_dump(mode="json"), status_code=201)


@app.patch("/api/coupons/{voucher_id}")
async def update_coupon(
    voucher_id: str, patch: coupon_store.CouponPatch, request: Request
) -> JSONResponse:
    try:
        v = coupon_store.update_coupon(voucher_id, patch, user=request.state.user)
    except KeyError:
        raise HTTPException(404, "Only coupons added in the app can be edited") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return JSONResponse(v.model_dump(mode="json"))


@app.delete("/api/coupons/{voucher_id}", status_code=204)
async def delete_coupon(voucher_id: str) -> Response:
    try:
        coupon_store.delete_coupon(voucher_id)
    except KeyError:
        raise HTTPException(404, "Only coupons added in the app can be deleted") from None
    return Response(status_code=204)


class ImageIn(BaseModel):
    media_type: str
    data: str = Field(max_length=8_000_000)  # base64


@app.put("/api/coupons/{voucher_id}/image")
async def set_coupon_image(voucher_id: str, img: ImageIn) -> JSONResponse:
    """Attach the coupon's photo (stored on the server only; never sent to the AI)."""
    try:
        data = base64.b64decode(img.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "the photo could not be read") from None
    try:
        v = coupon_store.set_image(voucher_id, data)
    except KeyError:
        raise HTTPException(404, "Photos can be added only to coupons added in the app") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return JSONResponse(v.model_dump(mode="json"))


@app.get("/api/coupons/{voucher_id}/image")
async def get_coupon_image(voucher_id: str) -> FileResponse:
    try:
        path, media_type = coupon_store.image_file(voucher_id)
    except KeyError:
        raise HTTPException(404, "No photo") from None
    # the URL carries the file name (?v=...), so a new photo gets a new URL
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=2592000",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.delete("/api/coupons/{voucher_id}/image")
async def delete_coupon_image(voucher_id: str) -> JSONResponse:
    try:
        v = coupon_store.remove_image(voucher_id)
    except KeyError:
        raise HTTPException(404, "Only coupons added in the app can be edited") from None
    return JSONResponse(v.model_dump(mode="json"))


@app.post("/api/coupons/extract")
async def extract_coupon(img: ImageIn) -> JSONResponse:
    """Draft coupon fields from a screenshot. Nothing is saved until the user confirms."""
    if not get_settings().llm_enabled:
        raise HTTPException(503, "Screenshot reading needs ANTHROPIC_API_KEY in .env")
    try:
        draft = await coupon_reader.extract(img.data, img.media_type)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except Exception as e:  # noqa: BLE001
        logging.exception("extract failed")
        raise HTTPException(502, f"Could not read the image: {type(e).__name__}") from None
    return JSONResponse(draft)


@app.get("/api/coupons/export.csv")
async def export_coupons() -> Response:
    """Backup of every coupon (e.g. to save into Google Drive)."""
    buf = io.StringIO()
    cols = ["voucher_id", "issuer", "card_network", "valid_stores", "code", "face_value",
            "paid_amount", "remaining_balance", "currency", "purchase_date", "expiry_date",
            "status", "purchased_from", "notes", "link", "source_file"]  # fmt: skip
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for v in local_voucher.load_vouchers():
        d = v.model_dump(mode="json")
        d["valid_stores"] = ";".join(v.valid_stores)
        w.writerow(d)
    return Response(
        "\ufeff" + buf.getvalue(),  # BOM so Excel shows Hebrew correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="coupons-{date.today()}.csv"'},
    )
