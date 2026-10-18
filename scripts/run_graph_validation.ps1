param(
    [Parameter(Mandatory = $true)]
    [string]$ValidationScript,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$target = (Resolve-Path -LiteralPath $ValidationScript).Path
$dataRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'data')).Path
if (-not $target.StartsWith($dataRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw '真实验收脚本必须位于项目 data 下的独立结果目录。'
}

Push-Location $projectRoot
try {
    # The SDK exercises only a local fake HTTP server. In a restricted launch it
    # fails before any real-model script is allowed to start.
    $preflightTemp = Join-Path $dataRoot ('graph-validation-preflight-' + [guid]::NewGuid().ToString('N'))
    & $python -m pytest tests/test_graph_agent.py -q -k real_sdk --basetemp $preflightTemp
    if ($LASTEXITCODE -ne 0) {
        throw '本机 SDK 回环预检未通过；未启动真实模型验收。请在获准的网络执行环境重跑整条命令。'
    }
    if ($PreflightOnly) {
        Write-Output '本机 SDK 回环预检通过；未启动真实模型。'
        return
    }
    $previousPreflightMarker = $env:GRAPH_VALIDATION_PREFLIGHT_OK
    try {
        $env:GRAPH_VALIDATION_PREFLIGHT_OK = '1'
        & $python $target
    }
    finally {
        $env:GRAPH_VALIDATION_PREFLIGHT_OK = $previousPreflightMarker
    }
    if ($LASTEXITCODE -ne 0) {
        throw '真实验收脚本失败；不得自动重试模型请求。'
    }
}
finally {
    Pop-Location
}
