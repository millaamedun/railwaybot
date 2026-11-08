# Avoid Railpack/mise; build the Telegram bot using Docker directly.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY bot_railway.py ./

CMD ["python", "bot_railway.py"]
