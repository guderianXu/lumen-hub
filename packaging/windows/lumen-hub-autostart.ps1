param(
    [ValidateSet("StartupShortcut", "ScheduledTask")]
    [string]$Mode = "StartupShortcut",
    [switch]$Uninstall,
    [string]$Command = "lumen-hub-gui"
)

$ErrorActionPreference = "Stop"
$AppName = "Lumen Hub"
$TaskName = "Lumen Hub"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "Lumen Hub.lnk"

function New-LumenHubLaunchArgument {
    param([string]$LaunchCommand)
    return "-NoProfile -WindowStyle Hidden -Command `"Start-Process -FilePath '$LaunchCommand'`""
}

function Install-StartupShortcut {
    param([string]$LaunchCommand)
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = New-LumenHubLaunchArgument -LaunchCommand $LaunchCommand
    $Shortcut.WorkingDirectory = $env:USERPROFILE
    $Shortcut.Description = "$AppName GUI autostart"
    $Shortcut.Save()
    Write-Host "Installed Startup shortcut: $ShortcutPath"
}

function Install-ScheduledTask {
    param([string]$LaunchCommand)
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (New-LumenHubLaunchArgument -LaunchCommand $LaunchCommand)
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Description "$AppName GUI autostart" -Force | Out-Null
    Write-Host "Installed ScheduledTask: $TaskName"
}

function Uninstall-LumenHubAutostart {
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "Removed Startup shortcut: $ShortcutPath"
    }
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $Task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed ScheduledTask: $TaskName"
    }
}

if ($Uninstall) {
    Uninstall-LumenHubAutostart
    return
}

if ($Mode -eq "ScheduledTask") {
    Install-ScheduledTask -LaunchCommand $Command
} else {
    Install-StartupShortcut -LaunchCommand $Command
}
