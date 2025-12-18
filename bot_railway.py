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

pattern = re.compile(r'(فروش|خرید)\s*:\s*([\d,]+)')

# جلوگیری از ارسال تکراری
processed_ids = set()


def detect_delta(text: str) -> int:
    """
    تشخیص مقدار اختلاف قیمت بر اساس نوع
    """
    if "مثقال" in text:
        return 10000
    elif "گرم" in text:
        return 2100
    else:
        return 100000  # سکه و سایر موارد


async def process_message(msg):
    text = msg.message or ""

    # فقط پیام‌هایی که قیمت دارند
    if not pattern.search(text):
        return

    delta = detect_delta(text)

    matches = pattern.findall(text)
    new_text = text

    for label, number in matches:
        clean = int(number.replace(",", ""))

        if label == "خرید":
            new_price = clean - delta
        else:  # فروش
            new_price = clean + delta

        new_price_str = f"{new_price:,}"

        new_text = re.sub(
            fr"{label}\s*:\s*{number}",
            f"{label} : {new_price_str}",
            new_text
        )

    # حذف آیدی کانال مبدا
    new_text = re.sub(r'@[\w]+', '', new_text).strip()

    # افزودن آیدی کانال تو
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

        await asyncio.sleep(5)  # هر ۵ ثانیه


async def main():
    await client.start()
    await poll()


asyncio.run(main())