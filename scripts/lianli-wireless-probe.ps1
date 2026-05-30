param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ProbeArgs
)

function Resolve-PythonExe {
    $candidates = @("py", "python3", "python")
    foreach ($candidate in $candidates) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $found) {
            continue
        }
        try {
            $version = & $found.Source --version 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -match "Python") {
                return $found.Source
            }
        } catch {
            continue
        }
    }
    throw "No python3 executable found. Install Python 3 and make sure it is on PATH."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$probeScript = Join-Path $repoRoot.Path "tools\lianli_wireless_probe.py"

if (-not (Test-Path -LiteralPath $probeScript)) {
    throw "Missing lianli_wireless_probe.py: $probeScript"
}

$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $repoRoot.Path
} else {
    "$($repoRoot.Path);$env:PYTHONPATH"
}

$pythonExe = Resolve-PythonExe
& $pythonExe $probeScript @ProbeArgs
exit $LASTEXITCODE
