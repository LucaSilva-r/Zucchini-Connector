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

port="${CONNECTOR_HTTPS_PORT:-8443}"
case "$port" in
    ''|*[!0-9]*) echo "error: CONNECTOR_HTTPS_PORT must be a number from 1 to 65535"; exit 1 ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "error: CONNECTOR_HTTPS_PORT must be a number from 1 to 65535"
    exit 1
fi

confirm_firewall_change() {
    [ -t 0 ] || return 1
    printf "[run] %s [y/N] " "$1"
    read -r answer
    case "$answer" in
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

ufw_allows_port() {
    printf '%s\n' "$1" | awk -v tcp="$port/tcp" -v any="$port" \
        '$1 == tcp || $1 == any { if ($2 == "ALLOW") found = 1 } END { exit !found }'
}

check_linux_firewall() {
    [ "${CONNECTOR_FIREWALL:-1}" != "0" ] || {
        echo "[run] firewall check skipped (CONNECTOR_FIREWALL=0)"
        return
    }

    # firewalld can report its state without root on its usual D-Bus setup.
    if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --quiet --state 2>/dev/null; then
        zones="$(firewall-cmd --get-active-zones 2>/dev/null | awk 'NF == 1 { print $1 }')"
        [ -n "$zones" ] || zones="$(firewall-cmd --get-default-zone 2>/dev/null || true)"
        if [ -z "$zones" ]; then
            echo "[run] WARNING: firewalld is active but its active zone could not be determined"
            return
        fi
        for zone in $zones; do
            if firewall-cmd --quiet --zone="$zone" --query-port="$port/tcp" 2>/dev/null; then
                echo "[run] firewall: TCP $port is allowed by firewalld (zone $zone)"
                return
            fi
        done

        echo "[run] WARNING: firewalld does not allow TCP $port in the active zone(s): $zones"
        if confirm_firewall_change "Open TCP $port with firewalld? (sudo may ask for your password)"; then
            firewall_changed=true
            for zone in $zones; do
                if ! sudo firewall-cmd --zone="$zone" --add-port="$port/tcp" || \
                    ! sudo firewall-cmd --permanent --zone="$zone" --add-port="$port/tcp"; then
                    firewall_changed=false
                    break
                fi
            done
            if [ "$firewall_changed" = true ]; then
                echo "[run] firewall: opened TCP $port with firewalld"
            else
                echo "[run] WARNING: could not add the firewalld rule; remote cabinets may not connect"
            fi
        else
            echo "[run] continuing without changing the firewall; remote cabinets may not connect"
        fi
        return
    fi

    # UFW rule inspection normally needs root. Try without elevation first,
    # then offer one explicit sudo operation when UFW is known to be enabled.
    if command -v ufw >/dev/null 2>&1; then
        ufw_status="$(ufw status 2>/dev/null || true)"
        if [ -z "$ufw_status" ] && command -v sudo >/dev/null 2>&1; then
            ufw_status="$(sudo -n ufw status 2>/dev/null || true)"
        fi
        ufw_enabled=false
        if printf '%s\n' "$ufw_status" | grep -q '^Status: active'; then
            ufw_enabled=true
        elif [ -r /etc/ufw/ufw.conf ] && grep -q '^ENABLED=yes' /etc/ufw/ufw.conf; then
            ufw_enabled=true
        fi

        if [ "$ufw_enabled" = true ]; then
            if ufw_allows_port "$ufw_status"; then
                echo "[run] firewall: TCP $port is allowed by UFW"
            else
                if [ -n "$ufw_status" ]; then
                    prompt="Open TCP $port with UFW? (sudo may ask for your password)"
                else
                    prompt="Check UFW and open TCP $port if needed? (sudo will ask for your password)"
                fi
                echo "[run] WARNING: UFW is active and TCP $port could not be confirmed as allowed"
                if confirm_firewall_change "$prompt"; then
                    if ufw_status="$(sudo ufw status)"; then
                        if ufw_allows_port "$ufw_status"; then
                            echo "[run] firewall: TCP $port is already allowed by UFW"
                        elif sudo ufw allow "$port/tcp" comment 'Zucchini Connector HTTPS'; then
                            echo "[run] firewall: opened TCP $port with UFW"
                        else
                            echo "[run] WARNING: could not add the UFW rule; remote cabinets may not connect"
                        fi
                    else
                        echo "[run] WARNING: could not inspect UFW; remote cabinets may not connect"
                    fi
                else
                    echo "[run] continuing without changing the firewall; remote cabinets may not connect"
                fi
            fi
            return
        fi
    fi

    if command -v nft >/dev/null 2>&1 || command -v iptables >/dev/null 2>&1; then
        echo "[run] firewall: no active UFW/firewalld manager detected; verify that inbound TCP $port is allowed"
    else
        echo "[run] firewall: no supported active Linux firewall detected"
    fi
}

check_firewall() {
    case "$(uname -s)" in
        Linux) check_linux_firewall ;;
        Darwin)
            echo "[run] firewall: macOS manages incoming access per application; allow Python if macOS prompts"
            ;;
    esac
}

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

mkdir -p storage/SONGS/TJA storage/SONGS/OSU storage/SONGS/CONVERTED storage/cabinets

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

check_firewall
echo "[run] web UI: https://localhost:$port/ui"
# tja2fumen is vendored inside app/ and imported as a top-level module.
export PYTHONPATH="$PWD/app${PYTHONPATH:+:$PYTHONPATH}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$port" \
    --ssl-certfile "$cert_dir/server.crt" --ssl-keyfile "$cert_dir/server.key"
