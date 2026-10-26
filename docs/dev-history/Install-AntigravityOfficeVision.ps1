param(
  [Parameter(Mandatory=$true)]
  [string]$ProjectPath,
  [switch]$InstallUv,
  [switch]$InstallGlobalRules
)

$ErrorActionPreference = 'Stop'

Write-Host "[1/5] Checking project path..."
$project = (Resolve-Path $ProjectPath).Path

Write-Host "[2/5] Checking Antigravity CLI..."
$agy = Get-Command agy -ErrorAction SilentlyContinue
if (-not $agy) {
  Write-Host "Antigravity CLI not found. Installing the official Windows CLI..."
  irm https://antigravity.google/cli/install.ps1 | iex
}

if (-not (Get-Command agy -ErrorAction SilentlyContinue)) {
  throw "Antigravity CLI is still not available on PATH. Restart PowerShell and run this script again."
}

Write-Host "[3/5] Installing workspace customization..."
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRoot = Split-Path -Parent $sourceRoot

Copy-Item (Join-Path $sourceRoot 'GEMINI.md') $project -Force
Copy-Item (Join-Path $sourceRoot 'AGENTS.md') $project -Force
Copy-Item (Join-Path $sourceRoot 'BOOTSTRAP_PROMPT.md') $project -Force
Copy-Item (Join-Path $sourceRoot 'docs') $project -Recurse -Force
Copy-Item (Join-Path $sourceRoot '.agents') $project -Recurse -Force

Write-Host "[4/5] Writing conservative CLI settings..."
$cfgRoot = Join-Path $HOME '.gemini/antigravity-cli'
New-Item -ItemType Directory -Path $cfgRoot -Force | Out-Null
$settingsPath = Join-Path $cfgRoot 'settings.json'

if (Test-Path $settingsPath) {
  $existing = Get-Content $settingsPath -Raw | ConvertFrom-Json
} else {
  $existing = [pscustomobject]@{}
}

# Keep existing user settings. We only set safe defaults if absent.
if (-not ($existing.PSObject.Properties.Name -contains 'toolPermission')) {
  $existing | Add-Member -NotePropertyName toolPermission -NotePropertyValue 'proceed-in-sandbox'
}
if (-not ($existing.PSObject.Properties.Name -contains 'enableTerminalSandbox')) {
  $existing | Add-Member -NotePropertyName enableTerminalSandbox -NotePropertyValue $true
}
if (-not ($existing.PSObject.Properties.Name -contains 'agentMode')) {
  $existing | Add-Member -NotePropertyName agentMode -NotePropertyValue 'plan'
}
$existing | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

if ($InstallGlobalRules) {
  Write-Host "[5/5] Installing global GEMINI.md rules..."
  Copy-Item (Join-Path $sourceRoot 'GEMINI.md') (Join-Path $HOME '.gemini/GEMINI.md') -Force
} else {
  Write-Host "[5/5] Global rules not changed. Use -InstallGlobalRules only if desired."
}

if ($InstallUv -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv for the AWS Documentation MCP server..."
  irm https://astral.sh/uv/install.ps1 | iex
}

Write-Host ""
Write-Host "Antigravity project bootstrap installed." -ForegroundColor Green
Write-Host "Next: cd `"$project`" and run: agy --mode=plan --model=gemini-3.1-pro-high"
Write-Host "Then paste BOOTSTRAP_PROMPT.md"
