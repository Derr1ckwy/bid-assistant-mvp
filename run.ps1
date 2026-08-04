$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "未找到项目虚拟环境。请先在项目目录运行：python -m venv .venv"
}

Set-Location -LiteralPath $projectRoot
& $pythonPath -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
