param(
    [string]$Project = '',
    [double]$QaExitAfterSeconds = 0,
    [string]$RenderLog = '',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtime = Join-Path $projectRoot '.runtime\python'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$config = Join-Path $projectRoot '.venv\pyvenv.cfg'
if (-not (Test-Path -LiteralPath $pythonw) -or
    -not (Test-Path -LiteralPath $python) -or
    -not (Test-Path -LiteralPath $config)) {
    throw 'Project virtual environment is incomplete. Run uv sync --locked --all-extras before starting.'
}
if (-not (Test-Path -LiteralPath $runtime)) {
    Write-Output 'Preparing the project-local Python runtime for first launch.'
    # uv launchers may embed the original interpreter and remain locked while
    # running. Query it first, then prepare from a separate base interpreter.
    $sourceJson = & $python -I -c 'import json,sys; print(json.dumps(sys.base_prefix))'
    if ($LASTEXITCODE -ne 0) { throw 'Could not locate the virtual environment base Python.' }
    $source = $sourceJson | ConvertFrom-Json
    $sourcePython = Join-Path $source 'python.exe'
    & $sourcePython -I (Join-Path $projectRoot 'scripts\localize_python.py') --source $source
    if ($LASTEXITCODE -ne 0) {
        throw 'Project runtime preparation failed. See the diagnostic above.'
    }
}
if (
    -not (Test-Path -LiteralPath (Join-Path $runtime 'python.exe')) -or
    -not (Test-Path -LiteralPath (Join-Path $runtime 'Lib\encodings'))) {
    throw 'Project runtime is incomplete. Inspect .runtime\python; if it is a failed new installation, move that directory aside and rerun setup-environment.cmd.'
}
# Existing local runtimes may accompany a newly recreated uv environment.
# Never mix redirectors for a different interpreter version.
$runtimePython = Join-Path $runtime 'python.exe'
$runtimeVersion = & $runtimePython -I -c 'import sys; print(sys.version_info.major,sys.version_info.minor,sep=chr(46))'
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the project-local Python runtime.' }
$versionLine = Get-Content -LiteralPath $config | Where-Object { $_ -match '^\s*(version_info|version)\s*=\s*(\d+)\.(\d+)' } | Select-Object -First 1
if (-not $versionLine -or $versionLine -notmatch '^\s*(version_info|version)\s*=\s*(\d+)\.(\d+)' -or "$($Matches[2]).$($Matches[3])" -ne $runtimeVersion) {
    throw 'The virtual environment and local Python runtime versions do not match. Inspect them before rebuilding the runtime.'
}
$needsRepair = $false
$hasher = [Security.Cryptography.SHA256]::Create()
foreach ($pair in @(@('venvlauncher.exe', 'python.exe'), @('venvwlauncher.exe', 'pythonw.exe'))) {
    $redirector = Join-Path $runtime ("Lib\venv\scripts\nt\" + $pair[0])
    $installed = Join-Path $projectRoot (".venv\Scripts\" + $pair[1])
    if (-not (Test-Path -LiteralPath $redirector)) { throw "Missing CPython redirector: $redirector" }
    $expectedHash = [Convert]::ToBase64String($hasher.ComputeHash([IO.File]::ReadAllBytes($redirector)))
    $actualHash = [Convert]::ToBase64String($hasher.ComputeHash([IO.File]::ReadAllBytes($installed)))
    if ($expectedHash -ne $actualHash) { $needsRepair = $true }
}
$hasher.Dispose()
if ($needsRepair) {
    $identityCode = "import json,sys,struct,platform,sysconfig; print(json.dumps([list(sys.version_info[:3]),sys.implementation.cache_tag,struct.calcsize(chr(80)),platform.machine(),sysconfig.get_config_var('Py_GIL_DISABLED')]))"
    $runtimeIdentity = & $runtimePython -I -c $identityCode
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the local Python runtime identity.' }
    try {
        $venvIdentity = & $python -I -c $identityCode 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Interpreter could not run.' }
    } catch {
        throw 'Cannot verify the existing virtual environment interpreter. Recreate .venv with uv and rerun environment setup.'
    }
    if (($venvIdentity -join '') -ne ($runtimeIdentity -join '')) {
        throw 'The virtual environment and local runtime differ in Python version, ABI, or architecture. Inspect the existing runtime before rebuilding it.'
    }
    Write-Output 'Repairing virtual environment launchers for the project-local Python runtime.'
    & $runtimePython -I (Join-Path $projectRoot 'scripts\localize_python.py') --source $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Project runtime preparation failed. See the diagnostic above.' }
}
# A moved project must point its venv at the copy beside it, never the old host.
$configLines = @(Get-Content -LiteralPath $config | Where-Object { $_ -notmatch '^(home|executable|command)\s*=' })
$localConfig = @('home = ' + $runtime) + $configLines
$configText = ($localConfig -join "`n") + "`n"
if ([IO.File]::ReadAllText($config) -ne $configText) {
    [IO.File]::WriteAllText($config, $configText, (New-Object Text.UTF8Encoding($false)))
}
& $python -I (Join-Path $projectRoot 'scripts\check_runtime.py')
if ($LASTEXITCODE -ne 0) {
    throw 'Project startup checks failed. See the diagnostic above.'
}
if ($PrepareOnly) {
    Write-Output 'Project environment is ready.'
    return
}
$arguments = @('-I', '-m', 'narrative.native_app')
if ($Project) {
    $arguments += @('--project', ('"' + $Project + '"'))
}
if ($QaExitAfterSeconds -gt 0) {
    $arguments += @('--qa-exit-after-seconds', $QaExitAfterSeconds.ToString([cultureinfo]::InvariantCulture))
}
if ($RenderLog) {
    $renderLogPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $RenderLog))
    $arguments += @('--render-log', ('"' + $renderLogPath + '"'))
}
# pythonw hosts the requested visible interactive Qt window without a console.
Write-Output "Starting native desktop from $projectRoot."
$desktopProcess = Start-Process -FilePath $pythonw -WorkingDirectory $projectRoot -ArgumentList $arguments -NoNewWindow -PassThru
Write-Output "Native desktop started (PID $($desktopProcess.Id))."
if ($QaExitAfterSeconds -gt 0) {
    $waitMilliseconds = [int](($QaExitAfterSeconds + 15) * 1000)
    if (-not $desktopProcess.WaitForExit($waitMilliseconds)) {
        throw 'Timed desktop QA did not exit after the requested duration.'
    }
    Write-Output "Native desktop QA exited (code $($desktopProcess.ExitCode))."
}
