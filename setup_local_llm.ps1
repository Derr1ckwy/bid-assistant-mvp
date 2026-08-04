[CmdletBinding()]
param(
    [string]$RuntimeMirror = "https://ghfast.top/https://github.com",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeVersion = "b10256"
$runtimeArchiveName = "llama-$runtimeVersion-bin-win-vulkan-x64.zip"
$runtimeUrl = "$RuntimeMirror/ggml-org/llama.cpp/releases/download/$runtimeVersion/$runtimeArchiveName"
$runtimeSha256 = "ea787c151309a80b908809a04b6f71d44da41de0ea4b2794114567b67df861f6"

$modelName = "Qwen3-4B-Q4_K_M.gguf"
$modelUrl = "https://modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/master/$modelName"
$modelSha256 = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"

$downloadDir = Join-Path $projectRoot "runtime\downloads"
$runtimeDir = Join-Path $projectRoot "runtime\llama.cpp"
$modelDir = Join-Path $projectRoot "models\qwen3-4b"
$runtimeArchive = Join-Path $downloadDir $runtimeArchiveName
$runtimePartial = "$runtimeArchive.part"
$modelPath = Join-Path $modelDir $modelName

New-Item -ItemType Directory -Force -Path $downloadDir, $runtimeDir, $modelDir | Out-Null

function Test-FileHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }

    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    return $actual -eq $Expected
}

function Invoke-ResumableDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $curl = Get-Command curl.exe -ErrorAction Stop
    & $curl.Source -L --fail --retry 20 --retry-all-errors --retry-delay 2 `
        --connect-timeout 20 -C - --output $Destination $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed with curl exit code ${LASTEXITCODE}: $Url"
    }
}

if ($Force -or -not (Test-FileHash -Path $runtimeArchive -Expected $runtimeSha256)) {
    if (Test-Path -LiteralPath $runtimeArchive) {
        Remove-Item -LiteralPath $runtimeArchive -Force
    }
    if ((Test-Path -LiteralPath $runtimePartial) -and
        ((Get-Item -LiteralPath $runtimePartial).Length -gt 34127269)) {
        Remove-Item -LiteralPath $runtimePartial -Force
    }
    Write-Host "Downloading llama.cpp Windows Vulkan runtime..."
    Invoke-ResumableDownload -Url $runtimeUrl -Destination $runtimePartial
    if (-not (Test-FileHash -Path $runtimePartial -Expected $runtimeSha256)) {
        throw "llama.cpp runtime SHA-256 verification failed."
    }
    Move-Item -LiteralPath $runtimePartial -Destination $runtimeArchive -Force
}
else {
    Write-Host "llama.cpp runtime already verified."
}

Expand-Archive -LiteralPath $runtimeArchive -DestinationPath $runtimeDir -Force
if (-not (Test-Path -LiteralPath (Join-Path $runtimeDir "llama-server.exe"))) {
    throw "llama-server.exe was not found after extraction."
}

if ($Force -or -not (Test-FileHash -Path $modelPath -Expected $modelSha256)) {
    $partialModel = "$modelPath.part"
    if ($Force -and (Test-Path -LiteralPath $partialModel)) {
        Remove-Item -LiteralPath $partialModel -Force
    }
    if ((Test-Path -LiteralPath $partialModel) -and
        ((Get-Item -LiteralPath $partialModel).Length -gt 2497280256)) {
        Remove-Item -LiteralPath $partialModel -Force
    }
    Write-Host "Downloading Qwen3-4B Q4_K_M from ModelScope..."
    Invoke-ResumableDownload -Url $modelUrl -Destination $partialModel
    if (-not (Test-FileHash -Path $partialModel -Expected $modelSha256)) {
        throw "Qwen3 model SHA-256 verification failed."
    }
    Move-Item -LiteralPath $partialModel -Destination $modelPath -Force
}
else {
    Write-Host "Qwen3 model already verified."
}

Write-Host "Local model setup completed."
Write-Host "Runtime: $runtimeDir"
Write-Host "Model:   $modelPath"
