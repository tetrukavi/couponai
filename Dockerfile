# couponAI server image (used by Railway; works on any Docker host).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY web ./web
COPY scripts ./scripts
COPY data/store_aliases.yaml data/card_networks.yaml data/clubs.yaml ./data/
RUN pip install -e . && mkdir -p /data

# Private data lives on a mounted volume at /data (coupon db + saved search results).
# The sample Excel file is NOT in the image, and login is mandatory.
# Runs as root because Railway mounts volumes as root.
ENV COUPONAI_STATE_DIR=/data \
    COUPONAI_VOUCHER_DIR=/data/vouchers \
    COUPONAI_REQUIRE_LOGIN=true

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
