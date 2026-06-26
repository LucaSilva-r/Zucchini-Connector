FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/app

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        openssl \
        sox \
        libsox-fmt-all \
        wine \
        wine32 \
        wine64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY docker/tjarepo-entrypoint.sh /usr/local/bin/tjarepo-entrypoint
RUN chmod +x /usr/local/bin/tjarepo-entrypoint
RUN mkdir -p /app/storage/ESE-convert

EXPOSE 8090
ENTRYPOINT ["/usr/local/bin/tjarepo-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
