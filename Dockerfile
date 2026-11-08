FROM python:3.12-slim
WORKDIR /app
RUN python -m pip install --no-cache-dir --upgrade pip
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir 'Telethon>=1.35,<2' 'httpx>=0.27,<1' 'python-socketio[asyncio_client]>=5.11,<6' && python -m pip check && python -c "import httpx, socketio, telethon; print(\"DEPENDENCIES OK - httpx:\", httpx.__version__)"
COPY bot.py ./
CMD ["python", "bot.py"]
