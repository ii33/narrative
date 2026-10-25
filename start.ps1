param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8501,
    [string]$Database = 'data/graph-projects/launch.sqlite3',
    [switch]$NoBrowser
)

# The Streamlit browser entry was retired on 2026-10-20. Keep this script as a
# compatibility shim so existing shortcuts still open the supported Qt window.
# Port/Database/NoBrowser are accepted but no longer used by the desktop entry.
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    Write-Host 'Streamlit entry retired 2026-10-20. Starting the native Qt desktop instead.'
    & (Join-Path $PSScriptRoot 'start-desktop.ps1')
    exit $LASTEXITCODE
}
catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
