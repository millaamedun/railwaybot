"""
Mahor -> YousefianAbShodeh Telegram publisher.
All executable bot code lives in this ONE bot.py file.
Secrets are read from environment variables (Railway Variables).
"""

from __future__ import annotations

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

GROUP_ORDER = ["coin_86", "coin_low_date", "melted_gold", "other"]
GROUP_TITLES = {
    "coin_86": "سکه‌های ۸۶",
    "coin_low_date": "سکه‌های تاریخ پایین",
    "melted_gold": "آبشده‌ها",
    "other": "سایر نرخ‌ها",
}


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


@dataclass(frozen=True)
class MarketRow:
    code: str
    title: str
    settlement_type: str
    buy: Decimal | None
    sell: Decimal | None
    category: str


@dataclass(frozen=True)
class Snapshot:
    messages: tuple[tuple[str, str], ...]  # (group_key, message_text)
    fingerprint: str
    visible_markets: int
    available_market_codes: tuple[str, ...]


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
        poll_seconds=env_float("POLL_SECONDS", "60", 5),
        min_post_seconds=env_float("MIN_POST_SECONDS", "10", 0),
        debounce_seconds=env_float("DEBOUNCE_SECONDS", "1", 0),
        timeout_seconds=env_float("HTTP_TIMEOUT_SECONDS", "8", 1),
        state_file=Path(os.getenv("STATE_FILE", "state.json")).expanduser(),
        send_mode=send_mode,
        label_perspective=label_perspective,
    )


def make_nonce() -> str:
    return secrets.token_urlsafe(24)


def sign_rest(key_id: str, secret: str, *, timestamp: int | None = None, nonce: str | None = None) -> dict[str, str]:
    timestamp_str = str(timestamp if timestamp is not None else int(time.time()))
    nonce_str = nonce or make_nonce()
    canonical = "\n".join(["GET", PRICE_PATH, timestamp_str, nonce_str, EMPTY_BODY_SHA256])
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "Accept": "application/json",
        "X-Mahor-Partner-Key": key_id,
        "X-Mahor-Timestamp": timestamp_str,
        "X-Mahor-Nonce": nonce_str,
        "X-Mahor-Content-SHA256": EMPTY_BODY_SHA256,
        "X-Mahor-Signature": signature,
    }


def sign_ws(key_id: str, secret: str, *, timestamp: int | None = None, nonce: str | None = None) -> dict[str, str]:
    timestamp_str = str(timestamp if timestamp is not None else int(time.time()))
    nonce_str = nonce or make_nonce()
    canonical = "\n".join(["WS_CONNECT", WS_NAMESPACE, timestamp_str, nonce_str])
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"keyId": key_id, "timestamp": timestamp_str, "nonce": nonce_str, "signature": signature}


def parse_positive_price(raw: Any) -> Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        price = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not price.is_finite() or price <= 0:
        return None
    return price


def format_price(price: Decimal) -> str:
    fixed = format(price, "f")
    if "." in fixed:
        fixed = fixed.rstrip("0").rstrip(".")
    left, dot, right = fixed.partition(".")
    return f"{int(left):,}" + (f".{right}" if dot else "")


def normalize_title_for_group(title: str) -> str:
    return " ".join(title.replace("ي", "ی").replace("ك", "ک").split())


def classify_group(row: MarketRow) -> str:
    title = normalize_title_for_group(row.title)
    if row.category == "MELTED_GOLD" or "آبشده" in title:
        return "melted_gold"
    if "تاریخ پایین" in title:
        return "coin_low_date"
    if "86" in title or "۸۶" in title or "سکه" in title:
        return "coin_86"
    return "other"


def settlement_sort_key(settlement_type: str) -> int:
    return {"SPOT": 0, "TOMORROW": 1, "DAY_AFTER_TOMORROW": 2}.get(settlement_type, 3)


def parse_market_rows(payload: Any, settings: Settings) -> tuple[list[MarketRow], tuple[str, ...]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Invalid Mahor API response (items missing)")
    items = payload["items"]
    if not items:
        raise ValueError("Mahor returned an empty markets list; not publishing")

    seen_markets: set[str] = set()
    parsed: list[MarketRow] = []
    codes_available: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Malformed market item")
        instrument = item.get("instrument")
        if not isinstance(instrument, dict):
            raise ValueError("Market instrument is missing")
        code = str(instrument.get("code", "")).strip().upper()
        title = str(instrument.get("title", "")).strip()
        settlement_type = str(item.get("settlementType", "")).strip().upper()
        category = str(instrument.get("category", "")).strip().upper()
        if not code or not title:
            raise ValueError("Market instrument has no code/title")
        codes_available.add(code)
        if settings.market_codes and code not in settings.market_codes:
            continue
        market_identity = str(item.get("marketId") or f"{code}/{settlement_type}")
        if market_identity in seen_markets:
            raise ValueError(f"Duplicate market entry {market_identity}")
        seen_markets.add(market_identity)

        if category == "MELTED_GOLD":
            title = {
                "SPOT": "آبشده نقد حاضر",
                "TOMORROW": "آبشده نقد فردا",
                "DAY_AFTER_TOMORROW": "آبشده نقد پس‌فردا",
            }.get(settlement_type, title)
        elif settlement_type in {"TOMORROW", "DAY_AFTER_TOMORROW"}:
            suffix = "فردا" if settlement_type == "TOMORROW" else "پس‌فردا"
            if suffix not in title:
                title = f"{title} ({suffix})"

        side_prices: list[Decimal | None] = []
        for side_name in ("buy", "sell"):
            side = item.get(side_name)
            if not isinstance(side, dict):
                raise ValueError(f"Missing side {side_name} for {code}")
            price = None
            if side.get("enabled") is True:
                price = parse_positive_price(side.get(settings.price_field))
                if price is None:
                    raise ValueError(f"Invalid enabled {settings.price_field} price for {code}/{side_name}")
            side_prices.append(price)
        buy, sell = side_prices
        if buy is not None or sell is not None:
            parsed.append(MarketRow(code=code, title=title, settlement_type=settlement_type, buy=buy, sell=sell, category=category))

    if settings.market_codes and not (settings.market_codes & set(codes_available)):
        raise ValueError("No matching MARKET_CODES. Available: " + ", ".join(sorted(codes_available)))

    parsed.sort(key=lambda x: (GROUP_ORDER.index(classify_group(x)) if classify_group(x) in GROUP_ORDER else 999, settlement_sort_key(x.settlement_type), x.code, x.title))
    return parsed, tuple(sorted(codes_available))


def build_group_message(group_key: str, rows: list[MarketRow], settings: Settings) -> str:
    # lines: list[str] = [f"🟢 {GROUP_TITLES[group_key]}", ""]
    for row in rows:
        lines.append(row.title)
        if settings.label_perspective == "customer":
            sell_label_price, buy_label_price = row.sell, row.buy
        else:
            sell_label_price, buy_label_price = row.buy, row.sell
        if buy_label_price is not None:
            lines.append(f"🟢 خرید: {format_price(buy_label_price)}")
        if sell_label_price is not None:
            lines.append(f"🔴 فروش: {format_price(sell_label_price)}")
        lines.append("")
    lines.extend(["📌 @" + settings.target.lstrip("@")])
    text = "\n".join(lines).strip()
    if len(text) > 3900:
        raise ValueError(f"Telegram message too long in group {group_key}; filter markets with MARKET_CODES")
    return text


def make_snapshot(payload: Any, settings: Settings) -> Snapshot:
    rows, available_codes = parse_market_rows(payload, settings)
    grouped: dict[str, list[MarketRow]] = {k: [] for k in GROUP_ORDER}
    for row in rows:
        grouped[classify_group(row)].append(row)

    messages: list[tuple[str, str]] = []
    for key in GROUP_ORDER:
        if grouped[key]:
            messages.append((key, build_group_message(key, grouped[key], settings)))

    if not messages:
        content = "🔴 وضعیت نرخ‌های ماهور\n\n⛔ در حال حاضر نرخ فعال برای انتشار وجود ندارد.\n\n📌 @" + settings.target.lstrip("@")
        messages.append(("empty", content))

    fingerprint_source = "\n\n=====GROUP=====".join([f"{k}\n{text}" for k, text in messages])
    return Snapshot(
        messages=tuple(messages),
        fingerprint=hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
        visible_markets=len(rows),
        available_market_codes=available_codes,
    )


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read state file {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise RuntimeError(f"Invalid state file {path}")
    return state


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


LOG = logging.getLogger("mahor_telegram")


async def fetch_prices(http: httpx.AsyncClient, settings: Settings) -> Any:
    headers = sign_rest(settings.key_id, settings.secret)
    response = await http.get(settings.base_url + PRICE_PATH, headers=headers)
    response.raise_for_status()
    return response.json()


class Publisher:
    def __init__(self, settings: Settings, telegram: Any | None, http: httpx.AsyncClient, dry_run: bool = False):
        self.settings = settings
        self.telegram = telegram
        self.http = http
        self.dry_run = dry_run
        self.state = read_state(settings.state_file) if not dry_run else {}

    async def sync(self) -> bool:
        feed = await fetch_prices(self.http, self.settings)
        snapshot = make_snapshot(feed, self.settings)
        if self.dry_run:
            print("\n--- MAHOR PREVIEW (NOT SENT) ---\n")
            for idx, (group_key, text) in enumerate(snapshot.messages, start=1):
                print(f"[MESSAGE {idx} - {group_key}]\n")
                print(text)
                print("\n-----------------------------\n")
            print("Available MARKET_CODES:", ", ".join(snapshot.available_market_codes))
            return False
        if self.state.get("fingerprint") == snapshot.fingerprint:
            LOG.info("No price change; no Telegram message")
            return False
        last_sent = float(self.state.get("sent_at", 0))
        delay = self.settings.min_post_seconds - (time.time() - last_sent)
        if delay > 0:
            await asyncio.sleep(delay)
            snapshot = make_snapshot(await fetch_prices(self.http, self.settings), self.settings)
            if self.state.get("fingerprint") == snapshot.fingerprint:
                return False

        from telethon.errors import FloodWaitError, MessageIdInvalidError
        try:
            sent_message_ids = await self._send_messages(snapshot.messages, MessageIdInvalidError)
        except FloodWaitError as exc:
            LOG.warning("Telegram FloodWait: sleeping %d seconds", exc.seconds)
            await asyncio.sleep(exc.seconds + 1)
            snapshot = make_snapshot(await fetch_prices(self.http, self.settings), self.settings)
            if self.state.get("fingerprint") == snapshot.fingerprint:
                return False
            sent_message_ids = await self._send_messages(snapshot.messages, MessageIdInvalidError)

        self.state = {
            "fingerprint": snapshot.fingerprint,
            "message_ids": sent_message_ids,
            "sent_at": time.time(),
        }
        write_state(self.settings.state_file, self.state)
        LOG.info("Published %d markets in %d messages", snapshot.visible_markets, len(snapshot.messages))
        return True

    async def _send_messages(self, messages: tuple[tuple[str, str], ...], invalid_id_exception: type[Exception]) -> dict[str, int]:
        sent: dict[str, int] = {}
        old_ids = self.state.get("message_ids") if isinstance(self.state.get("message_ids"), dict) else {}

        for group_key, text in messages:
            if self.settings.send_mode == "edit" and group_key in old_ids:
                try:
                    msg = await self.telegram.edit_message(self.settings.target, int(old_ids[group_key]), text)
                    sent[group_key] = int(msg.id)
                    continue
                except invalid_id_exception:
                    LOG.warning("Previous message for %s unavailable; publishing a new one", group_key)
            msg = await self.telegram.send_message(self.settings.target, text, link_preview=False)
            sent[group_key] = int(msg.id)
            await asyncio.sleep(0.6)
        return sent


async def periodic_refresh(event: asyncio.Event, poll_seconds: float) -> None:
    while True:
        await asyncio.sleep(poll_seconds)
        event.set()


async def websocket_listener(settings: Settings, event: asyncio.Event) -> None:
    import socketio
    while True:
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)

        @sio.on("partner-prices:updated", namespace=WS_NAMESPACE)
        async def prices_updated(_data: Any) -> None:
            event.set()

        @sio.on("partner-feed:error", namespace=WS_NAMESPACE)
        async def partner_error(payload: Any) -> None:
            LOG.warning("Mahor realtime access error: %s", payload)

        try:
            await sio.connect(
                settings.base_url,
                transports=["websocket"],
                namespaces=[WS_NAMESPACE],
                auth=sign_ws(settings.key_id, settings.secret),
                wait_timeout=10,
            )
            LOG.info("Mahor realtime websocket connected")
            event.set()
            await sio.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("Realtime unavailable (%s). REST fallback remains active.", exc)
        finally:
            if sio.connected:
                try:
                    await sio.disconnect()
                except Exception:
                    pass
        await asyncio.sleep(10)


async def run_worker(publisher: Publisher, event: asyncio.Event) -> None:
    event.set()
    while True:
        await event.wait()
        event.clear()
        if publisher.settings.debounce_seconds:
            await asyncio.sleep(publisher.settings.debounce_seconds)
        event.clear()
        try:
            await publisher.sync()
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            LOG.error("Mahor API HTTP %s (check credentials, API enabled, server)", exc.response.status_code)
            await asyncio.sleep(10)
            event.set()
        except Exception:
            LOG.exception("Sync failed; last posted state is retained")
            await asyncio.sleep(10)
            event.set()


async def run(once: bool = False, check: bool = False) -> None:
    settings = read_settings()
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=False) as http:
        if check:
            await Publisher(settings, None, http, dry_run=True).sync()
            return
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id = os.getenv("API_ID", "").strip()
        api_hash = os.getenv("API_HASH", "").strip()
        session = os.getenv("STRING_SESSION", "").strip()
        if not api_id.isdigit() or not api_hash or not session:
            raise ValueError("API_ID, API_HASH and STRING_SESSION are required to send")
        telegram = TelegramClient(StringSession(session), int(api_id), api_hash)
        await telegram.start()
        try:
            publisher = Publisher(settings, telegram, http)
            if once:
                await publisher.sync()
                return
            event = asyncio.Event()
            tasks = [
                asyncio.create_task(periodic_refresh(event, settings.poll_seconds)),
                asyncio.create_task(websocket_listener(settings, event)),
            ]
            try:
                await run_worker(publisher, event)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await telegram.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mahor -> Telegram (Railway / one-file)")
    parser.add_argument("--check", action="store_true", help="Preview API data; never send to Telegram")
    parser.add_argument("--once", action="store_true", help="Fetch once and send only if changed")
    args = parser.parse_args()
    if args.check and args.once:
        parser.error("--check and --once cannot be used together")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(once=args.once, check=args.check))
    except KeyboardInterrupt:
        LOG.info("Stopped")


if __name__ == "__main__":
    main()
