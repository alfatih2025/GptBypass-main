$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    throw "未找到 venv\Scripts\python.exe"
}

. .\venv\Scripts\Activate.ps1

python -m pip install -U pyinstaller pystray pillow customtkinter | Out-Host

$pngIcon = Join-Path $root "ico.png"
$icoFile = Join-Path $root "app_icon.ico"

if (-not (Test-Path $pngIcon)) {
    throw "未找到图标文件：$pngIcon"
}

python -c "from PIL import Image; img = Image.open(r'$pngIcon').convert('RGBA'); sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]; img.save(r'$icoFile', format='ICO', sizes=sizes)" | Out-Host

$distDir = Join-Path $root "dist_gui"
$buildDir = Join-Path $root "build_gui"

if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force }
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
if (Test-Path ".\proxy_gui.spec") { Remove-Item ".\proxy_gui.spec" -Force }

python -m PyInstaller `
  --clean `
  --noconfirm `
  --onefile `
  --windowed `
  --name proxy_gui `
  --collect-all rust_filter `
  --collect-all customtkinter `
  --collect-all pystray `
  --collect-all PIL `
  --collect-submodules proxy `
  --collect-submodules uvicorn `
  --collect-submodules fastapi `
  --collect-submodules starlette `
  --collect-submodules anyio `
  --add-data "ico.png;." `
  --add-data "app_icon.ico;." `
  --add-data "logo.png;." `
  --icon "$icoFile" `
  --distpath "$distDir" `
  --workpath "$buildDir" `
  gui_app.py | Out-Host

Write-Host ""
Write-Host "构建完成：$distDir\proxy_gui.exe"
Write-Host "运行方式：双击或执行 .\dist_gui\proxy_gui.exe"
