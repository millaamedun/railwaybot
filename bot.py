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

    main()
