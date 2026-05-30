param(
    [string]$Version = "2.1.17",
    [string]$CaptureBase = "l-connect-v2.1.17",
    [string]$CaptureDir = ".cache\lianli\windows-captures-v2.1.17",
    [string]$ArtifactDir = ".cache\lianli",
    [switch]$DryRun
)

function Resolve-PythonExe {
    $pathCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path $env:PROGRAMFILES "Python311"),
        (Join-Path $env:PROGRAMFILES "Python"),
        "C:\\Users\\13122\\AppData\\Local\\Programs\\Python"
    )

    $pythonCandidates = @()

    foreach ($base in $pathCandidates) {
        if (-not (Test-Path -LiteralPath $base)) { continue }
        Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $pythonCandidates += Join-Path $_.FullName "python.exe"
            $pythonCandidates += Join-Path $_.FullName "Scripts\\python.exe"
        }
    }

    $pythonCandidates += "python"
    $pythonCandidates += "python3"

    foreach ($candidate in $pythonCandidates) {
        $cmd = $null
        if (Test-Path -LiteralPath $candidate) {
            $cmd = Get-Item -LiteralPath $candidate
        } else {
            $found = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($null -ne $found) { $cmd = $found }
        }

        if ($null -eq $cmd) { continue }

        $exe = if ($cmd -is [System.IO.FileInfo]) { $cmd.FullName } else { $cmd.Source }
        if ([string]::IsNullOrWhiteSpace($exe)) { continue }

        try {
            $out = & $exe -V 2>$null
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($out)) {
                if ($out -match "Python") {
                    return $exe
                }
            }
        } catch {
            continue
        }
    }

    throw "未找到可用 python。请先安装 Python 3.10+ 并加入 PATH，或将可执行路径写入 PATH。"
}

function New-DirectorySafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType Directory -Force -Path $Path
    }
}

function Invoke-Step {
    param(
        [string]$PythonExe,
        [string]$Title,
        [string[]]$Arguments,
        [string]$StdOutPath = "",
        [bool]$DryRun
    )

    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
    $commandText = "$PythonExe " + (($Arguments | ForEach-Object { "`"$_`"" }) -join " ")
    Write-Host $commandText
    if ($DryRun) {
        Write-Host "(dry-run: skip)"
        return
    }

    if ([string]::IsNullOrWhiteSpace($StdOutPath)) {
        & $PythonExe @Arguments
    } else {
        & $PythonExe @Arguments > $StdOutPath
    }

    if ($LASTEXITCODE -ne 0) {
        throw "$Title 失败，退出码: $LASTEXITCODE"
    }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot.Path
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $repoRoot.Path } else { "$($repoRoot.Path);$env:PYTHONPATH" }

$pythonExe = Resolve-PythonExe
New-DirectorySafe -Path $CaptureDir
New-DirectorySafe -Path $ArtifactDir

$scenarios = @(
    @{ Id = "baseline"; File = "$CaptureBase-00-baseline.pcapng" },
    @{ Id = "direct-fan-speed"; File = "$CaptureBase-01-direct-fan-speed.pcapng" },
    @{ Id = "motherboard-pwm-sync"; File = "$CaptureBase-02-mb-pwm-sync.pcapng" },
    @{ Id = "rf-rebind"; File = "$CaptureBase-03-rf-rebind.pcapng" },
    @{ Id = "sort-quick-sync"; File = "$CaptureBase-04-sort-quick-sync.pcapng" },
    @{ Id = "lighting-static-and-off"; File = "$CaptureBase-05-lighting-static-off.pcapng" },
    @{ Id = "lighting-generated-rainbow"; File = "$CaptureBase-06-lighting-generated-rainbow.pcapng" }
)

$runbookJson = Join-Path $ArtifactDir "windows-capture-runbook-$Version.json"
$captureSetReportJson = Join-Path $CaptureDir "capture-set-report.json"
$captureGapReportJson = Join-Path $CaptureDir "capture-gap-report.json"
$captureTriageSummaryJson = Join-Path $CaptureDir "capture-triage-report.json"
$targetRegistryJson = Join-Path $CaptureDir "linux-control-target-registry.json"
$linuxPlanPath = Join-Path $CaptureDir "linux-post-capture-commands.txt"
$captureNotesDir = Join-Path $CaptureDir "notes"
New-DirectorySafe -Path $captureNotesDir

Invoke-Step -PythonExe $pythonExe -DryRun:$DryRun.IsPresent -Title "生成 windows-capture-runbook" -Arguments @(
    "tools/lianli_wireless_probe.py",
    "--save-json", $runbookJson,
    "windows-capture-runbook", $CaptureDir,
    "--version", $Version,
    "--capture-base", $CaptureBase,
    "--artifact-dir", $ArtifactDir
)

foreach ($scenario in $scenarios) {
    $notePath = Join-Path $captureNotesDir "$($scenario.Id).notes.json"
    Invoke-Step -PythonExe $pythonExe -DryRun:$DryRun.IsPresent -Title "生成 scenario note: $($scenario.Id)" -Arguments @(
        "tools/lianli_wireless_probe.py",
        "windows-capture-note", $scenario.Id,
        "--version", $Version,
        "--capture-base", $CaptureBase,
        "--capture-file", $scenario.File,
        "--artifact-dir", $ArtifactDir
    ) -StdOutPath $notePath
}

$linuxPlan = New-Object System.Collections.Generic.List[string]
$linuxCaptureDir = $CaptureDir.Replace('\', '/')
$linuxArtifactDir = $ArtifactDir.Replace('\', '/')
$linuxSetReportJson = $captureSetReportJson.Replace('\', '/')
$linuxGapReportJson = $captureGapReportJson.Replace('\', '/')
$linuxTriageSummaryJson = $captureTriageSummaryJson.Replace('\', '/')
$linuxTargetRegistryJson = $targetRegistryJson.Replace('\', '/')

$linuxPlan.Add("# 采集完成后在 Linux 继续执行。请先切换到仓库根目录再执行以下命令。")
$linuxPlan.Add("bash scripts/lianli-wireless-probe.sh --save-json '$linuxSetReportJson' capture-set-report '$linuxCaptureDir' --version '$Version' --capture-base '$CaptureBase' --artifact-dir '$linuxArtifactDir'")
$linuxPlan.Add("bash scripts/lianli-wireless-probe.sh --save-json '$linuxGapReportJson' capture-gap-report '$linuxCaptureDir' --version '$Version' --capture-base '$CaptureBase' --artifact-dir '$linuxArtifactDir'")
$linuxPlan.Add("bash scripts/lianli-wireless-probe.sh --save-json '$linuxTriageSummaryJson' summarize-captures '$linuxCaptureDir'")

foreach ($scenario in $scenarios) {
    $capturePath = Join-Path $CaptureDir $scenario.File
    if (-not (Test-Path -LiteralPath $capturePath -ErrorAction SilentlyContinue)) {
        continue
    }
    $stem = [IO.Path]::GetFileNameWithoutExtension($scenario.File)
    $linuxCaptureJson = "$($capturePath.Replace('\', '/')).json"
    $linuxPlan.Add("bash scripts/lianli-wireless-probe.sh --save-json '$linuxCaptureJson' capture-triage-report '$($capturePath.Replace('\','/'))'")
    $linuxPlan.Add("bash scripts/lianli-wireless-probe.sh --save-json '$($linuxCaptureDir.Replace('\', '/'))/capture-signature-match-$stem.json' capture-signature-match '$($capturePath.Replace('\','/'))'")
}

$linuxPlan.Add("bash scripts/lianli-wireless-probe.sh --save-json '$linuxTargetRegistryJson' linux-control-target-registry '$linuxCaptureDir' --version '$Version' --capture-base '$CaptureBase'")
$linuxPlan | Set-Content -Encoding UTF8 -Path $linuxPlanPath

Write-Host ""
Write-Host "已生成:"
Write-Host "  - runbook: $runbookJson"
Write-Host "  - notes:  $captureNotesDir\\*.notes.json"
Write-Host "  - linux plan: $linuxPlanPath"
Write-Host ""
Write-Host "下一步: 请在 Windows 用 USBPcap 抓 7 个文件到 $CaptureDir，文件名为:"
foreach ($scenario in $scenarios) {
    Write-Host "  - $($scenario.File)"
}
