#!/bin/sh
set -eu

tls_enabled="${TJAREPO_TLS_ENABLED:-1}"

# All conversion workers share this prefix. Initialize it once here so the
# first batch cannot race several Wine prefix initializations against itself.
if [ -n "${WINEPREFIX:-}" ] && [ ! -d "$WINEPREFIX/drive_c" ]; then
    wineboot -u >/dev/null 2>&1 || true
    wineserver -w >/dev/null 2>&1 || true
fi

if [ "$tls_enabled" != "0" ] && [ "$tls_enabled" != "false" ]; then
    cert_dir="${TJAREPO_TLS_CERT_DIR:-/app/storage/certificates/local}"
    cert_path="${TJAREPO_TLS_CERT_FILE:-$cert_dir/server.crt}"
    key_path="${TJAREPO_TLS_KEY_FILE:-$cert_dir/server.key}"
    cn="${TJAREPO_TLS_CN:-tjarepo.local}"

    mkdir -p "$(dirname "$cert_path")" "$(dirname "$key_path")"

    if [ ! -s "$cert_path" ] || [ ! -s "$key_path" ]; then
        openssl req \
            -x509 \
            -newkey rsa:2048 \
            -sha256 \
            -nodes \
            -days "${TJAREPO_TLS_DAYS:-3650}" \
            -subj "/CN=$cn" \
            -addext "subjectAltName=DNS:$cn,DNS:localhost,IP:127.0.0.1" \
            -keyout "$key_path" \
            -out "$cert_path"
    fi

    if [ "$#" -gt 0 ] && [ "$1" = "uvicorn" ]; then
        set -- "$@" --ssl-certfile "$cert_path" --ssl-keyfile "$key_path"
    fi
fi

exec "$@"
