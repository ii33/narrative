#requires -Version 5.1
<#
.SYNOPSIS
    Create or repair the isolated Microsoft GraphRAG environment.

.DESCRIPTION
    The sidecar uses a separate virtual environment so GraphRAG's dependency
    tree never leaks into the application environment. This script:

      1. creates .venv-graphrag with Python 3.13 when missing;
      2. installs the exact versions recorded in requirements-graphrag.lock.txt;
      3. on Windows, raises the venv directory to Medium integrity so the
         CPython venv launcher can read pyvenv.cfg inside a low-integrity
         workspace (otherwise python.exe exits 107);
      4. verifies the installed graphrag version.

    It never installs models, never starts a server, and never writes secrets.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required: https://docs.astral.sh/uv/"
}

$venv = Join-Path $root ".venv-graphrag"
$python = Join-Path $venv "Scripts\python.exe"
$lock = Join-Path $root "requirements-graphrag.lock.txt"

if (-not (Test-Path $python)) {
    Write-Host "Creating $venv"
    uv venv $venv --python 3.13
}

Write-Host "Installing locked GraphRAG dependencies"
uv pip install --python $python -r $lock

if ($env:OS -eq "Windows_NT") {
    # Low-integrity workspaces break the CPython venv launcher with
    # "failed to open pyvenv.cfg" (exit 107). Medium integrity fixes it.
    Write-Host "Setting Medium integrity on $venv"
    icacls $venv /setintegritylevel "(OI)(CI)Medium" /T | Out-Null
}

& $python -c "import importlib.metadata as m; print('graphrag', m.version('graphrag'))"
if ($LASTEXITCODE -ne 0) {
    throw "GraphRAG import check failed"
}
Write-Host "GraphRAG environment ready: $python"
