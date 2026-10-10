param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

# 从任意目录启动时都切换到项目根目录，确保前端 dist 和 .env 可被找到。
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

& "$projectRoot\.venv-gpu\Scripts\python.exe" `
    -m uvicorn trippilot.app:app `
    --host 0.0.0.0 `
    --port $Port
