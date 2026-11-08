FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# Install the bot's dependencies explicitly and verify they can be imported.
# The explicit names also protect against an outdated requirements.txt in GitHub.
RUN python -m pip install --no-cache-dir -r requirements.txt \
    'Telethon>=1.35,<2' \
    'httpx>=0.27,<1' \
    'python-socketio[asyncio_client]>=5.11,<6' \
    && python -m pip check \
    && python -c "import httpx, socketio, telethon; print('DEPENDENCIES OK - httpx:', httpx.__version__)"

COPY bot.py /app/bot.py
RUN python -m py_compile /app/bot.py

CMD ["python", "bot.py"]
