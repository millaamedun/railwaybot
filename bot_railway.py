import os
import re
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
string_session = os.getenv("STRING_SESSION")

client = TelegramClient(StringSession(string_session), api_id, api_hash)

SOURCE = "alizadeyazd"
TARGET = "YousefianAbShodeh"

price_pattern = re.compile(r'(فروش|خرید)\s*:\s*([\d,]+)')

processed_ids = set()


async def process_message(msg):
    text = msg.message or ""

    if not price_pattern.search(text):
        return

    lines = text.splitlines()
    new_lines = []

    # آیا پیام شامل مثقال یا گرم هست؟
    has_mesghal_or_gram = ("مثقال" in text) or ("گرم" in text)

    current_section = None

    for line in lines:
        # تشخیص سکشن
        if "مثقال" in line:
            current_section = "mesghal"
        elif "گرم" in line:
            current_section = "gram"

        match = price_pattern.search(line)
        if match:
            label, number = match.groups()
            clean = int(number.replace(",", ""))

            # تعیین delta
            if has_mesghal_or_gram:
                if current_section == "mesghal":
                    delta = 10000
                elif current_section == "gram":
                    delta = 2100
                else:
                    # قیمت‌هایی که زیر سکشن نامشخصن دست نخورده می‌مونن
                    new_lines.append(line)
                    continue
            else:
                # پیام سکه
                delta = 100000

            new_price = clean - delta if label == "خرید" else clean + delta
            new_price_str = f"{new_price:,}"

            line = re.sub(
                price_pattern,
                f"{label} : {new_price_str}",
                line
            )

        new_lines.append(line)

    new_text = "\n".join(new_lines)

    # حذف آیدی مبدا
    new_text = re.sub(r'@[\w]+', '', new_text).strip()

    # افزودن آیدی خودت
    new_text += "\n\n📌 @YousefianAbShodeh"

    await client.send_message(TARGET, new_text)
    print("FORWARDED:\n", new_text)


async def poll():
    print("Polling bot started…")

    while True:
        try:
            messages = await client.get_messages(SOURCE, limit=5)

            for msg in reversed(messages):
                if msg.id not in processed_ids:
                    processed_ids.add(msg.id)
                    await process_message(msg)

        except Exception as e:
            print("Error:", e)

        await asyncio.sleep(5)


async def main():
    await client.start()
    await poll()

asyncio.run(main())