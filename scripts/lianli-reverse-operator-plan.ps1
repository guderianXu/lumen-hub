param(
    [string]$Version = "2.1.17",
    [string]$CaptureBase = "l-connect-v2.1.17",
    [string]$CaptureDir = ".cache\\lianli\\windows-captures-v2.1.17",
    [string]$ArtifactDir = ".cache\\lianli",
    [string]$TargetId = "",
    [switch]$Run,
    [switch]$DryRun,
    [switch]$SkipLive,
    [switch]$SkipLinux
)

function Resolve-PythonExe {
    $candidates = @("py", "python3", "python")
    foreach ($candidate in $candidates) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $found) { continue }
        try {
            $version = & $found.Source --version 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -match "Python") {
                return $found.Source
            }
        } catch {
            continue
        }
    }
    throw "未找到 Python3，可执行命令需在 PATH 中（推荐 py / python3）"
}

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        $null = New-Item -ItemType Directory -Force -Path $Path
    }
}

function Invoke-Step {
    param(
        [string]$PythonExe,
        [string]$Title,
        [string[]]$CommandArgs,
        [switch]$NoExecute
    )
    $commandText = "$PythonExe " + ($CommandArgs -join " ")
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
    Write-Host $commandText
    if ($NoExecute) {
        return $true
    }
    & $PythonExe @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "执行失败: $Title (exit=$LASTEXITCODE)"
    }
    return $true
}

function New-CommandText {
    param([string[]]$CommandArgs)
    if ($CommandArgs.Count -eq 0) {
        return ""
    }
    $displayArgs = @($CommandArgs)
    if ($displayArgs.Count -gt 0 -and $displayArgs[0] -eq "tools/lianli_wireless_probe.py") {
        $displayArgs = @($displayArgs | Select-Object -Skip 1)
    }
    $isWindowsRuntime = $IsWindows
    if ($null -eq $isWindowsRuntime) {
        $isWindowsRuntime = [System.IO.Path]::DirectorySeparatorChar -eq '\'
    }
    $quotedArgs = $displayArgs | ForEach-Object {
        $safe = "$_" -replace '"', '\"'
        if (-not $isWindowsRuntime) {
            $safe = $safe -replace '\\', '/'
        }
        ('"' + $safe + '"')
    }

    if ($isWindowsRuntime) {
        return "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/lianli-wireless-probe.ps1 " + ($quotedArgs -join " ")
    }
    return "bash scripts/lianli-wireless-probe.sh " + ($quotedArgs -join " ")
}

function New-TargetIdFromRegistry {
    param([string]$RegistryJsonPath)
    if (-not (Test-Path -LiteralPath $RegistryJsonPath)) {
        return ""
    }
    try {
        $payload = Get-Content -Raw -LiteralPath $RegistryJsonPath | ConvertFrom-Json -ErrorAction Stop
        $targets = $payload.targets
        if ($targets -isnot [System.Collections.IEnumerable]) {
            return ""
        }
        foreach ($target in $targets) {
            $id = [string]$target.id
            if (-not [string]::IsNullOrWhiteSpace($id)) {
                return $id
            }
        }
    } catch {
        return ""
    }
    return ""
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot.Path
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $repoRoot.Path } else { "$repoRoot.Path;$env:PYTHONPATH" }

Ensure-Dir -Path $CaptureDir
Ensure-Dir -Path $ArtifactDir

$pythonExe = Resolve-PythonExe
$commandsOutput = Join-Path $CaptureDir "reverse-operator-commands.txt"
$summaryOutput = Join-Path $CaptureDir "reverse-operator-summary.json"

$runbookJson = Join-Path $ArtifactDir "windows-capture-runbook-$Version.json"
$checklistJson = Join-Path $ArtifactDir "windows-capture-checklist-$Version.json"
$queueJson = Join-Path $ArtifactDir "windows-capture-queue-$Version.json"
$capturePlanJson = Join-Path $ArtifactDir "windows-capture-plan-$Version.json"
$registryJson = Join-Path $CaptureDir "linux-control-target-registry.json"
$setJson = Join-Path $CaptureDir "capture-set-report.json"
$gapJson = Join-Path $CaptureDir "capture-gap-report.json"
$triageJson = Join-Path $CaptureDir "capture-triage-report.json"
$signatureJson = Join-Path $CaptureDir "capture-signature-match.json"
$validationJson = Join-Path $CaptureDir "lianli-validation-gate.json"
$controlManifestJson = Join-Path $CaptureDir "linux-control-manifest.json"

$scenarios = @(
    @{ Id = "baseline"; File = "$CaptureBase-00-baseline.pcapng" },
    @{ Id = "direct-fan-speed"; File = "$CaptureBase-01-direct-fan-speed.pcapng" },
    @{ Id = "motherboard-pwm-sync"; File = "$CaptureBase-02-mb-pwm-sync.pcapng" },
    @{ Id = "rf-rebind"; File = "$CaptureBase-03-rf-rebind.pcapng" },
    @{ Id = "sort-quick-sync"; File = "$CaptureBase-04-sort-quick-sync.pcapng" },
    @{ Id = "lighting-static-and-off"; File = "$CaptureBase-05-lighting-static-off.pcapng" },
    @{ Id = "lighting-generated-rainbow"; File = "$CaptureBase-06-lighting-generated-rainbow.pcapng" }
)

$scenarioCommands = @{
    "baseline" = @{ Operation = "live-pwm"; PwmValues = "66,55,44,33" }
    "direct-fan-speed" = @{ Operation = "live-pwm"; PwmValues = "66,55,44,33" }
    "motherboard-pwm-sync" = @{ Operation = "live-pwm-sync"; MotherboardPwm = 90 }
    "rf-rebind" = @{ Operation = "live-rgb"; Color = "64,64,64" }
    "sort-quick-sync" = @{ Operation = "live-pwm-sync"; MotherboardPwm = 120 }
    "lighting-static-and-off" = @{ Operation = "live-rgb"; Color = "0,0,0"; EffectIndex = 1 }
    "lighting-generated-rainbow" = @{ Operation = "live-rainbow"; FrameCount = 24; IntervalMs = 50; EffectIndex = 1 }
}

$steps = New-Object System.Collections.Generic.List[PSObject]

$steps.Add([PSCustomObject]@{ Name = "设备扫描（非写入）"; Command = @("tools/lianli_wireless_probe.py", "scan") })
if (-not $SkipLive) {
    $steps.Add([PSCustomObject]@{ Name = "Live 权限与可见性"; Command = @("tools/lianli_wireless_probe.py", "live-list") })
} else {
    Write-Host "跳过 live-list（-SkipLive），如需权限验证请后续手工执行 live-list" -ForegroundColor Yellow
}

$steps.Add([PSCustomObject]@{ Name = "生成 runbook"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $runbookJson, "windows-capture-runbook", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir) })
$steps.Add([PSCustomObject]@{ Name = "按场景生成 note"; Command = @("tools/lianli_wireless_probe.py", "windows-capture-note-batch", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir, "--write-files") })
$steps.Add([PSCustomObject]@{ Name = "生成 capture checklist"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $checklistJson, "windows-capture-checklist", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir, "--max-tasks", "8") })
$steps.Add([PSCustomObject]@{ Name = "生成 capture queue"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $queueJson, "windows-capture-queue", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir) })
$steps.Add([PSCustomObject]@{ Name = "生成 Windows 抓包计划"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $capturePlanJson, "windows-capture-plan", "--version", $Version, "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir) })

if (-not $SkipLinux) {
    $captureFiles = @()
    if (Test-Path -LiteralPath $CaptureDir) {
        $captureFiles = Get-ChildItem -LiteralPath $CaptureDir -Filter "*.pcapng" -File -ErrorAction SilentlyContinue
    }
    $steps.Add([PSCustomObject]@{ Name = "Linux ingest（兼容）"; Command = @("tools/lianli_wireless_probe.py", "windows-capture-ingest", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir, "--target-context-from", (Join-Path $ArtifactDir "hardware")) })
    $steps.Add([PSCustomObject]@{ Name = "capture set report"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $setJson, "capture-set-report", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase) })
    $steps.Add([PSCustomObject]@{ Name = "capture gap report"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $gapJson, "capture-gap-report", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase) })
    if ($captureFiles.Count -gt 0) {
        foreach ($captureFile in $captureFiles) {
            $stem = [IO.Path]::GetFileNameWithoutExtension($captureFile.Name)
            $steps.Add([PSCustomObject]@{ Name = ("capture triage report: {0}" -f $captureFile.Name); Command = @("tools/lianli_wireless_probe.py", "--save-json", (Join-Path $CaptureDir ("capture-triage-report-{0}.json" -f $stem)), "capture-triage-report", $captureFile.FullName) })
            $steps.Add([PSCustomObject]@{ Name = ("capture signature match: {0}" -f $captureFile.Name); Command = @("tools/lianli_wireless_probe.py", "--save-json", (Join-Path $CaptureDir ("capture-signature-match-{0}.json" -f $stem)), "capture-signature-match", $captureFile.FullName) })
        }
    } else {
        $steps.Add([PSCustomObject]@{ Name = "capture triage report"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $triageJson, "summarize-captures", $CaptureDir) })
    }
    $steps.Add([PSCustomObject]@{ Name = "validation gate"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $validationJson, "lianli-validation-gate", "--capture-dir", $CaptureDir, "--hardware-dir", (Join-Path $ArtifactDir "hardware"), "--capture-base", $CaptureBase, "--artifact-dir", $ArtifactDir, "--version", $Version) })
    $steps.Add([PSCustomObject]@{ Name = "linux control manifest"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $controlManifestJson, "linux-control-manifest", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase) })
    $steps.Add([PSCustomObject]@{ Name = "linux-control target registry"; Command = @("tools/lianli_wireless_probe.py", "--save-json", $registryJson, "linux-control-target-registry", $CaptureDir, "--version", $Version, "--capture-base", $CaptureBase) })
} else {
    Write-Host "已跳过 Linux 侧 ingest/report 步骤（-SkipLinux）" -ForegroundColor Yellow
}

$commandLines = New-Object System.Collections.Generic.List[string]
$commandLines.Add("# Windows + Linux 联力官方抓包逆向闭环")
$commandLines.Add("")

foreach ($step in $steps) {
    $commandLines.Add((New-CommandText $step.Command))
}

$candidateTargetId = $TargetId
if ([string]::IsNullOrWhiteSpace($candidateTargetId) -and (Test-Path -LiteralPath $registryJson)) {
    $candidateTargetId = New-TargetIdFromRegistry $registryJson
}
if ([string]::IsNullOrWhiteSpace($candidateTargetId)) {
    $candidateTargetId = "<target-id>"
}

$scenarioComparePlans = New-Object System.Collections.Generic.List[PSObject]

foreach ($scenario in $scenarios) {
    $captureFile = Join-Path $CaptureDir $scenario.File
    $scenarioComparePlans.Add([PSCustomObject]@{
        Scenario = $scenario.Id
        CaptureFile = $captureFile
        CompareJson = Join-Path $CaptureDir ("linux-control-packet-compare-{0}.json" -f $scenario.Id)
        Operation = $null
        Args = $null
    })

    if (-not $scenarioCommands.ContainsKey($scenario.Id)) {
        $commandLines.Add("# scenario={0} 无映射 compare，不自动执行" -f $scenario.Id)
        continue
    }

    $operationPlan = $scenarioCommands[$scenario.Id]
    $compareArgs = @(
        "tools/lianli_wireless_probe.py",
        "--save-json",
        (Join-Path $CaptureDir ("linux-control-packet-compare-{0}.json" -f $scenario.Id)),
        "linux-control-packet-compare",
        $CaptureDir,
        $captureFile,
        $operationPlan.Operation,
        "--target-id",
        $candidateTargetId
    )

    if ($operationPlan.PwmValues) {
        $compareArgs += @("--pwm-values", [string]$operationPlan.PwmValues)
    }
    if ($operationPlan.MotherboardPwm) {
        $compareArgs += @("--motherboard-pwm", [string]$operationPlan.MotherboardPwm)
    }
    if ($operationPlan.Color) {
        $compareArgs += @("--color", [string]$operationPlan.Color)
    }
    if ($operationPlan.FrameCount) {
        $compareArgs += @("--frame-count", [string]$operationPlan.FrameCount)
    }
    if ($operationPlan.IntervalMs) {
        $compareArgs += @("--interval-ms", [string]$operationPlan.IntervalMs)
    }
    if ($operationPlan.EffectIndex) {
        $compareArgs += @("--effect-index", [string]$operationPlan.EffectIndex)
    }

    $scenarioComparePlans[-1].Operation = $operationPlan.Operation
    $scenarioComparePlans[-1].Args = $compareArgs
    $commandLines.Add((New-CommandText $compareArgs))
}

Set-Content -Encoding UTF8 -Path $commandsOutput -Value $commandLines
Write-Host "已生成命令清单: $commandsOutput"

if (-not $Run) {
    Write-Host "未使用 -Run，默认仅生成闭环命令清单。"
    Write-Host "执行示例: .\\scripts\\lianli-reverse-operator-plan.ps1 -Run"
    return
}

$summaryRows = New-Object System.Collections.Generic.List[hashtable]
foreach ($step in $steps) {
    try {
        Invoke-Step -PythonExe $pythonExe -Title $step.Name -CommandArgs $step.Command -NoExecute:$DryRun
        $summaryRows.Add(@{ scenario = "pipeline"; step = $step.Name; status = "ok" })
        continue
    } catch {
        if ($step.Name -eq "Live 权限与可见性") {
            Write-Host "live-list 执行失败，已跳过并继续后续非写入步骤：" -ForegroundColor Yellow
            Write-Host "  $($_.Exception.Message)" -ForegroundColor Yellow
            $summaryRows.Add(@{ scenario = "pipeline"; step = $step.Name; status = "failed-noncritical" })
            continue
        }
        throw
    }
}

foreach ($plan in $scenarioComparePlans) {
    if (-not (Test-Path -LiteralPath $plan.CaptureFile)) {
        Write-Host "跳过缺失 pcap: $($plan.CaptureFile)" -ForegroundColor Yellow
        $summaryRows.Add(@{ scenario = $plan.Scenario; step = "packet-compare"; status = "skipped-missing-pcap" })
        continue
    }
    if ($null -eq $plan.Args) {
        Write-Host "跳过无映射 compare: $($plan.Scenario)" -ForegroundColor Yellow
        $summaryRows.Add(@{ scenario = $plan.Scenario; step = "packet-compare"; status = "skipped-no-operation-map" })
        continue
    }
    Invoke-Step -PythonExe $pythonExe -Title ("packet-compare: {0}" -f $plan.Scenario) -CommandArgs $plan.Args -NoExecute:$DryRun

    if (-not $DryRun -and (Test-Path -LiteralPath $plan.CompareJson)) {
        try {
            $payload = Get-Content -Raw -LiteralPath $plan.CompareJson | ConvertFrom-Json -ErrorAction Stop
            $compareStatus = [string]$payload.status
            $summaryRows.Add(@{ scenario = $plan.Scenario; step = "packet-compare"; status = $compareStatus; operation = [string]$plan.Operation })
        } catch {
            $summaryRows.Add(@{ scenario = $plan.Scenario; step = "packet-compare"; status = "parse-error"; operation = [string]$plan.Operation })
        }
    }
}

$summaryPayload = @{
    operation = "lianli-official-reverse-closure-summary"
    version = $Version
    capture_base = $CaptureBase
    capture_dir = $CaptureDir
    target_id = $candidateTargetId
    steps = @($summaryRows.ToArray())
}
$summaryPayload | ConvertTo-Json -Depth 6 | Set-Content -Path $summaryOutput -Encoding UTF8
Write-Host "闭环摘要: $summaryOutput"
Write-Host "执行完成（如含未识别/缺失项请按需补齐 pcap 或修正 -TargetId）。"
