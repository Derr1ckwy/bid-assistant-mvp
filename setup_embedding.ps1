[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipEnvConfig
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $projectRoot "runtime\llama.cpp\llama-server.exe"
$modelDir = Join-Path $projectRoot "models\qwen3-embedding-0.6b"
$modelName = "Qwen3-Embedding-0.6B-Q8_0.gguf"
$modelPath = Join-Path $modelDir $modelName
$partialPath = "$modelPath.part"
$envPath = Join-Path $projectRoot ".env"
$envExamplePath = Join-Path $projectRoot ".env.example"
$modelUrl = "https://modelscope.cn/models/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/master/$modelName"
$modelSha256 = "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"

if (-not (Test-Path -LiteralPath $serverPath)) {
    throw "llama-server.exe is missing. Run .\setup_local_llm.ps1 first."
}

New-Item -ItemType Directory -Force -Path $modelDir | Out-Null

function Test-FileHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() -eq $Expected
}

if ($Force -or -not (Test-FileHash -Path $modelPath -Expected $modelSha256)) {
    if ($Force -and (Test-Path -LiteralPath $partialPath)) {
        Remove-Item -LiteralPath $partialPath -Force
    }
    Write-Host "Downloading Qwen3-Embedding-0.6B Q8_0 from ModelScope..."
    curl.exe -L --fail --retry 20 --retry-all-errors --retry-delay 2 `
        --connect-timeout 20 -C - --output $partialPath $modelUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Embedding model download failed with curl exit code $LASTEXITCODE."
    }
    if (-not (Test-FileHash -Path $partialPath -Expected $modelSha256)) {
        throw "Embedding model SHA-256 verification failed."
    }
    Move-Item -LiteralPath $partialPath -Destination $modelPath -Force
}
else {
    Write-Host "Embedding model already verified."
}

if (-not $SkipEnvConfig) {
    $embeddingSettings = [ordered]@{
        EMBEDDING_BASE_URL = "http://127.0.0.1:11435/v1"
        EMBEDDING_API_KEY = "embedding"
        EMBEDDING_MODEL = "qwen3-embedding:0.6b"
        EMBEDDING_TIMEOUT_SECONDS = "180"
        EMBEDDING_BATCH_SIZE = "16"
        EMBEDDING_QUERY_INSTRUCTION = "Retrieve the most relevant supporting passage for a Chinese construction tender question."
        VECTOR_MIN_FILES = "40"
    }
    if (Test-Path -LiteralPath $envPath) {
        $envLines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    }
    elseif (Test-Path -LiteralPath $envExamplePath) {
        $envLines = @(Get-Content -LiteralPath $envExamplePath -Encoding UTF8)
    }
    else {
        $envLines = @()
    }

    $updatedLines = [Collections.Generic.List[string]]::new()
    $writtenKeys = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($line in $envLines) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=') {
            $key = $Matches[1]
            if ($embeddingSettings.Contains($key)) {
                $updatedLines.Add("$key=$($embeddingSettings[$key])")
                [void]$writtenKeys.Add($key)
                continue
            }
        }
        $updatedLines.Add($line)
    }
    if ($updatedLines.Count -gt 0 -and $updatedLines[$updatedLines.Count - 1].Trim()) {
        $updatedLines.Add("")
    }
    foreach ($key in $embeddingSettings.Keys) {
        if (-not $writtenKeys.Contains($key)) {
            $updatedLines.Add("$key=$($embeddingSettings[$key])")
        }
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines($envPath, [string[]]$updatedLines, $utf8NoBom)
    Write-Host "Local embedding settings were written to $envPath"
}

$item = Get-Item -LiteralPath $modelPath
Write-Host "Embedding model setup completed." -ForegroundColor Green
Write-Host "Model: $modelPath"
Write-Host "Size:  $([math]::Round($item.Length / 1MB, 2)) MiB"
