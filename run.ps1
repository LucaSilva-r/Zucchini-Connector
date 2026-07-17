# Run the connector directly, no Docker (Windows).
# Creates a virtualenv, installs dependencies, generates a self-signed TLS
# certificate on first run, and serves on https://0.0.0.0:8443.
#
# Requirements: Python 3.10+, openssl (ships with Git for Windows). For song
# conversion additionally: ffmpeg and storage\ps3_at3tool.exe (runs natively,
# no Wine needed on Windows). The UI and cabinet management work without the
# conversion tools.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }

if (-not (Test-Path .venv)) {
    Write-Host "[run] creating virtualenv..."
    & $python -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install -q -r requirements.txt

if (-not (Test-Path "app\static\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "[run] building web UI..."
        Push-Location frontend
        npm ci; npm run build
        Pop-Location
    } else {
        throw "app\static is missing and npm is not installed to build it"
    }
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "[run] WARNING: ffmpeg not found - song conversion will fail"
}
if (-not (Test-Path "storage\ps3_at3tool.exe")) {
    Write-Host "[run] WARNING: storage\ps3_at3tool.exe missing - song conversion will fail"
}

foreach ($d in "storage\ESE", "storage\OSU", "storage\ESE-convert", "storage\cabinets") {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$certDir = "storage\certificates\local"
$crt = Join-Path $certDir "server.crt"
$key = Join-Path $certDir "server.key"
if (-not ((Test-Path $crt) -and (Test-Path $key))) {
    if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
        throw "openssl is required to create the TLS certificate (install Git for Windows or OpenSSL)"
    }
    Write-Host "[run] generating self-signed TLS certificate..."
    New-Item -ItemType Directory -Force -Path $certDir | Out-Null
    $cn = if ($env:CONNECTOR_TLS_CN) { $env:CONNECTOR_TLS_CN } else { "connector.local" }
    openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 `
        -subj "/CN=$cn" `
        -addext "subjectAltName=DNS:$cn,DNS:localhost,IP:127.0.0.1" `
        -keyout $key -out $crt
}

$port = if ($env:CONNECTOR_HTTPS_PORT) { $env:CONNECTOR_HTTPS_PORT } else { "8443" }
Write-Host "[run] web UI: https://localhost:$port/ui"
# tja2fumen is vendored inside app\ and imported as a top-level module.
$env:PYTHONPATH = (Join-Path $PSScriptRoot "app") + $(if ($env:PYTHONPATH) { ";" + $env:PYTHONPATH } else { "" })
& $venvPy -m uvicorn app.main:app --host 0.0.0.0 --port $port `
    --ssl-certfile $crt --ssl-keyfile $key
