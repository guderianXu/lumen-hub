param(
    [string]$Source = "",
    [string]$InstallDir = "",
    [switch]$DesktopShortcut,
    [switch]$Startup,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$AppName = "Lumen Hub"
$InstallMarkerName = ".lumen-hub-install"
$DefaultInstallDir = Join-Path $env:LOCALAPPDATA "Programs\LumenHub"
$RequestedInstallDir = if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $DefaultInstallDir
} else {
    $InstallDir
}
$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$StartMenuShortcut = Join-Path $StartMenuDir "$AppName.lnk"
$DesktopShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"
$StartupShortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) "$AppName.lnk"

function Resolve-FullPath {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        throw "Path must not be empty."
    }
    return [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($PathValue))
}

function Normalize-PathText {
    param([string]$PathValue)

    $Full = Resolve-FullPath $PathValue
    $TrimChars = [char[]]@('\', '/')
    return $Full.TrimEnd($TrimChars).ToLowerInvariant()
}

function Test-SamePath {
    param(
        [string]$Left,
        [string]$Right
    )

    return (Normalize-PathText $Left) -eq (Normalize-PathText $Right)
}

function Assert-InstallRoot {
    param([string]$PathValue)

    $Full = Resolve-FullPath $PathValue
    $Root = [System.IO.Path]::GetPathRoot($Full)
    if ([string]::IsNullOrWhiteSpace($Root) -or (Test-SamePath $Full $Root)) {
        throw "Refusing to use a filesystem root as the install directory: $Full"
    }
    $Parent = Split-Path -Parent $Full
    if ([string]::IsNullOrWhiteSpace($Parent)) {
        throw "Refusing to use an install directory without a parent: $Full"
    }
    return $Full
}

function Assert-SafeInstallDir {
    param([string]$PathValue)

    $Full = Assert-InstallRoot $PathValue
    $Home = [Environment]::GetFolderPath("UserProfile")
    if (-not [string]::IsNullOrWhiteSpace($Home) -and (Test-SamePath $Full $Home)) {
        throw "Refusing to use the user profile root as the install directory: $Full"
    }
    return $Full
}

function Assert-SafeSourceDir {
    param(
        [string]$SourceDir,
        [string]$InstallDir
    )

    $FullSource = Resolve-FullPath $SourceDir
    $SourceRoot = [System.IO.Path]::GetPathRoot($FullSource)
    if ([string]::IsNullOrWhiteSpace($SourceRoot) -or (Test-SamePath $FullSource $SourceRoot)) {
        throw "Refusing to install from a filesystem root: $FullSource"
    }
    if (Test-SamePath $FullSource $InstallDir) {
        throw "Refusing to install from the destination directory: $FullSource"
    }
    $ExePath = Join-Path $FullSource "LumenHub.exe"
    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        throw "Source does not contain LumenHub.exe: $FullSource"
    }
    return $FullSource
}

function Test-LumenHubInstallMarker {
    param([string]$PathValue)

    return Test-Path -LiteralPath (Join-Path $PathValue $InstallMarkerName) -PathType Leaf
}

function Remove-LumenHubInstallDir {
    param([string]$PathValue)

    $SafeDir = Assert-SafeInstallDir $PathValue
    if (-not (Test-Path -LiteralPath $SafeDir)) {
        return
    }
    if (-not (Test-LumenHubInstallMarker $SafeDir)) {
        throw "Refusing to remove unmarked install directory: $SafeDir"
    }
    Remove-Item -LiteralPath $SafeDir -Recurse -Force
    Write-Host "Removed application directory: $SafeDir"
}

function Resolve-LumenHubSource {
    if (-not [string]::IsNullOrWhiteSpace($Source)) {
        $Candidate = (Resolve-Path $Source).Path
        if (Test-Path -LiteralPath (Join-Path $Candidate "LumenHub.exe") -PathType Leaf) {
            return $Candidate
        }
        $Nested = Join-Path $Candidate "LumenHub"
        if (Test-Path -LiteralPath (Join-Path $Nested "LumenHub.exe") -PathType Leaf) {
            return (Resolve-Path $Nested).Path
        }
        throw "Source does not contain LumenHub.exe: $Source"
    }

    $ScriptDir = Split-Path -Parent $PSCommandPath
    if (Test-Path -LiteralPath (Join-Path $ScriptDir "LumenHub.exe") -PathType Leaf) {
        return $ScriptDir
    }
    $BundledDir = Join-Path $ScriptDir "LumenHub"
    if (Test-Path -LiteralPath (Join-Path $BundledDir "LumenHub.exe") -PathType Leaf) {
        return (Resolve-Path $BundledDir).Path
    }
    $RepoDist = Join-Path $ScriptDir "..\..\dist\LumenHub"
    if (Test-Path -LiteralPath (Join-Path $RepoDist "LumenHub.exe") -PathType Leaf) {
        return (Resolve-Path $RepoDist).Path
    }

    throw "Cannot find LumenHub.exe. Pass -Source .\dist\LumenHub or run this next to the release bundle."
}

function New-LumenHubShortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetPath,
        [string]$WorkingDirectory
    )

    $ShortcutParent = Split-Path -Parent $ShortcutPath
    New-Item -ItemType Directory -Force -Path $ShortcutParent | Out-Null
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.WorkingDirectory = $WorkingDirectory
    $Shortcut.Description = "$AppName GUI"
    $Shortcut.Save()
}

$ResolvedInstallDir = Assert-SafeInstallDir $RequestedInstallDir

function Remove-LumenHubInstall {
    foreach ($Shortcut in @($StartMenuShortcut, $DesktopShortcutPath, $StartupShortcutPath)) {
        if (Test-Path -LiteralPath $Shortcut) {
            Remove-Item -LiteralPath $Shortcut -Force
            Write-Host "Removed shortcut: $Shortcut"
        }
    }
    Remove-LumenHubInstallDir $ResolvedInstallDir
}

if ($Uninstall) {
    Remove-LumenHubInstall
    return
}

$SourceDir = Assert-SafeSourceDir (Resolve-LumenHubSource) $ResolvedInstallDir
Remove-LumenHubInstallDir $ResolvedInstallDir
New-Item -ItemType Directory -Force -Path $ResolvedInstallDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir "*") -Destination $ResolvedInstallDir -Recurse -Force
Set-Content -Path (Join-Path $ResolvedInstallDir $InstallMarkerName) -Value "Lumen Hub managed install directory" -Encoding UTF8

$ExePath = Join-Path $ResolvedInstallDir "LumenHub.exe"
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Install failed because LumenHub.exe is missing: $ExePath"
}

New-LumenHubShortcut -ShortcutPath $StartMenuShortcut -TargetPath $ExePath -WorkingDirectory $ResolvedInstallDir
if ($DesktopShortcut) {
    New-LumenHubShortcut -ShortcutPath $DesktopShortcutPath -TargetPath $ExePath -WorkingDirectory $ResolvedInstallDir
}
if ($Startup) {
    New-LumenHubShortcut -ShortcutPath $StartupShortcutPath -TargetPath $ExePath -WorkingDirectory $ResolvedInstallDir
}

Write-Host "Installed $AppName to: $ResolvedInstallDir"
Write-Host "Start menu shortcut: $StartMenuShortcut"
if ($DesktopShortcut) {
    Write-Host "Desktop shortcut: $DesktopShortcutPath"
}
if ($Startup) {
    Write-Host "Startup shortcut: $StartupShortcutPath"
}
