param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONUSERBASE = (Join-Path $repoRoot ".pyuser")
New-Item -ItemType Directory -Force -Path $env:PYTHONUSERBASE | Out-Null

if ($Clean) {
    Remove-Item -LiteralPath "build", "dist" -Recurse -Force -ErrorAction SilentlyContinue
}

$null = cmd /c "python -B -c ""import PyInstaller"" >NUL 2>NUL"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Install it with: python -m pip install pyinstaller"
}

python -B -m PyInstaller `
    --noconfirm `
    --clean `
    "local_llm_clients\packaging\agent_client_textual.spec"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Built: dist\agent_client_textual\agent_client_textual.exe"
