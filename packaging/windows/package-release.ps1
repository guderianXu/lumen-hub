param(
    [switch]$OneFile,
    [switch]$Clean,
    [switch]$SkipInstall,
    [string]$OutputDir = "",
    [string]$ArchiveName = "LumenHub-windows-x64.zip"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildScript = Join-Path $PSScriptRoot "build-exe.ps1"
$InstallScript = Join-Path $PSScriptRoot "install-app.ps1"
$ReleaseDir = if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    Join-Path $RepoRoot "release"
} else {
    $OutputDir
}
$StageDir = Join-Path $RepoRoot "build\windows-release"

$BuildArgs = @()
if ($OneFile) {
    $BuildArgs += "-OneFile"
}
if ($Clean) {
    $BuildArgs += "-Clean"
}
if ($SkipInstall) {
    $BuildArgs += "-SkipInstall"
}

& $BuildScript @BuildArgs

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path $StageDir) {
    Remove-Item $StageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

if ($OneFile) {
    $BuiltApp = Join-Path $RepoRoot "dist\LumenHub.exe"
    if (-not (Test-Path $BuiltApp)) {
        throw "Expected packaged executable was not created: $BuiltApp"
    }
    Copy-Item $BuiltApp -Destination (Join-Path $StageDir "LumenHub.exe") -Force
} else {
    $BuiltApp = Join-Path $RepoRoot "dist\LumenHub"
    if (-not (Test-Path (Join-Path $BuiltApp "LumenHub.exe"))) {
        throw "Expected packaged executable was not created: $(Join-Path $BuiltApp 'LumenHub.exe')"
    }
    Copy-Item $BuiltApp -Destination (Join-Path $StageDir "LumenHub") -Recurse -Force
}

Copy-Item $InstallScript -Destination (Join-Path $StageDir "install-app.ps1") -Force

$ArchivePath = Join-Path $ReleaseDir $ArchiveName
if (Test-Path $ArchivePath) {
    Remove-Item $ArchivePath -Force
}

Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $ArchivePath -Force
Write-Host "Windows release package: $ArchivePath"
Write-Host "After extracting, run: powershell -ExecutionPolicy Bypass -File .\install-app.ps1 -DesktopShortcut"
