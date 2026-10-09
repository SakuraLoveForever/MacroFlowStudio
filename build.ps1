# 用 Continue 而非 Stop：PowerShell 5.1 在 "Stop" 下会把原生命令
# （python.exe）写在 stderr 的任何输出当作错误并中断脚本，而
# PyInstaller 的日志全走 stderr；错误改由 $LASTEXITCODE 显式检查。
param(
  [switch]$Clean
)

$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir
$ProjectPython = $null
if ((Test-Path -LiteralPath (Join-Path $ProjectDir ".deps")) -and
    (Test-Path -LiteralPath "C:\Python313\python.exe")) {
  $ProjectPython = "C:\Python313\python.exe"
}
if (-not $ProjectPython) {
  $ProjectPython = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $ProjectPython) {
  throw "Python executable not found."
}
$ProjectDependencies = Join-Path $ProjectDir ".deps"
$PreviousModulePath = $env:PYTHONPATH
if (Test-Path -LiteralPath $ProjectDependencies) {
  $env:PYTHONPATH = if ($PreviousModulePath) { "$ProjectDependencies;$PreviousModulePath" } else { $ProjectDependencies }
}

# 体积优化（重复 DLL 去重、排除运行时不可达依赖）都在
# MacroFlowStudio.spec 中实现，构建必须走 spec，不要改回命令行参数模式
# （命令行参数模式会用参数覆盖本文件同目录的 spec，导致优化丢失）。
$BuildStamp = Join-Path $ProjectDir "build\MacroFlowStudio.inputs.sha256"
$BuildInputPaths = @(
  (Join-Path $ProjectDir "MacroFlowStudio.spec"),
  (Join-Path $ProjectDir "tools\ocr_closure_modules.txt"),
  (Join-Path $ProjectDir "build.ps1")
)
$BuildInputPaths += @(Get-ChildItem -LiteralPath (Join-Path $ProjectDir "src") -File -Recurse |
  Where-Object {
    $_.FullName -notmatch '[\\/]__pycache__([\\/]|$)' -and
    $_.Extension -notin @('.pyc', '.pyo')
  } |
  Sort-Object FullName | Select-Object -ExpandProperty FullName)
$BuildInputLines = foreach ($InputPath in $BuildInputPaths) {
  if (-not (Test-Path -LiteralPath $InputPath)) {
    throw "构建输入缺失：$InputPath"
  }
  $RelativePath = $InputPath.Substring($ProjectDir.Length).TrimStart('\')
  $ContentHash = (Get-FileHash -LiteralPath $InputPath -Algorithm SHA256).Hash
  "$RelativePath`t$ContentHash"
}
$HashProvider = [Security.Cryptography.SHA256]::Create()
try {
  $BuildInputHash = [BitConverter]::ToString(
    $HashProvider.ComputeHash([Text.Encoding]::UTF8.GetBytes(($BuildInputLines -join "`n")))
  ).Replace("-", "")
} finally {
  $HashProvider.Dispose()
}
$ExistingBuildHash = if (Test-Path -LiteralPath $BuildStamp) {
  (Get-Content -LiteralPath $BuildStamp -Raw).Trim()
} else {
  ""
}
$CanReuseExecutable = (-not $Clean) -and
  (Test-Path -LiteralPath (Join-Path $ProjectDir "dist\MacroFlowStudio.exe")) -and
  ($ExistingBuildHash -eq $BuildInputHash)

if ($CanReuseExecutable) {
  Write-Host "PyInstaller skipped: source/spec unchanged (use .\build.ps1 -Clean to force rebuild)."
} else {
  try {
    $PyInstallerArgs = @("--noconfirm")
    if ($Clean) {
      $PyInstallerArgs += "--clean"
    }
    $PyInstallerArgs += "MacroFlowStudio.spec"
    & $ProjectPython -m PyInstaller @PyInstallerArgs
  } finally {
    $env:PYTHONPATH = $PreviousModulePath
  }

  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $BuildStamp) | Out-Null
  Set-Content -LiteralPath $BuildStamp -Value $BuildInputHash -NoNewline -Encoding ASCII
}

# Keep the release notes beside the packaged executable in sync with this build.
Copy-Item -LiteralPath (Join-Path $ProjectDir "README.md") `
  -Destination (Join-Path $ProjectDir "dist\README.md") -Force
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir "dist\README.md"))) {
  throw "Failed to copy README.md to dist\README.md"
}
Copy-Item -LiteralPath (Join-Path $ProjectDir "CHANGELOG.md") `
  -Destination (Join-Path $ProjectDir "dist\CHANGELOG.md") -Force
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir "dist\CHANGELOG.md"))) {
  throw "Failed to copy CHANGELOG.md to dist\CHANGELOG.md"
}

# RapidOCR、ONNX Runtime CPU、wheel 内模型及未由 exe 提供的传递依赖
# 都由依赖元数据计算后同步到单独目录。该脚本只重建 dist\rapidocr_ocr。
& $ProjectPython (Join-Path $ProjectDir "tools\ocr_deps_setup.py")
if ($LASTEXITCODE -ne 0) {
  throw "RapidOCR 组件准备失败（exit code $LASTEXITCODE）"
}

$OcrTarget = Join-Path $ProjectDir "dist\rapidocr_ocr"
$OcrMarkers = @(
  "rapidocr\__init__.py",
  "onnxruntime\__init__.py",
  "onnxruntime\capi\onnxruntime.dll",
  "onnxruntime\capi\onnxruntime_providers_shared.dll",
  "rapidocr\models\PP-OCRv6_det_small.onnx",
  "rapidocr\models\ch_ppocr_mobile_v2.0_cls_mobile.onnx",
  "rapidocr\models\PP-OCRv6_rec_small.onnx",
  "RapidOCR-LICENSE.txt",
  "THIRD_PARTY_DEPENDENCIES.txt",
  "OCR_EXTERNAL_MODULES.txt"
)
foreach ($Marker in $OcrMarkers) {
  $MarkerPath = Join-Path $OcrTarget $Marker
  if (-not (Test-Path -LiteralPath $MarkerPath)) {
    throw "RapidOCR 组件不完整：$MarkerPath"
  }
}
if (-not (Get-ChildItem -LiteralPath (Join-Path $OcrTarget "onnxruntime\capi") `
    -Filter "onnxruntime_pybind11_state*.pyd" -File -ErrorAction SilentlyContinue)) {
  throw "RapidOCR 组件缺少 ONNX Runtime Python CPU 扩展"
}

Write-Host "Build complete: $ProjectDir\dist\MacroFlowStudio.exe (+ rapidocr_ocr/ 外置 OCR 组件)"

foreach ($ClientDir in @((Join-Path $ProjectDir "dist"))) {
  New-Item -ItemType Directory -Force -Path (Join-Path $ClientDir "docs") | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $ClientDir "examples\python") | Out-Null
  Copy-Item -LiteralPath (Join-Path $ProjectDir "docs\python-scripts.md") -Destination (Join-Path $ClientDir "docs") -Force
  Copy-Item -LiteralPath (Join-Path $ProjectDir "examples\python\hello_macro.py") -Destination (Join-Path $ClientDir "examples\python") -Force
}
