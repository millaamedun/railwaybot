# Railway: use Docker instead of Railpack/mise.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the precise runtime dependencies directly so an old/pinned
# requirements.txt in GitHub cannot conflict with the bot's dependencies.
RUN python -m pip install --no-cache-dir \
    'Telethon>=1.35,<2' \
    'httpx>=0.27,<1' \
    'python-socketio[asyncio_client]>=5.11,<6' \
    && python -m pip check \
    && python -c "import httpx, socketio, telethon; print('DEPENDENCIES OK - httpx:', httpx.__version__)"

COPY bot.py /app/bot.py
RUN python -m py_compile /app/bot.py

CMD ["python", "bot.py"]
