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

$portText = if ($env:CONNECTOR_HTTPS_PORT) { $env:CONNECTOR_HTTPS_PORT } else { "8443" }
$port = 0
if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    throw "CONNECTOR_HTTPS_PORT must be a number from 1 to 65535"
}

function Test-LocalPortSpec {
    param([object]$Spec, [int]$Port)

    foreach ($part in @($Spec)) {
        $text = [string]$part
        if ($text -eq "Any" -or $text -eq [string]$Port) { return $true }
        if ($text -match '^(\d+)-(\d+)$' -and $Port -ge [int]$Matches[1] -and $Port -le [int]$Matches[2]) {
            return $true
        }
    }
    return $false
}

function Test-InteractiveConsole {
    return [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Add-ConnectorFirewallRule {
    param([int]$Port)

    $displayName = "Zucchini Connector HTTPS ($Port)"
    $command = "New-NetFirewallRule -DisplayName '$displayName' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Domain | Out-Null"
    if (Test-IsAdministrator) {
        New-NetFirewallRule -DisplayName $displayName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -Profile Private,Domain | Out-Null
    } else {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru `
            -ArgumentList "-NoProfile", "-EncodedCommand", $encoded
        if ($process.ExitCode -ne 0) {
            throw "the elevated firewall command exited with code $($process.ExitCode)"
        }
    }
}

function Check-ConnectorFirewall {
    param([int]$Port)

    if ($env:CONNECTOR_FIREWALL -eq "0") {
        Write-Host "[run] firewall check skipped (CONNECTOR_FIREWALL=0)"
        return
    }
    if (-not (Get-Command Get-NetFirewallProfile -ErrorAction SilentlyContinue)) {
        Write-Host "[run] WARNING: Windows Firewall tools are unavailable; verify that inbound TCP $Port is allowed"
        return
    }

    try {
        $enabledProfiles = @(Get-NetFirewallProfile -ErrorAction Stop | Where-Object Enabled)
        if ($enabledProfiles.Count -eq 0) {
            Write-Host "[run] firewall: Windows Defender Firewall is disabled"
            return
        }
        $publicNetworks = @(
            Get-NetConnectionProfile -ErrorAction SilentlyContinue |
                Where-Object NetworkCategory -eq "Public"
        )
        if ($publicNetworks.Count -gt 0) {
            Write-Host "[run] WARNING: an active Windows network is Public; automatic rules only cover trusted Private/Domain networks"
        }

        $allowed = $false
        $rules = Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow -ErrorAction Stop
        foreach ($rule in $rules) {
            $applicationFilters = @($rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue)
            $allowsAnyApplication = $applicationFilters.Count -eq 0 -or @(
                $applicationFilters | Where-Object Program -eq "Any"
            ).Count -gt 0
            if (-not $allowsAnyApplication) { continue }
            foreach ($filter in @($rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue)) {
                if (($filter.Protocol -eq "TCP" -or $filter.Protocol -eq 6) -and (Test-LocalPortSpec $filter.LocalPort $Port)) {
                    $allowed = $true
                    break
                }
            }
            if ($allowed) { break }
        }
        if ($allowed) {
            Write-Host "[run] firewall: found a Windows Defender Firewall allow rule for inbound TCP $Port"
            return
        }
    } catch {
        Write-Host "[run] WARNING: could not inspect Windows Defender Firewall: $($_.Exception.Message)"
        return
    }

    Write-Host "[run] WARNING: no Windows Defender Firewall allow rule was found for inbound TCP $Port"
    if (-not (Test-InteractiveConsole)) {
        Write-Host "[run] non-interactive session; continuing without changing the firewall"
        return
    }

    $answer = Read-Host "[run] Open TCP $Port on Private and Domain networks? (administrator approval required) [y/N]"
    if ($answer -notmatch '^(?i:y|yes)$') {
        Write-Host "[run] continuing without changing the firewall; remote cabinets may not connect"
        return
    }
    try {
        Add-ConnectorFirewallRule -Port $Port
        Write-Host "[run] firewall: opened TCP $Port on Private and Domain networks"
    } catch {
        Write-Host "[run] WARNING: could not add the firewall rule: $($_.Exception.Message)"
        Write-Host "[run] continuing; remote cabinets may not connect"
    }
}

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

foreach ($d in "storage\SONGS\TJA", "storage\SONGS\OSU", "storage\SONGS\CONVERTED", "storage\cabinets") {
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

Check-ConnectorFirewall -Port $port
Write-Host "[run] web UI: https://localhost:$port/ui"
# tja2fumen is vendored inside app\ and imported as a top-level module.
$env:PYTHONPATH = (Join-Path $PSScriptRoot "app") + $(if ($env:PYTHONPATH) { ";" + $env:PYTHONPATH } else { "" })
& $venvPy -m uvicorn app.main:app --host 0.0.0.0 --port $port `
    --ssl-certfile $crt --ssl-keyfile $key
