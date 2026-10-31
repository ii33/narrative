param(
    [string]$Project = '',
    [double]$QaExitAfterSeconds = 0,
    [string]$RenderLog = ''
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot 'start-desktop.ps1') @PSBoundParameters
    exit $LASTEXITCODE
}
catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
