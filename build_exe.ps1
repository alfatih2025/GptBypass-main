$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    throw "未找到 venv\Scripts\python.exe"
}

. .\venv\Scripts\Activate.ps1

python -m pip install -U pyinstaller | Out-Host

$distDir = Join-Path $root "dist"
$buildDir = Join-Path $root "build"

if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force }
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
if (Test-Path ".\proxy_app.spec") { Remove-Item ".\proxy_app.spec" -Force }

python -m PyInstaller `
  --clean `
  --noconfirm `
  --onefile `
  --name proxy_app `
  --collect-all rust_filter `
  --collect-submodules uvicorn `
  --collect-submodules fastapi `
  --collect-submodules starlette `
  --collect-submodules anyio `
  --add-data "config.json;." `
  run_proxy.py | Out-Host

Write-Host ""
Write-Host "构建完成：$distDir\proxy_app.exe"
Write-Host "可执行示例："
Write-Host ".\dist\proxy_app.exe --host 127.0.0.1 --port 8999"
