param(
    [string]$PythonVersion = "3.12",
    [switch]$Recreate,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".mineru-venv"
$ModelRoot = Join-Path $ProjectDir ".mineru-models"
$ModelCache = Join-Path $ModelRoot "modelscope"
$ModelConfig = Join-Path $ModelRoot "mineru.json"
$ProjectUv = Join-Path $ProjectDir ".venv\Scripts\uv.exe"
$RuntimeRoot = Join-Path $env:LOCALAPPDATA "BidAssistantRuntime"
$RuntimeVenv = Join-Path $RuntimeRoot "mineru-venv"

if (-not $env:LOCALAPPDATA) {
    throw "LOCALAPPDATA is unavailable. MinerU requires an ASCII runtime path on Windows."
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($UvCommand) {
    $UvExe = $UvCommand.Source
}
elseif (Test-Path -LiteralPath $ProjectUv) {
    $UvExe = $ProjectUv
}
else {
    throw "uv was not found. Run: .\.venv\Scripts\python.exe -m pip install uv"
}

if ($Recreate) {
    & $UvExe venv --python $PythonVersion --managed-python --clear $VenvDir
}
elseif (-not (Test-Path -LiteralPath $VenvDir)) {
    & $UvExe venv --python $PythonVersion --managed-python $VenvDir
}

$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
& $UvExe pip install --python $PythonExe -U "mineru[all]" "onnxruntime==1.22.1"
if ($LASTEXITCODE -ne 0) {
    throw "MinerU dependency installation failed."
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $ModelCache | Out-Null
if (Test-Path -LiteralPath $RuntimeVenv) {
    $runtimeItem = Get-Item -LiteralPath $RuntimeVenv -Force
    if (-not ($runtimeItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$RuntimeVenv exists but is not a directory junction. Move it and rerun setup_mineru.ps1."
    }
    $targetMatches = @($runtimeItem.Target) | Where-Object {
        [IO.Path]::GetFullPath($_) -eq [IO.Path]::GetFullPath($VenvDir)
    }
    if (-not $targetMatches) {
        Remove-Item -LiteralPath $RuntimeVenv -Force
    }
}
if (-not (Test-Path -LiteralPath $RuntimeVenv)) {
    New-Item -ItemType Junction -Path $RuntimeVenv -Target $VenvDir | Out-Null
}

$RuntimePython = Join-Path $RuntimeVenv "Scripts\python.exe"
$env:PYTHONUTF8 = "1"
$env:MINERU_MODEL_SOURCE = "modelscope"
$env:MODELSCOPE_CACHE = $ModelCache
$env:MINERU_TOOLS_CONFIG_JSON = $ModelConfig

& $RuntimePython -c "import fasttext; from fast_langdetect.ft_detect.infer import LOCAL_SMALL_MODEL_PATH; fasttext.load_model(str(LOCAL_SMALL_MODEL_PATH)); print('FastText path check passed.')"
if ($LASTEXITCODE -ne 0) {
    throw "MinerU FastText compatibility check failed."
}

if (-not $SkipModels) {
    & $RuntimePython -m mineru.cli.models_download -s modelscope -m pipeline
    if ($LASTEXITCODE -ne 0) {
        throw "MinerU pipeline model download failed."
    }
}

$environmentBytes = (Get-ChildItem -LiteralPath $VenvDir -Recurse -File | Measure-Object Length -Sum).Sum
$modelBytes = (Get-ChildItem -LiteralPath $ModelRoot -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum

Write-Host "MinerU installation completed." -ForegroundColor Green
Write-Host "Runtime Python: $RuntimePython"
Write-Host "Project environment: $([math]::Round($environmentBytes / 1GB, 3)) GiB"
Write-Host "Pipeline models: $([math]::Round($modelBytes / 1GB, 3)) GiB"
Write-Host "The application discovers the ASCII runtime automatically."
