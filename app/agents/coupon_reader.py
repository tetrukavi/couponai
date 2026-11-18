"""Reads a coupon screenshot/photo and drafts the "Add coupon" form. The user reviews it before
anything is saved. Triggered only by the user's explicit "Fill from screenshot" action."""

import base64
import binascii
from datetime import date

from app import llm
from app.agents.local_voucher import card_networks
from app.config import get_settings

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_coupon": {"type": "boolean"},
        "issuer": {"type": ["string", "null"]},
        "card_network": {"type": ["string", "null"]},
        "valid_stores": {"type": "array", "items": {"type": "string"}},
        "code": {"type": ["string", "null"]},
        "face_value": {"type": ["number", "null"]},
        "paid_amount": {"type": ["number", "null"]},
        "remaining_balance": {"type": ["number", "null"]},
        "currency": {"type": "string"},
        "purchase_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "expiry_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "purchased_from": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
        "link": {"type": ["string", "null"], "description": "redemption web address, if shown"},
        "unsure_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "is_coupon", "issuer", "card_network", "valid_stores", "code", "face_value",
        "paid_amount", "remaining_balance", "currency", "purchase_date", "expiry_date",
        "purchased_from", "notes", "link", "unsure_fields",
    ],
    "additionalProperties": False,
}  # fmt: skip

TASK = """\
You read a screenshot or photo of an Israeli voucher, gift card or coupon (often Hebrew: e.g.
BuyMe emails, club purchase confirmations, plastic gift cards) and extract its details.
- code: copy the voucher/card number or redemption code EXACTLY, character by character.
  If several numbers appear, prefer the one labelled code / קוד / מספר שובר / מספר כרטיס.
- card_network: only for multi-store cards (e.g. "BuyMe ALL", "Max Gift Card", "תו הזהב");
  null for a single-store voucher. valid_stores: the store(s) for a single-store voucher.
- face_value = value printed on the voucher; paid_amount only if a price paid is shown.
- Dates: ISO format. Israeli dates are day/month/year.
- link: a web address for using/redeeming it, only if clearly shown (copy it exactly).
- Never guess: use null for anything not visible and list doubtful fields in unsure_fields.
- If the image is not a voucher, set is_coupon=false."""


def decode_image(data_b64: str, media_type: str) -> str:
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"unsupported image type {media_type}; use JPEG, PNG or WebP")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except binascii.Error as e:
        raise ValueError("image is not valid base64") from e
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image is larger than 5 MB")
    return data_b64


async def extract(data_b64: str, media_type: str) -> dict:
    s = get_settings()
    data_b64 = decode_image(data_b64, media_type)
    known = ", ".join(n["name"] for n in card_networks())
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_b64}},
        {
            "type": "text",
            "text": f"Today is {date.today().isoformat()}. Known gift-card networks: {known}.\n"
            "Extract this voucher's details.",
        },
    ]
    return await llm.structured(
        TASK, content, _SCHEMA, model=s.extract_model, effort="low", max_tokens=4000,
        fallbacks=True,
    )  # fmt: skip
