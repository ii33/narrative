param(
    [string]$Project = '',
    [double]$QaExitAfterSeconds = 0,
    [string]$RenderLog = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtime = Join-Path $projectRoot '.runtime\python'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$config = Join-Path $projectRoot '.venv\pyvenv.cfg'
if (-not (Test-Path -LiteralPath $pythonw) -or
    -not (Test-Path -LiteralPath $python) -or
    -not (Test-Path -LiteralPath $config) -or
    -not (Test-Path -LiteralPath (Join-Path $runtime 'python.exe')) -or
    -not (Test-Path -LiteralPath (Join-Path $runtime 'Lib\encodings'))) {
    throw 'Project runtime is incomplete. Prepare .venv and .runtime\python with scripts\localize_python.py before starting. No system Python or uv fallback is used.'
}
# A moved project must point its venv at the copy beside it, never the old host.
$configLines = @(Get-Content -LiteralPath $config | Where-Object { $_ -notmatch '^(home|executable|command)\s*=' })
$localConfig = @('home = ' + $runtime) + $configLines
$configText = ($localConfig -join "`n") + "`n"
if ([IO.File]::ReadAllText($config) -ne $configText) {
    [IO.File]::WriteAllText($config, $configText, (New-Object Text.UTF8Encoding($false)))
}
$probe = & $python -I -c 'import pathlib,sys,sysconfig,narrative; root=pathlib.Path(sys.argv[1]).resolve(); assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in (sys.prefix,sys.base_prefix,sysconfig.get_path("stdlib"),narrative.__file__)), "Python environment escapes the project"' $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Project Python isolation check failed.'
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
