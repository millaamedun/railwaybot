from telethon import TelegramClient, events
import re, os

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")

SOURCE = "alizadeyazd"
TARGET = "YousefianAbShodeh"

SESSION = "railway.session"  # ← سشن مخصوص Railway

pattern = re.compile(r'(فروش|خرید)\s*:\s*([\d,]+)')

client = TelegramClient(SESSION, api_id, api_hash)

@client.on(events.NewMessage(chats=SOURCE))
async def handler(event):
    text = event.message.message or ""
    if not pattern.search(text):
        return

    matches = pattern.findall(text)
    new_text = text

    for label, number in matches:
        clean = int(number.replace(",", ""))
        new = clean - 10000 if label == "خرید" else clean + 10000
        new_str = f"{new:,}"
        new_text = re.sub(
            fr'{label}\s*:\s*{number}',
            f"{label} : {new_str}",
            new_text
        )

    new_text = re.sub(r'@[\w]+', '', new_text).strip()
    new_text += "\n\n📌 @YousefianAbShodeh"

    await client.send_message(TARGET, new_text)
    print("SENT:\n", new_text)

async def main():
    await client.start()
    print("Railway bot is running…")
    await client.run_until_disconnected()

import asyncio
asyncio.run(main())
