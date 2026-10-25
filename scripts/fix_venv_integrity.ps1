#requires -Version 5.1
<#
.SYNOPSIS
    Raise virtual environments to Medium integrity on Windows.

.DESCRIPTION
    A workspace created at Low integrity makes CPython's venv launcher fail
    with "failed to open pyvenv.cfg" (exit 107), and uv trampolines fail with
    "permission denied". Medium integrity on the venv directory fixes both.
    This changes ACL integrity labels only; it does not change file ownership
    or read/write permissions.
#>
param(
    [string[]]$Paths = @(".venv", ".venv-graphrag")
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($env:OS -ne "Windows_NT") {
    Write-Host "Not Windows; nothing to do."
    exit 0
}

foreach ($path in $Paths) {
    if (-not (Test-Path $path)) {
        Write-Host "Skip missing: $path"
        continue
    }
    Write-Host "Setting Medium integrity: $path"
    icacls $path /setintegritylevel "(OI)(CI)Medium" /T | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed for $path"
    }
}
Write-Host "Done. Verify with: .venv\Scripts\python.exe -V"
