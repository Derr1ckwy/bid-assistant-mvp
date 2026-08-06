[CmdletBinding()]
param(
    [int]$Port = 11435,
    [int]$ContextSize = 8192,
    [int]$GpuLayers = 99,
    [string]$Device = "Vulkan0",
    [string]$ApiKey = "embedding",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $projectRoot "runtime\llama.cpp\llama-server.exe"
$modelPath = Join-Path $projectRoot "models\qwen3-embedding-0.6b\Qwen3-Embedding-0.6B-Q8_0.gguf"
$pidPath = Join-Path $projectRoot ".embedding-server.pid"
$logDir = Join-Path $projectRoot "runtime\logs"
$stdoutLog = Join-Path $logDir "embedding-server.out.log"
$stderrLog = Join-Path $logDir "embedding-server.err.log"
$baseUrl = "http://127.0.0.1:$Port/v1"
$modelAlias = "qwen3-embedding:0.6b"

if (-not (Test-Path -LiteralPath $serverPath)) {
    throw "llama-server.exe is missing. Run .\setup_local_llm.ps1 first."
}
if (-not (Test-Path -LiteralPath $modelPath)) {
    throw "Embedding model is missing. Run .\setup_embedding.ps1 first."
}

function Test-EmbeddingServer {
    try {
        $headers = @{ Authorization = "Bearer $ApiKey" }
        $body = @{ model = $modelAlias; input = @("health check") } | ConvertTo-Json -Depth 3
        $response = Invoke-RestMethod -Method Post -Uri "$baseUrl/embeddings" `
            -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 8
        return $response.data.Count -eq 1 -and $response.data[0].embedding.Count -gt 0
    }
    catch {
        return $false
    }
}

if (Test-EmbeddingServer) {
    Write-Host "Embedding service is already ready at $baseUrl"
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
    "--alias", $modelAlias,
    "--ctx-size", "$ContextSize",
    "--batch-size", "8192",
    "--ubatch-size", "8192",
    "--gpu-layers", "$GpuLayers",
    "--device", $Device,
    "--flash-attn", "on",
    "--embeddings",
    "--pooling", "last",
    "--api-key", $ApiKey,
    "--cors-origins", "http://127.0.0.1:8501,http://localhost:8501"
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
            $detail = (Get-Content -LiteralPath $stderrLog -Tail 30 -ErrorAction SilentlyContinue) -join "`n"
        }
        throw "Embedding server exited before becoming ready.`n$detail"
    }
    if (Test-EmbeddingServer) {
        Write-Host "Embedding service is ready: $baseUrl"
        Write-Host "PID: $($process.Id)"
        return
    }
    Start-Sleep -Seconds 2
}

throw "Embedding server did not become ready within 120 seconds. Check $stderrLog"
