[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidPath = Join-Path $projectRoot ".embedding-server.pid"
$expectedExecutable = Join-Path $projectRoot "runtime\llama.cpp\llama-server.exe"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "No managed embedding server PID was found."
    return
}

$serverPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
if (-not $processInfo) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Host "The recorded embedding server is no longer running."
    return
}

if ($processInfo.ExecutablePath -ne $expectedExecutable) {
    throw "PID $serverPid does not belong to the managed llama-server process."
}

Stop-Process -Id $serverPid -Force
Remove-Item -LiteralPath $pidPath -Force
Write-Host "Embedding server stopped."
