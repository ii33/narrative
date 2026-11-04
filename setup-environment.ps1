$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv was not found. Install uv and reopen the terminal before running setup.'
}
Push-Location -LiteralPath $projectRoot
try {
    & uv sync --locked --all-packages --all-extras --all-groups
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit code $LASTEXITCODE)." }
    & (Join-Path $projectRoot 'start-desktop.ps1') -PrepareOnly
} finally {
    Pop-Location
}
