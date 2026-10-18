param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8501,
    [string]$Database = 'data/graph-projects/launch.sqlite3',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$narrativeExitCode = 1
$previousHeadless = $env:STREAMLIT_SERVER_HEADLESS
Push-Location -LiteralPath $PSScriptRoot
try {
    # Streamlit opens the browser after the local server is ready.
    $env:STREAMLIT_SERVER_HEADLESS = if ($NoBrowser) { 'true' } else { 'false' }
    Write-Host "Narrative workbench: http://127.0.0.1:$Port"
    Write-Host 'Keep this window open. Press Ctrl+C to stop.'
    $projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $projectPython) {
        & $projectPython -m narrative.cli --db $Database ui --port $Port
    }
    elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        # uv prepares the project environment on the first run.
        & uv run narrative --db $Database ui --port $Port
    }
    else {
        throw 'Project environment missing. Install uv, then run uv sync in this folder.'
    }
    $narrativeExitCode = $LASTEXITCODE
}
catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    $env:STREAMLIT_SERVER_HEADLESS = $previousHeadless
    Pop-Location
}
exit $narrativeExitCode
