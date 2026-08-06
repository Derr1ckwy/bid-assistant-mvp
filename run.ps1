$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "未找到项目虚拟环境。请先在项目目录运行：python -m venv .venv"
}

Set-Location -LiteralPath $projectRoot

$envPath = Join-Path $projectRoot ".env"
$baseUrl = "http://localhost:11434/v1"
if (Test-Path -LiteralPath $envPath) {
    $baseUrlLine = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^LLM_BASE_URL=' } |
        Select-Object -First 1
    if ($baseUrlLine) {
        $baseUrl = ($baseUrlLine -split '=', 2)[1].Trim('"')
    }
}

if ($baseUrl -match 'localhost:11434|127\.0\.0\.1:11434') {
    $ollamaListening = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
    if (-not $ollamaListening) {
        $localStarter = Join-Path $projectRoot "start_local_llm.ps1"
        $localRuntime = Join-Path $projectRoot "runtime\llama.cpp\llama-server.exe"
        $localModel = Join-Path $projectRoot "models\qwen3-4b\Qwen3-4B-Q4_K_M.gguf"

        if ((Test-Path -LiteralPath $localStarter) -and
            (Test-Path -LiteralPath $localRuntime) -and
            (Test-Path -LiteralPath $localModel)) {
            try {
                & $localStarter
            }
            catch {
                Write-Warning "Local Qwen service did not start: $($_.Exception.Message)"
            }
        }
        else {
            $ollama = Get-Command ollama -ErrorAction SilentlyContinue
            if ($ollama) {
                Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
            }
        }
    }
}

$embeddingBaseUrl = ""
if (Test-Path -LiteralPath $envPath) {
    $embeddingBaseUrlLine = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^EMBEDDING_BASE_URL=' } |
        Select-Object -First 1
    if ($embeddingBaseUrlLine) {
        $embeddingBaseUrl = ($embeddingBaseUrlLine -split '=', 2)[1].Trim('"')
    }
}

if ($embeddingBaseUrl -match 'localhost:11435|127\.0\.0\.1:11435') {
    $embeddingListening = Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue
    if (-not $embeddingListening) {
        $embeddingStarter = Join-Path $projectRoot "start_embedding.ps1"
        $embeddingModel = Join-Path $projectRoot "models\qwen3-embedding-0.6b\Qwen3-Embedding-0.6B-Q8_0.gguf"
        if ((Test-Path -LiteralPath $embeddingStarter) -and (Test-Path -LiteralPath $embeddingModel)) {
            try {
                & $embeddingStarter
            }
            catch {
                Write-Warning "Embedding service did not start: $($_.Exception.Message)"
            }
        }
    }
}

& $pythonPath -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
