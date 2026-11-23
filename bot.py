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
