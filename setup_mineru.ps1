param(
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".mineru-venv"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "未找到 uv。请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
}

if (-not (Test-Path -LiteralPath $VenvDir)) {
    uv venv --python $PythonVersion $VenvDir
}

$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
uv pip install --python $PythonExe -U "mineru[all]"

Write-Host "MinerU 安装完成。" -ForegroundColor Green
Write-Host "CLI: $(Join-Path $VenvDir 'Scripts\mineru.exe')"
Write-Host "建议在 .env 中设置 MINERU_CLI=.mineru-venv/Scripts/mineru.exe"
