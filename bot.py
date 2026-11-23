"""Mahor tomorrow melted-gold SELL price -> Telegram channel.

A single-file Railway bot. No secrets are hardcoded.

Required Railway Variables:
    MAHOR_PARTNER_KEY_ID, MAHOR_PARTNER_SECRET
    TARGET=@YousefianAbShodeh
    BOT_TOKEN=...  # Telegram bot created in @BotFather; grant channel post rights.

If BOT_TOKEN is absent, the old API_ID, API_HASH and STRING_SESSION still work,
but normal Telegram *user accounts cannot attach a real inline URL button*.
The user-session fallback includes a clickable text URL instead.

Recommended:
    PRICE_FIELD=basePrice
    POLL_SECONDS=10
    DEBOUNCE_SECONDS=0.2
    MIN_POST_SECONDS=1
    STATE_FILE=/data/mahor_farda_sell_state.json  # mount a Railway Volume at /data

Run `python bot.py --check` to preview without posting.
Build verified: full single-file bot (2026-11-23).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import httpx

LOG = logging.getLogger("mahor-farda-sell")
PRICE_PATH = "/v1/partner-api/prices"
WS_NAMESPACE = "/partner-price-feed"
APP_URL = "https://goldmahor.ir"
BUTTON_TEXT = "دریافت لینک اپلیکیشن"
BODY_HASH = hashlib.sha256(b"").hexdigest()
MESGHAL_TO_GRAM = Decimal("4.3318")
THOUSAND = Decimal("1000")


@dataclass(frozen=True)
class Config:
    base_url: str
    key_id: str
    secret: str
    target: str
    bot_token: str
    price_field: str
    market_code: str
    poll_seconds: float
    debounce_seconds: float
    min_post_seconds: float
    http_timeout: float
    state_file: Path


@dataclass(frozen=True)
class Quote:
    raw_price: Decimal
    market_id: str
    code: str
    # Display values are integer units with 3 trailing zeros removed.
    mazne: int
    gram: int


class TelegramPublishError(Exception):
    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


def env_number(key: str, default: str, minimum: float) -> float:
    try:
        value = float(os.getenv(key, default))
    except ValueError as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if not minimum <= value < float("inf"):
        raise ValueError(f"{key} must be at least {minimum}")
    return value


def config_from_env() -> Config:
    base_url = os.getenv("MAHOR_API_BASE_URL", "https://api.goldmahor.ir").strip().rstrip("/")
    if not base_url.startswith("https://") or "?" in base_url or "#" in base_url:
        raise ValueError("MAHOR_API_BASE_URL must be an HTTPS origin")
    key_id = os.getenv("MAHOR_PARTNER_KEY_ID", "").strip()
    secret = os.getenv("MAHOR_PARTNER_SECRET", "").strip()
    if not key_id or not secret:
        raise ValueError("MAHOR_PARTNER_KEY_ID and MAHOR_PARTNER_SECRET are required")
    target = os.getenv("TARGET", "@YousefianAbShodeh").strip()
    if not target:
        raise ValueError("TARGET cannot be empty")
    token = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    field = os.getenv("PRICE_FIELD", "basePrice").strip()
    if field not in ("basePrice", "price"):
        raise ValueError("PRICE_FIELD must be basePrice or price")
    return Config(
        base_url=base_url,
        key_id=key_id,
        secret=secret,
        target=target,
        bot_token=token,
        price_field=field,
        market_code=os.getenv("MAHOR_TARGET_MARKET_CODE", "").strip().upper(),
        poll_seconds=env_number("POLL_SECONDS", "10", 5),
        debounce_seconds=env_number("DEBOUNCE_SECONDS", "0.2", 0),
        min_post_seconds=env_number("MIN_POST_SECONDS", "1", 0),
        http_timeout=env_number("HTTP_TIMEOUT_SECONDS", "8", 1),
        state_file=Path(os.getenv("STATE_FILE", "state.json")).expanduser(),
    )


def rest_headers(config: Config) -> dict[str, str]:
    ts = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    canonical = "\n".join(["GET", PRICE_PATH, ts, nonce, BODY_HASH])
    digest = hmac.new(config.secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "Accept": "application/json",
        "X-Mahor-Partner-Key": config.key_id,
        "X-Mahor-Timestamp": ts,
        "X-Mahor-Nonce": nonce,
        "X-Mahor-Content-SHA256": BODY_HASH,
        "X-Mahor-Signature": digest,
    }


def realtime_auth(config: Config) -> dict[str, str]:
    ts = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    canonical = "\n".join(["WS_CONNECT", WS_NAMESPACE, ts, nonce])
    digest = hmac.new(config.secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"keyId": config.key_id, "timestamp": ts, "nonce": nonce, "signature": digest}


def positive_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def rounded_whole(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def find_farda_sell(feed: Any, config: Config) -> tuple[Quote | None, str | None]:
    """Return only MELTED_GOLD / TOMORROW / SELL; never other markets."""
    if not isinstance(feed, dict) or not isinstance(feed.get("items"), list):
        raise ValueError("Mahor price feed has no items list")
    if not feed["items"]:
        # An outage must never be published as a zero price.
        raise ValueError("Mahor returned empty items; retaining previous price")

    matches = []
    for market in feed["items"]:
        if not isinstance(market, dict):
            raise ValueError("Malformed market data")
        instrument = market.get("instrument")
        if not isinstance(instrument, dict):
            raise ValueError("Missing instrument data")
        category = str(instrument.get("category", "")).upper().strip()
        settlement = str(market.get("settlementType", "")).upper().strip()
        code = str(instrument.get("code", "")).upper().strip()
        if category != "MELTED_GOLD" or settlement != "TOMORROW":
            continue
        if config.market_code and code != config.market_code:
            continue
        matches.append((market, code))

    if len(matches) > 1:
        codes = ", ".join(code for _, code in matches)
        raise ValueError(f"Multiple TOMORROW melted-gold markets ({codes}); set MAHOR_TARGET_MARKET_CODE")
    if not matches:
        LOG.warning("Mahor tomorrow melted-gold market not found; nothing posted")
        return None, None

    market, code = matches[0]
    market_id = str(market.get("marketId") or "").strip()
    side = market.get("sell")
    if not isinstance(side, dict):
        raise ValueError("Missing SELL side for tomorrow melted-gold market")
    if side.get("enabled") is not True:
        # Market closed / seller side disabled: no post, no zero-price message.
        return None, market_id or None
    raw = positive_decimal(side.get(config.price_field))
    if raw is None:
        raise ValueError(f"Invalid active SELL {config.price_field}; preserving last posted price")

    # 104,300,000 -> 104,300 ; (104,300,000 / 4.3318) / 1000 -> 24,078
    mazne = rounded_whole(raw / THOUSAND)
    gram = rounded_whole(raw / MESGHAL_TO_GRAM / THOUSAND)
    return Quote(raw, market_id, code, mazne, gram), market_id or None


def text_for_quote(quote: Quote, previous_mazne: int | None, *, has_button: bool) -> str:
    # Compare with the last **published displayed** price, not the latest API tick.
    direction = ""
    if previous_mazne is not None:
        direction = " 🔵" if quote.mazne > previous_mazne else " 🔴" if quote.mazne < previous_mazne else ""
    text = f"🔅 آبشده فروش : {quote.mazne:,}{direction}\n\n✨ گرمی : {quote.gram:,}"
    if not has_button:
        text += f"\n\n🔗 {BUTTON_TEXT}: {APP_URL}"
    return text


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read state file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Invalid state.json structure")
    return value


def save_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


async def request_mahor(http: httpx.AsyncClient, config: Config) -> Any:
    response = await http.get(config.base_url + PRICE_PATH, headers=rest_headers(config))
    response.raise_for_status()
    return response.json()


class TelegramSender:
    """Bot API for true inline buttons; legacy Telethon session is fallback."""

    def __init__(self, config: Config, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.telethon_client: Any = None
        self.is_bot = bool(config.bot_token)

    async def start(self) -> None:
        if self.is_bot:
            # No STRING_SESSION at all when a Bot token is used.
            # The bot must be a channel admin with Post Messages permission.
            await self._bot_call("getMe", {})
            LOG.info("Telegram Bot API authenticated; inline URL button enabled")
            return
        api_id = os.getenv("API_ID", "").strip()
        api_hash = os.getenv("API_HASH", "").strip()
        session = os.getenv("STRING_SESSION", "").strip()
        if not api_id.isdigit() or not api_hash or not session:
            raise ValueError("Set BOT_TOKEN for the inline button, or API_ID / API_HASH / STRING_SESSION for text-link fallback")
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        self.telethon_client = TelegramClient(StringSession(session), int(api_id), api_hash)
        await self.telethon_client.start()
        LOG.warning("No BOT_TOKEN: sending with legacy Telegram user session (link in message; no inline button)")

    async def close(self) -> None:
        if self.telethon_client is not None:
            await self.telethon_client.disconnect()

    async def _bot_call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        # Telegram requires the token in the URL. NEVER log the URL or HTTPX exceptions.
        url = f"https://api.telegram.org/bot{self.config.bot_token}/{method}"
        try:
            response = await self.http.post(url, json=body)
            data = response.json()
        except (httpx.RequestError, ValueError) as exc:
            raise TelegramPublishError(f"Telegram Bot API network/response error ({type(exc).__name__})") from None
        if not isinstance(data, dict):
            raise TelegramPublishError("Telegram Bot API returned malformed JSON")
        if response.status_code == 429:
            retry_after = data.get("parameters", {}).get("retry_after", 5) if isinstance(data.get("parameters"), dict) else 5
            try:
                retry = min(600, max(1, float(retry_after)))
            except (TypeError, ValueError):
                retry = 5
            raise TelegramPublishError("Telegram Bot API rate-limited publication", retry_after=retry)
        if response.status_code >= 400 or data.get("ok") is not True:
            # Description provided by Telegram, not URL/token.
            reason = str(data.get("description", "Unknown error"))[:180]
            raise TelegramPublishError(f"Telegram Bot API error {response.status_code}: {reason}")
        return data

    async def send(self, text: str) -> int:
        if self.is_bot:
            payload = {
                "chat_id": self.config.target,
                "text": text,
                "link_preview_options": {"is_disabled": True},
                "reply_markup": {
                    "inline_keyboard": [[{"text": BUTTON_TEXT, "url": APP_URL}]]
                },
            }
            data = await self._bot_call("sendMessage", payload)
            result = data.get("result", {})
            if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
                raise TelegramPublishError("Telegram did not return a message ID")
            return result["message_id"]
        msg = await self.telethon_client.send_message(self.config.target, text, link_preview=False)
        return int(msg.id)


class Publisher:
    def __init__(self, config: Config, http: httpx.AsyncClient, sender: TelegramSender | None):
        self.config = config
        self.http = http
        self.sender = sender
        self.state = read_state(config.state_file)
        self.tracked_market_id: str | None = None

    def previous_mazne(self, quote: Quote) -> int | None:
        if self.state.get("version") != 2:
            return None  # Prior multi-market bot state cannot identify this quote.
        if (self.state.get("target") != self.config.target or
            self.state.get("price_field") != self.config.price_field or
            self.state.get("market_code") != quote.code):
            return None
        try:
            prev = int(self.state["last_mazne"])
            return prev if prev > 0 else None
        except (KeyError, TypeError, ValueError):
            return None

    async def sync(self, preview: bool = False) -> bool:
        feed = await request_mahor(self.http, self.config)
        quote, market_id = find_farda_sell(feed, self.config)
        if market_id:
            self.tracked_market_id = market_id
        if quote is None:
            LOG.info("Tomorrow SELL market unavailable/closed; not publishing and keeping previous price")
            return False

        previous = self.previous_mazne(quote)
        if preview:
            print("\n--- PREVIEW ONLY (NO TELEGRAM POST) ---")
            print(text_for_quote(quote, previous, has_button=bool(self.config.bot_token)))
            print("\nButton:", BUTTON_TEXT, "=>", APP_URL)
            print("Raw SELL base/price:", quote.raw_price)
            print("Market code:", quote.code, "marketId:", quote.market_id)
            print("--- END PREVIEW ---\n")
            return False

        if previous is not None and previous == quote.mazne:
            LOG.debug("Tomorrow SELL display price unchanged: %s", quote.mazne)
            return False

        if self.sender is None:
            raise RuntimeError("Telegram sender not initialized")
        last = float(self.state.get("sent_at", 0)) if self.state.get("version") == 2 else 0.0
        wait = self.config.min_post_seconds - (time.time() - last)
        if wait > 0:
            await asyncio.sleep(wait)
            # Publish latest authoritative quote, not a stale intermediate tick.
            feed = await request_mahor(self.http, self.config)
            quote, market_id = find_farda_sell(feed, self.config)
            if quote is None:
                return False
            if market_id:
                self.tracked_market_id = market_id
            previous = self.previous_mazne(quote)
            if previous is not None and previous == quote.mazne:
                return False

        text = text_for_quote(quote, previous, has_button=self.sender.is_bot)
        try:
            msg_id = await self.sender.send(text)
        except TelegramPublishError as exc:
            if not exc.retry_after:
                raise
            LOG.warning("Telegram flood control; retrying after %.1f seconds", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
            # Re-read after flood wait, preserving last published comparison.
            feed = await request_mahor(self.http, self.config)
            quote, market_id = find_farda_sell(feed, self.config)
            if quote is None:
                return False
            if market_id:
                self.tracked_market_id = market_id
            previous = self.previous_mazne(quote)
            if previous is not None and previous == quote.mazne:
                return False
            text = text_for_quote(quote, previous, has_button=self.sender.is_bot)
            msg_id = await self.sender.send(text)
        except Exception as exc:
            # Telethon FloodWait requires a fresh quote after waiting too.
            try:
                from telethon.errors import FloodWaitError
            except ImportError:
                raise
            if not isinstance(exc, FloodWaitError):
                raise
            LOG.warning("Telegram FloodWait: %d seconds", exc.seconds)
            await asyncio.sleep(exc.seconds + 1)
            feed = await request_mahor(self.http, self.config)
            quote, market_id = find_farda_sell(feed, self.config)
            if quote is None:
                return False
            if market_id:
                self.tracked_market_id = market_id
            previous = self.previous_mazne(quote)
            if previous is not None and previous == quote.mazne:
                return False
            msg_id = await self.sender.send(text_for_quote(quote, previous, has_button=False))

        new_state = {
            "version": 2,
            "target": self.config.target,
            "price_field": self.config.price_field,
            "market_code": quote.code,
            "last_mazne": quote.mazne,
            "last_raw_price": str(quote.raw_price),
            "message_id": msg_id,
            "sent_at": time.time(),
        }
        save_state(self.config.state_file, new_state)
        self.state = new_state
        LOG.info("Published tomorrow melted-gold SELL = %s (gram=%s, message_id=%s)",
                 f"{quote.mazne:,}", f"{quote.gram:,}", msg_id)
        return True


async def websocket_watcher(config: Config, trigger: asyncio.Event, publisher: Publisher) -> None:
    import socketio

    while True:
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)

        @sio.on("partner-prices:updated", namespace=WS_NAMESPACE)
        async def updated(payload: Any) -> None:
            tracked = publisher.tracked_market_id
            if tracked and isinstance(payload, dict) and isinstance(payload.get("instrumentMarketIds"), list):
                ids = payload["instrumentMarketIds"]
                if ids and tracked not in ids:
                    return  # Ignore changes to unrelated markets.
            trigger.set()

        @sio.on("partner-feed:error", namespace=WS_NAMESPACE)
        async def feed_error(payload: Any) -> None:
            LOG.warning("Mahor realtime access error: %s", payload)

        try:
            await sio.connect(
                config.base_url,
                transports=["websocket"],
                namespaces=[WS_NAMESPACE],
                auth=realtime_auth(config),
                wait_timeout=10,
            )
            LOG.info("Mahor realtime websocket connected")
            trigger.set()
            await sio.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("Mahor realtime temporarily unavailable: %s. REST fallback stays on.", exc)
        finally:
            if sio.connected:
                try:
                    await sio.disconnect()
                except Exception:
                    pass
        trigger.set()  # Catch updates missed during disconnection.
        await asyncio.sleep(5)


async def periodic_poll(trigger: asyncio.Event, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        trigger.set()


async def worker(publisher: Publisher, trigger: asyncio.Event) -> None:
    trigger.set()  # Initial state, then only changes in displayed tomorrow SELL.
    while True:
        await trigger.wait()
        trigger.clear()
        if publisher.config.debounce_seconds:
            await asyncio.sleep(publisher.config.debounce_seconds)
        # Intentionally do NOT clear after debounce; newer events remain queued.
        try:
            await publisher.sync()
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            LOG.error("Mahor API HTTP %d", exc.response.status_code)
            await asyncio.sleep(3)
            trigger.set()
        except Exception:
            LOG.exception("Price sync failed; previous Telegram price retained")
            await asyncio.sleep(3)
            trigger.set()


async def run(*, check: bool = False, once: bool = False) -> None:
    config = config_from_env()
    async with httpx.AsyncClient(timeout=config.http_timeout, follow_redirects=False) as http:
        publisher = Publisher(config, http, sender=None)
        if check:
            await publisher.sync(preview=True)
            return
        sender = TelegramSender(config, http)
        await sender.start()
        publisher.sender = sender
        try:
            if once:
                await publisher.sync()
                return
            trigger = asyncio.Event()
            listeners = [
                asyncio.create_task(periodic_poll(trigger, config.poll_seconds)),
                asyncio.create_task(websocket_watcher(config, trigger, publisher)),
            ]
            try:
                await worker(publisher, trigger)
            finally:
                for task in listeners:
                    task.cancel()
                await asyncio.gather(*listeners, return_exceptions=True)
        finally:
            await sender.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mahor TOMORROW melted-gold SELL -> Telegram")
    parser.add_argument("--check", action="store_true", help="Read Mahor and preview; do NOT post")
    parser.add_argument("--once", action="store_true", help="One comparison & optional post")
    args = parser.parse_args()
    if args.check and args.once:
        parser.error("--check and --once cannot be combined")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx's default INFO log includes the URL, which would leak BOT_TOKEN.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(run(check=args.check, once=args.once))
    except KeyboardInterrupt:
        LOG.info("Stopped")


if __name__ == "__main__":
    main()

