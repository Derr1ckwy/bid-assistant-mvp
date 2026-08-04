[CmdletBinding()]
param(
    [int]$Port = 11434,
    [int]$ContextSize = 16384,
    [int]$GpuLayers = 99,
    [string]$Device = "Vulkan0",
    [string]$ApiKey = "ollama",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $projectRoot "runtime\llama.cpp\llama-server.exe"
$modelPath = Join-Path $projectRoot "models\qwen3-4b\Qwen3-4B-Q4_K_M.gguf"
$pidPath = Join-Path $projectRoot ".llama-server.pid"
$logDir = Join-Path $projectRoot "runtime\logs"
$stdoutLog = Join-Path $logDir "llama-server.out.log"
$stderrLog = Join-Path $logDir "llama-server.err.log"
$healthUrl = "http://127.0.0.1:$Port/v1/models"

if (-not (Test-Path -LiteralPath $serverPath)) {
    throw "llama-server.exe is missing. Run .\setup_local_llm.ps1 first."
}
if (-not (Test-Path -LiteralPath $modelPath)) {
    throw "Qwen3 model is missing. Run .\setup_local_llm.ps1 first."
}

function Test-LocalModelServer {
    try {
        $headers = @{ Authorization = "Bearer $ApiKey" }
        $response = Invoke-RestMethod -Method Get -Uri $healthUrl -Headers $headers -TimeoutSec 3
        return $null -ne $response.data
    }
    catch {
        return $false
    }
}

if (Test-LocalModelServer) {
    Write-Host "Local model server is already ready at $healthUrl"
    return
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already occupied by another service."
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$serverArguments = @(
    "--model", $modelPath,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--alias", "qwen3:4b",
    "--ctx-size", "$ContextSize",
    "--gpu-layers", "$GpuLayers",
    "--device", $Device,
    "--flash-attn", "on",
    "--reasoning", "off",
    "--api-key", $ApiKey,
    "--cors-origins", "http://127.0.0.1:8501,http://localhost:8501",
    "--jinja"
)

if ($Foreground) {
    & $serverPath @serverArguments
    exit $LASTEXITCODE
}

$process = Start-Process -FilePath $serverPath `
    -ArgumentList $serverArguments `
    -WorkingDirectory (Split-Path -Parent $serverPath) `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
        $detail = ""
        if (Test-Path -LiteralPath $stderrLog) {
            $detail = (Get-Content -LiteralPath $stderrLog -Tail 20 -ErrorAction SilentlyContinue) -join "`n"
        }
        throw "llama-server exited before becoming ready.`n$detail"
    }
    if (Test-LocalModelServer) {
        Write-Host "Local Qwen3 service is ready: $healthUrl"
        Write-Host "PID: $($process.Id)"
        return
    }
    Start-Sleep -Seconds 2
}

throw "llama-server did not become ready within 120 seconds. Check $stderrLog"
