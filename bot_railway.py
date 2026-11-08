"""
Mahor -> YousefianAbShodeh Telegram publisher.
All executable bot code lives in this ONE bot.py file.
Secrets are read from environment variables (Railway Variables).
"""

from __future__ import annotations

# Railway Variables (no .env file needed):
# API_ID, API_HASH, STRING_SESSION
# MAHOR_PARTNER_KEY_ID, MAHOR_PARTNER_SECRET
# Optional: TARGET, MAHOR_API_BASE_URL, PRICE_FIELD, LABEL_PERSPECTIVE,
# MARKET_CODES, SEND_MODE, POLL_SECONDS, MIN_POST_SECONDS,
# DEBOUNCE_SECONDS, HTTP_TIMEOUT_SECONDS, STATE_FILE


import argparse
import asyncio
import logging
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

PRICE_PATH = "/v1/partner-api/prices"
WS_NAMESPACE = "/partner-price-feed"
EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class Settings:
    base_url: str
    key_id: str
    secret: str
    target: str
    market_codes: frozenset[str]
    price_field: str
    poll_seconds: float
    min_post_seconds: float
    debounce_seconds: float
    timeout_seconds: float
    state_file: Path
    send_mode: str
    label_perspective: str = "customer"


def env_float(name: str, default: str, minimum: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value < float("inf"):
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def read_settings() -> Settings:
    base_url = os.getenv("MAHOR_API_BASE_URL", "https://api.goldmahor.ir").strip().rstrip("/")
    if not base_url.startswith("https://") or "?" in base_url or "#" in base_url:
        raise ValueError("MAHOR_API_BASE_URL must be an HTTPS origin (without path/query)")
    key_id = os.getenv("MAHOR_PARTNER_KEY_ID", "").strip()
    secret = os.getenv("MAHOR_PARTNER_SECRET", "").strip()
    if not key_id or not secret:
        raise ValueError("Set MAHOR_PARTNER_KEY_ID and MAHOR_PARTNER_SECRET in Railway Variables")
    target = os.getenv("TARGET", "@YousefianAbShodeh").strip()
    if not target:
        raise ValueError("TARGET is empty")
    market_codes = frozenset(
        code.strip().upper()
        for code in os.getenv("MARKET_CODES", "").split(",")
        if code.strip()
    )
    price_field = os.getenv("PRICE_FIELD", "basePrice").strip()
    if price_field not in {"basePrice", "price"}:
        raise ValueError("PRICE_FIELD must be basePrice or price")
    send_mode = os.getenv("SEND_MODE", "new").lower().strip()
    if send_mode not in {"new", "edit"}:
        raise ValueError("SEND_MODE must be new or edit")
    label_perspective = os.getenv("LABEL_PERSPECTIVE", "customer").lower().strip()
    if label_perspective not in {"customer", "dealer"}:
        raise ValueError("LABEL_PERSPECTIVE must be customer or dealer")
    return Settings(
        base_url=base_url,
        key_id=key_id,
        secret=secret,
        target=target,
        market_codes=market_codes,
        price_field=price_field,
