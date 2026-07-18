FROM node:24-alpine AS frontend-build

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/app

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        openssl \
        ffmpeg \
        wine \
        wine32 \
        wine64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY --from=frontend-build /app/static /app/app/static
COPY docker/connector-entrypoint.sh /usr/local/bin/connector-entrypoint
RUN chmod +x /usr/local/bin/connector-entrypoint
RUN mkdir -p /app/storage/SONGS/TJA /app/storage/SONGS/OSU /app/storage/SONGS/CONVERTED /app/storage/cabinets

EXPOSE 8090
ENTRYPOINT ["/usr/local/bin/connector-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
