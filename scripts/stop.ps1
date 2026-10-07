[CmdletBinding()]
param(
    [switch]$Minimal,
    [switch]$Destroy,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
$RootDir = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RootDir 'docker-compose.yml'

function Show-Usage {
    Write-Host @"
OryaSecurity - stop the project stack

Usage: .\stop.ps1 [options]

Options:
  -Minimal    Stop only core services (postgres, redis, scanner).
              Default: stop the full stack (UI, Prometheus, Grafana).
  -Destroy    Also remove data volumes (postgres, redis, grafana data).
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

$ComposeArgs = @('-f', $ComposeFile)
if (-not $Minimal) {
    $ComposeArgs += @('--profile', 'ui', '--profile', 'observability')
}

$DownArgs = @('down')
if ($Destroy) {
    $DownArgs += '-v'
}

Write-Host ("[stop] Stopping services (docker compose {0})..." -f ($DownArgs -join ' '))
& docker compose @ComposeArgs @DownArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: docker compose down failed." -ForegroundColor Red
    exit 1
}

if ($Destroy) {
    Write-Host "[stop] Services stopped, data volumes removed."
}
else {
    Write-Host "[stop] Services stopped, data volumes preserved (use -Destroy to remove)."
}
exit 0
