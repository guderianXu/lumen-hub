param(
    [string]$OutputDir = ".cache\lianli\windows-preflight",
    [string]$CaptureDir = ".cache\lianli\windows-captures-v2.1.17",
    [string]$Version = "2.1.17",
    [string]$CaptureBase = "l-connect-v2.1.17",
    [switch]$SkipLiveList
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Resolve-CommandPath {
    param(
        [string]$Name,
        [string[]]$Candidates = @()
    )

    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return ""
}

function Get-USBPcapCandidatePaths {
    $paths = @(
        "C:\Program Files\USBPcap\USBPcapCMD.exe",
        "C:\Program Files (x86)\USBPcap\USBPcapCMD.exe",
        "C:\Program Files\Wireshark\extcap\USBPcapCMD.exe",
        "$env:APPDATA\Wireshark\extcap\USBPcapCMD.exe",
        "D:\tools\USBPcapCMD.exe"
    )

    $uninstallRoots = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($root in $uninstallRoots) {
        if (-not (Test-Path $root)) {
            continue
        }
        foreach ($key in Get-ChildItem $root -ErrorAction SilentlyContinue) {
            $item = Get-ItemProperty $key.PSPath -ErrorAction SilentlyContinue
            if (-not $item -or "$($item.DisplayName)" -notmatch "USBPcap") {
                continue
            }
            if ($item.InstallLocation) {
                $paths += (Join-Path $item.InstallLocation "USBPcapCMD.exe")
            }
            if ($item.UninstallString -match '^[`"]?([^`"]+Uninstall\.exe)') {
                $paths += (Join-Path (Split-Path $Matches[1] -Parent) "USBPcapCMD.exe")
            }
        }
    }

    return $paths | Where-Object { $_ } | Select-Object -Unique
}

function Invoke-CapturedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 30
    )

    $resolvedFilePath = $FilePath
    if ($FilePath -and -not (Test-Path $FilePath)) {
        $command = Get-Command $FilePath -ErrorAction SilentlyContinue
        if ($command) {
            $resolvedFilePath = $command.Source
        }
    }

    if (-not $resolvedFilePath -or -not (Test-Path $resolvedFilePath)) {
        return [PSCustomObject]@{
            ok = $false
            exit_code = $null
            stdout = ""
            stderr = "missing executable: $FilePath"
            timed_out = $false
        }
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "lianli-preflight"
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    $stdoutPath = Join-Path $tempRoot ([System.Guid]::NewGuid().ToString() + ".stdout.txt")
    $stderrPath = Join-Path $tempRoot ([System.Guid]::NewGuid().ToString() + ".stderr.txt")
    $process = Start-Process -FilePath $resolvedFilePath -ArgumentList $Arguments -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -WindowStyle Hidden
    $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
    if ($timedOut) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    $exitCode = $null
    if (-not $timedOut) {
        $process.Refresh()
        $exitCode = $process.ExitCode
    }
    $stdout = Get-Content -Raw $stdoutPath -ErrorAction SilentlyContinue
    $stderr = Get-Content -Raw $stderrPath -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

    return [PSCustomObject]@{
        ok = (-not $timedOut -and ($exitCode -eq 0 -or $null -eq $exitCode))
        exit_code = $exitCode
        stdout = "$stdout"
        stderr = "$stderr"
        timed_out = $timedOut
    }
}

function Test-Npcap {
    param([string]$DumpcapPath)

    $service = Get-Service -Name npcap -ErrorAction SilentlyContinue
    $list = Invoke-CapturedCommand -FilePath $DumpcapPath -Arguments @("-D") -TimeoutSeconds 20
    $combined = "$($list.stdout)`n$($list.stderr)"
    return [PSCustomObject]@{
        status = if ($service -and $service.Status -eq "Running" -and $combined -match "\\Device\\NPF_") { "ready" } elseif ($combined -match "Unable to load Npcap") { "missing-or-not-loaded" } else { "unknown" }
        service_status = if ($service) { "$($service.Status)" } else { "missing" }
        dumpcap_lists_npf = [bool]($combined -match "\\Device\\NPF_")
        dumpcap_error = if ($combined -match "Unable to load Npcap") { "unable-to-load-npcap" } else { "" }
    }
}

function Test-USBPcap {
    param(
        [string]$DumpcapPath,
        [string]$USBPcapPath
    )

    $serviceText = (& sc.exe query USBPcap 2>&1) -join "`n"
    $serviceRunning = [bool]($serviceText -match "STATE\s+:\s+4\s+RUNNING")
    $classKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{36FC9E60-C465-11CF-8056-444553540000}"
    $upperFilters = @()
    if (Test-Path $classKey) {
        $classProps = Get-ItemProperty $classKey -ErrorAction SilentlyContinue
        if ($classProps -and $classProps.UpperFilters) {
            $upperFilters = @($classProps.UpperFilters)
        }
    }
    $list = Invoke-CapturedCommand -FilePath $DumpcapPath -Arguments @("-D") -TimeoutSeconds 20
    $combined = "$($list.stdout)`n$($list.stderr)"
    $dumpcapListsUsbPcap = [bool]($combined -match "USBPcap")
    $hasFilter = $upperFilters -contains "USBPcap"
    $status = "missing"
    if ($USBPcapPath -and (Test-Path $USBPcapPath) -and $serviceRunning -and $hasFilter -and $dumpcapListsUsbPcap) {
        $status = "ready"
    } elseif ($USBPcapPath -and (Test-Path $USBPcapPath) -and $serviceRunning -and $hasFilter) {
        $status = "installed-needs-reboot-or-replug"
    } elseif ($USBPcapPath -and (Test-Path $USBPcapPath)) {
        $status = "installed-not-attached"
    }

    return [PSCustomObject]@{
        status = $status
        usbpcapcmd_path = $USBPcapPath
        service_running = $serviceRunning
        upper_filters = $upperFilters
        class_filter_present = $hasFilter
        dumpcap_lists_usbpcap = $dumpcapListsUsbPcap
        reboot_recommended = [bool]($status -eq "installed-needs-reboot-or-replug")
    }
}

function Invoke-ProbeJson {
    param(
        [string]$ProbeScript,
        [string[]]$Arguments
    )
    $powerShellArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ProbeScript)
    $powerShellArgs += $Arguments
    $result = Invoke-CapturedCommand -FilePath "powershell.exe" -Arguments $powerShellArgs -TimeoutSeconds 45
    $payload = $null
    if ($result.stdout.Trim()) {
        try {
            $payload = $result.stdout | ConvertFrom-Json
        } catch {
            $payload = $null
        }
    }
    return [PSCustomObject]@{
        ok = $result.ok
        exit_code = $result.exit_code
        stdout = $result.stdout
        stderr = $result.stderr
        payload = $payload
    }
}

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot
$resolvedOutputDir = Join-Path $repoRoot $OutputDir
New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null

$dumpcapPath = Resolve-CommandPath -Name "dumpcap.exe" -Candidates @("C:\Program Files\Wireshark\dumpcap.exe")
$tsharkPath = Resolve-CommandPath -Name "tshark.exe" -Candidates @("C:\Program Files\Wireshark\tshark.exe")
$wiresharkPath = Resolve-CommandPath -Name "Wireshark.exe" -Candidates @("C:\Program Files\Wireshark\Wireshark.exe")
$usbpcapPath = Resolve-CommandPath -Name "USBPcapCMD.exe" -Candidates (Get-USBPcapCandidatePaths)
$probeScript = Join-Path $repoRoot "scripts\lianli-wireless-probe.ps1"
$operatorPlan = Join-Path $repoRoot "scripts\lianli-reverse-operator-plan.ps1"

$npcap = Test-Npcap -DumpcapPath $dumpcapPath
$usbpcap = Test-USBPcap -DumpcapPath $dumpcapPath -USBPcapPath $usbpcapPath
$scan = Invoke-ProbeJson -ProbeScript $probeScript -Arguments @("scan")
$liveList = $null
if (-not $SkipLiveList) {
    $liveList = Invoke-ProbeJson -ProbeScript $probeScript -Arguments @("live-list")
}

$runbookResult = Invoke-CapturedCommand -FilePath "powershell.exe" -Arguments @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $operatorPlan,
    "-DryRun",
    "-SkipLinux",
    "-Version",
    $Version,
    "-CaptureBase",
    $CaptureBase,
    "-CaptureDir",
    $CaptureDir
) -TimeoutSeconds 60
$runbookCommandFile = Join-Path $CaptureDir "reverse-operator-commands.txt"

$devices = @()
if ($scan.payload -and $scan.payload.devices) {
    $devices = @($scan.payload.devices)
}
$hasSender = [bool]($devices | Where-Object { "$($_.vendor_id):$($_.product_id)".ToLowerInvariant() -eq "0416:8040" })
$hasReceiver = [bool]($devices | Where-Object { "$($_.vendor_id):$($_.product_id)".ToLowerInvariant() -eq "0416:8041" })
$liveListStatus = "skipped"
if ($liveList) {
    $liveListCombined = "$($liveList.stdout)`n$($liveList.stderr)"
    if ($liveListCombined -match "Access denied|insufficient permissions|USB access denied") {
        $liveListStatus = "access-denied"
    } elseif ($liveList.payload) {
        $liveListStatus = "ready"
    } else {
        $liveListStatus = "failed"
    }
}

$status = "ready-for-non-capture-work"
if (-not $hasSender -or -not $hasReceiver) {
    $status = "needs-lianli-usb-hardware"
} elseif ($npcap.status -ne "ready") {
    $status = "needs-npcap"
} elseif ($usbpcap.status -ne "ready") {
    $status = "ready-except-usbpcap-capture"
}

$report = [PSCustomObject]@{
    operation = "lianli-windows-preflight"
    status = $status
    repo_root = $repoRoot
    output_dir = $resolvedOutputDir
    version = $Version
    capture_base = $CaptureBase
    capture_dir = $CaptureDir
    tools = [PSCustomObject]@{
        wireshark = $wiresharkPath
        tshark = $tsharkPath
        dumpcap = $dumpcapPath
        usbpcapcmd = $usbpcapPath
    }
    npcap = $npcap
    usbpcap = $usbpcap
    lianli_usb = [PSCustomObject]@{
        scan_status = if ($scan.payload -and $devices.Count -gt 0) { "ready" } elseif ($scan.ok) { "ready-empty" } else { "failed" }
        sender_0416_8040_seen = $hasSender
        receiver_0416_8041_seen = $hasReceiver
        devices = $devices
    }
    live_list = [PSCustomObject]@{
        status = $liveListStatus
        stderr = if ($liveList) { "$($liveList.stderr)$($liveList.stdout)" } else { "" }
    }
    runbook = [PSCustomObject]@{
        status = if ($runbookResult.ok -or (Test-Path $runbookCommandFile)) { "generated" } else { "failed" }
        stdout = $runbookResult.stdout
        stderr = $runbookResult.stderr
        command_file = $runbookCommandFile
    }
    recommended_next_steps = @(
        if ($usbpcap.reboot_recommended) { "Reboot Windows before USBPcap capture; the USBPcap class filter is installed but not attached to active USB hubs." }
        if ($liveListStatus -eq "access-denied") { "Use official USBPcap captures first; fix PyUSB live-list later with Administrator/Zadig only if needed." }
        if ($hasSender -and $hasReceiver) { "Proceed with runbook/note preparation while waiting for USBPcap reboot window." }
    )
}

$jsonPath = Join-Path $resolvedOutputDir "windows-preflight.json"
$textPath = Join-Path $resolvedOutputDir "windows-preflight.txt"
$report | ConvertTo-Json -Depth 20 | Set-Content -Path $jsonPath -Encoding UTF8

$summary = @(
    "LIAN LI Windows preflight",
    "status: $($report.status)",
    "npcap: $($npcap.status)",
    "usbpcap: $($usbpcap.status)",
    "usbpcapcmd: $usbpcapPath",
    "sender 0416:8040 seen: $hasSender",
    "receiver 0416:8041 seen: $hasReceiver",
    "live-list: $liveListStatus",
    "runbook: $($report.runbook.status)",
    "json: $jsonPath"
)
$summary | Set-Content -Path $textPath -Encoding UTF8

$report | ConvertTo-Json -Depth 20
