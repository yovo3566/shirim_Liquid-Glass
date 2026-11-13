# Builds the Windows app into dist\Shirim\Shirim.exe (pass -DistPath to build elsewhere, e.g. while the app is open)
# No $ErrorActionPreference = 'Stop': PyInstaller logs to stderr, which Windows PowerShell would treat as fatal.
# Failures are detected through $LASTEXITCODE instead.
param([string]$DistPath = 'dist', [string]$WorkPath = 'build')

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'

& $py desktop\make_icon.py
if ($LASTEXITCODE) { throw 'Icon generation failed' }

# The newest phone app travels with the computer's app, so paired phones can update themselves from it.
# Build the APK first (android\gradlew.bat assembleRelease).
$updates = Join-Path $root 'build-updates'
New-Item -ItemType Directory -Force $updates | Out-Null
$apk = Join-Path $root 'android\app\build\outputs\apk\standard\release\app-standard-release.apk'
$filteredApk = Join-Path $root 'android\app\build\outputs\apk\filtered\release\app-filtered-release.apk'
if (Test-Path $apk) {
    Copy-Item $apk (Join-Path $updates 'shirim.apk') -Force
    if (Test-Path $filteredApk) { Copy-Item $filteredApk (Join-Path $updates 'shirim-filtered.apk') -Force }
    $gradle = Get-Content (Join-Path $root 'android\app\build.gradle.kts') -Raw
    $code = [int]([regex]::Match($gradle, 'versionCode\s*=\s*(\d+)').Groups[1].Value)
    $name = [regex]::Match($gradle, 'versionName\s*=\s*"([^"]+)"').Groups[1].Value
    @{ versionCode = $code; versionName = $name } | ConvertTo-Json | Out-File (Join-Path $updates 'android.json') -Encoding ascii
} else {
    Write-Warning 'No phone app build found; paired phones will not be offered an update'
}

& $py -m PyInstaller --noconfirm --clean --windowed --name Shirim `
    --icon desktop\shirim.ico `
    --paths core `
    --add-data "web;web" `
    --add-data "build-updates;updates" `
    --add-data "desktop\shirim-filtered.ico;." `
    --add-binary "vendor\win\ffmpeg.exe;bin" `
    --add-binary "vendor\win\deno.exe;bin" `
    --collect-data ytmusicapi `
    --collect-data yt_dlp_ejs `
    --collect-submodules qrcode `
    --distpath $DistPath --workpath $WorkPath `
    desktop\shirim_desktop.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }

Write-Host "Built $root\$DistPath\Shirim\Shirim.exe"
