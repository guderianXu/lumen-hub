param(
    [switch]$OneFile,
    [switch]$Clean,
    [switch]$SkipInstall
)

# Usage: powershell -ExecutionPolicy Bypass -File packaging/windows/build-exe.ps1 -Clean -OneFile
$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BuildScript = Join-Path $RepoRoot "tools/build_package.py"
$Python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $Python) {
    throw "python was not found on PATH. Install Python 3.10+ or run this from the existing project environment."
}

$ArgsList = @($BuildScript)
if ($OneFile) {
    $ArgsList += "--onefile"
}
if ($Clean) {
    $ArgsList += "--clean"
}
if ($SkipInstall) {
    $ArgsList += "--skip-install"
}

& $Python.Source @ArgsList
