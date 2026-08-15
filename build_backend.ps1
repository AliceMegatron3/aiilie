# build_backend.ps1 — 通过 build.spec 构建 PyInstaller 后端 EXE
# 用法：powershell -ExecutionPolicy Bypass -File build_backend.ps1
$ErrorActionPreference = "Continue"
$root = "C:\Users\11482\Documents\aiilie"
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

Write-Output "[build] 开始清理 build/ 与 dist/"
if (Test-Path (Join-Path $root "build")) { Remove-Item -Recurse -Force (Join-Path $root "build") -ErrorAction SilentlyContinue }
if (Test-Path (Join-Path $root "dist"))  { Remove-Item -Recurse -Force (Join-Path $root "dist")  -ErrorAction SilentlyContinue }

Write-Output "[build] 执行 PyInstaller build.spec"
Set-Location $root
& $venvPy -m PyInstaller --noconfirm build.spec 2>&1 | Out-File -Append -Encoding utf8 (Join-Path $root "build_backend.log")
$code = $LASTEXITCODE
Write-Output "[build] PyInstaller 退出码: $code"

$exe = Join-Path $root "dist\No0_AI_V4.exe"
if (Test-Path $exe) {
    $size = (Get-Item $exe).Length
    Write-Output "[build] ✅ EXE 生成成功: $exe ($([math]::Round($size/1MB,1)) MB)"
} else {
    Write-Output "[build] ❌ EXE 未生成，构建失败。"
}
exit $code
