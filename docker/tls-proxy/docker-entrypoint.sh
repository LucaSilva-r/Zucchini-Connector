#!/bin/sh
set -eu

cert_dir=/certificates/local
cert_path="$cert_dir/server.crt"
key_path="$cert_dir/server.key"
cn="${TJAREPO_TLS_CN:-tjarepo.local}"

mkdir -p "$cert_dir"

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

exec "$@"
