param(
    [string]$ProjectsDir = 'data/graph-projects',
    [string]$Project = '',
    [double]$QaExitAfterSeconds = 0,
    [string]$RenderLog = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw 'Desktop dependencies are missing. Run uv sync --extra desktop in the project directory.'
}
$projectsPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $ProjectsDir))
$arguments = @('-m', 'narrative.native_app', '--projects-dir', ('"' + $projectsPath + '"'))
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
