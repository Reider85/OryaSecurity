[CmdletBinding()]
param(
    [switch]$Minimal,
    [switch]$Destroy,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
    Write-Host @"
OryaSecurity - restart the project stack (stop + start)

Usage: .\restart.ps1 [options]

Options:
  -Minimal    Restart only core services (postgres, redis, scanner).
              Default: full stack (UI, Prometheus, Grafana).
  -Destroy    On stop phase also remove data volumes.
  -Help       Show this help.
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

$StopScript = Join-Path $PSScriptRoot 'stop.ps1'
$StartScript = Join-Path $PSScriptRoot 'start.ps1'

$StopArgs = @()
$StartArgs = @()
if ($Minimal) {
    $StopArgs += '-Minimal'
    $StartArgs += '-Minimal'
}
if ($Destroy) {
    $StopArgs += '-Destroy'
}

Write-Host "[restart] Stopping..."
& $StopScript @StopArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "[restart] Starting..."
& $StartScript @StartArgs
exit $LASTEXITCODE
