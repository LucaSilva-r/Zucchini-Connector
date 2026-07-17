#!/bin/sh
# Run the connector directly, no Docker (Linux/macOS).
# Creates a virtualenv, installs dependencies, generates a self-signed TLS
# certificate on first run, and serves on https://0.0.0.0:8443.
#
# Requirements: python3 (3.10+), openssl. For song conversion additionally:
# ffmpeg, wine, and storage/ps3_at3tool.exe. The UI and cabinet management
# work without the conversion tools.
set -eu
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "error: python3 not found"; exit 1; }

if [ ! -d .venv ]; then
    echo "[run] creating virtualenv..."
    "$PY" -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f app/static/index.html ]; then
    if command -v npm >/dev/null 2>&1; then
        echo "[run] building web UI..."
        (cd frontend && npm ci && npm run build)
    else
        echo "error: app/static is missing and npm is not installed to build it"
        exit 1
    fi
fi

command -v ffmpeg >/dev/null 2>&1 || echo "[run] WARNING: ffmpeg not found — song conversion will fail"
command -v wine   >/dev/null 2>&1 || echo "[run] WARNING: wine not found — song conversion will fail"
[ -f storage/ps3_at3tool.exe ]    || echo "[run] WARNING: storage/ps3_at3tool.exe missing — song conversion will fail"

mkdir -p storage/ESE storage/OSU storage/ESE-convert storage/cabinets

cert_dir="storage/certificates/local"
if [ -e "$cert_dir/server.key" ] && [ ! -r "$cert_dir/server.key" ]; then
    echo "error: $cert_dir/server.key exists but is not readable (created by Docker as root?)"
    echo "fix:   sudo chown -R \$(id -u):\$(id -g) storage/certificates"
    exit 1
fi
if [ ! -s "$cert_dir/server.crt" ] || [ ! -s "$cert_dir/server.key" ]; then
    command -v openssl >/dev/null 2>&1 || { echo "error: openssl is required to create the TLS certificate"; exit 1; }
    echo "[run] generating self-signed TLS certificate..."
    mkdir -p "$cert_dir"
    openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
        -days "${CONNECTOR_TLS_DAYS:-3650}" \
        -subj "/CN=${CONNECTOR_TLS_CN:-connector.local}" \
        -addext "subjectAltName=DNS:${CONNECTOR_TLS_CN:-connector.local},DNS:localhost,IP:127.0.0.1" \
        -keyout "$cert_dir/server.key" -out "$cert_dir/server.crt"
fi

port="${CONNECTOR_HTTPS_PORT:-8443}"
echo "[run] web UI: https://localhost:$port/ui"
# tja2fumen is vendored inside app/ and imported as a top-level module.
export PYTHONPATH="$PWD/app${PYTHONPATH:+:$PYTHONPATH}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$port" \
    --ssl-certfile "$cert_dir/server.crt" --ssl-keyfile "$cert_dir/server.key"
