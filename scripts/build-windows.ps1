param([string]$Python='python', [string]$DistRoot='dist/public')
$ErrorActionPreference='Stop'
$workspaceRoot=(Resolve-Path "$PSScriptRoot\..").Path
$distPath=[IO.Path]::GetFullPath((Join-Path $workspaceRoot $DistRoot))
$packageRoot=Join-Path $distPath 'RailNomad'
if (-not $packageRoot.StartsWith($workspaceRoot+'\',[StringComparison]::OrdinalIgnoreCase)) {throw '输出超出仓库'}
Push-Location $workspaceRoot
try {
    & $Python scripts/check_public_release.py
    if ($LASTEXITCODE -ne 0) {throw '公开边界检查失败'}
    & $Python scripts/download_license_sources.py
    if ($LASTEXITCODE -ne 0) {throw '对应源码未准备齐全'}
    & $Python -m PyInstaller --noconfirm --windowed --onedir --name RailNomad --distpath $distPath --workpath "$workspaceRoot\build" --collect-submodules railnomad --hidden-import PySide6.support.deprecated --add-data 'railnomad/ui/assets;railnomad/ui/assets' --add-data 'railnomad/data/reference;railnomad/data/reference' --add-data 'public-data;public-data' --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQml --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtPdf run.py
    if ($LASTEXITCODE -ne 0) {throw '桌面打包失败'}
    & $Python scripts/trim_qt_runtime.py $packageRoot
    if ($LASTEXITCODE -ne 0) {throw 'Qt 裁剪检查失败'}
    Copy-Item -LiteralPath docs/runtime-readme.md -Destination (Join-Path $packageRoot 'README.md')
    Copy-Item -LiteralPath docs/manual-evidence.md -Destination $packageRoot
    & $Python scripts/package_notices.py $packageRoot
    if ($LASTEXITCODE -ne 0) {throw '许可和源码配套失败'}
} finally {Pop-Location}
