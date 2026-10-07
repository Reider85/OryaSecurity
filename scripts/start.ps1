[CmdletBinding()]
param(
    [switch]$Minimal,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
$RootDir = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RootDir 'docker-compose.yml'

function Show-Usage {
    Write-Host @"
OryaSecurity - start the project stack

Usage: .\start.ps1 [options]

Options:
  -Minimal    Start only core services (postgres, redis, scanner).
              Default: full stack (UI, Prometheus, Grafana).
  -Help       Show this help.
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

# --- prerequisites -----------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: docker not found. Install Docker Desktop / Docker Engine." -ForegroundColor Red
    exit 1
}
$null = docker compose version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: docker compose plugin not available (need Docker Compose v2)." -ForegroundColor Red
    exit 1
}

# --- .env --------------------------------------------------------------------
$EnvFile = Join-Path $RootDir '.env'
$EnvExample = Join-Path $RootDir '.env.example'
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "[start] Created .env from .env.example"
}

$ComposeArgs = @('-f', $ComposeFile)
if (-not $Minimal) {
    $ComposeArgs += @('--profile', 'ui', '--profile', 'observability')
}

function Show-ComposeDiagnostics {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "--- docker compose ps ---"
    $null = & docker compose @ComposeArgs ps 2>&1
    Write-Host "--- last 50 log lines ---"
    $null = & docker compose @ComposeArgs logs --tail=50 2>&1
}

function Wait-ForUrl {
    param([string]$Url, [string]$Name, [int]$TimeoutSec)
    $elapsed = 0
    Write-Host -NoNewline ("Waiting for {0,-11} ({1}) " -f $Name, $Url)
    while ($elapsed -lt $TimeoutSec) {
        try {
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            Write-Host ("OK ({0}s)" -f $elapsed)
            return $true
        }
        catch {
            # not ready yet
        }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 3
        $elapsed += 3
    }
    Write-Host " TIMEOUT"
    return $false
}

# --- up ----------------------------------------------------------------------
Write-Host "[start] Building and starting services (docker compose up -d --build)..."
& docker compose @ComposeArgs up -d --build
if ($LASTEXITCODE -ne 0) {
    Show-ComposeDiagnostics "docker compose up failed (exit code $LASTEXITCODE)."
    exit 1
}

# --- health checks -----------------------------------------------------------
if (-not (Wait-ForUrl "http://localhost:8000/health" "scanner" 90)) {
    Show-ComposeDiagnostics "scanner did not become healthy within 90s."
    exit 1
}
if (-not $Minimal) {
    if (-not (Wait-ForUrl "http://localhost:3000/" "ui" 120)) {
        Show-ComposeDiagnostics "ui did not become healthy within 120s."
        exit 1
    }
    if (-not (Wait-ForUrl "http://localhost:9090/" "prometheus" 60)) {
        Show-ComposeDiagnostics "prometheus did not become healthy within 60s."
        exit 1
    }
    if (-not (Wait-ForUrl "http://localhost:3001/" "grafana" 60)) {
        Show-ComposeDiagnostics "grafana did not become healthy within 60s."
        exit 1
    }
}

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host "[start] Stack is up:"
Write-Host "  Scanner (API): http://localhost:8000  (/health, /docs, /metrics)"
if (-not $Minimal) {
    Write-Host "  UI:            http://localhost:3000"
    Write-Host "  Prometheus:    http://localhost:9090"
    Write-Host "  Grafana:       http://localhost:3001  (admin/admin)"
}
exit 0
