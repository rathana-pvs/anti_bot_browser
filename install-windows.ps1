param(
    [ValidateSet("check", "install", "repair")]
    [string]$Mode = "install"
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Automat FB Beta Windows / WSL2 Bootstrap"
Write-Host " Mode: $Mode"
Write-Host "==================================================" -ForegroundColor Cyan

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    if ($Mode -eq "check") {
        Write-Error "WSL is not installed. Run this script as Administrator with -Mode install."
    }
    if (-not (Test-Administrator)) {
        Write-Error "Installing WSL requires Administrator permission. Reopen PowerShell as Administrator."
    }
    Write-Host "Enabling Windows Subsystem for Linux and Virtual Machine Platform..." -ForegroundColor Yellow
    & dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
    & dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
    Write-Host "Restart Windows, then rerun this script to install Ubuntu." -ForegroundColor Yellow
    exit 10
}

$distros = @(& wsl.exe --list --quiet | ForEach-Object { $_.Replace([char]0, "").Trim() } | Where-Object { $_ })
$ubuntu = $distros | Where-Object { $_ -match "Ubuntu" } | Select-Object -First 1
if (-not $ubuntu) {
    if ($Mode -eq "check") {
        Write-Error "No Ubuntu WSL distribution was found. Run with -Mode install."
    }
    if (-not (Test-Administrator)) {
        Write-Error "Installing Ubuntu requires Administrator permission. Reopen PowerShell as Administrator."
    }
    Write-Host "Installing Ubuntu for WSL2..." -ForegroundColor Yellow
    & wsl.exe --install -d Ubuntu
    Write-Host "Open Ubuntu once to create its user, then rerun this script." -ForegroundColor Yellow
    exit 10
}
Write-Host "OK: WSL distribution detected: $ubuntu" -ForegroundColor Green

$dockerDesktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
if (-not (Test-Path $dockerDesktop)) {
    if ($Mode -eq "check") {
        Write-Error "Docker Desktop is missing. Install it and enable integration for $ubuntu."
    }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Write-Error "Docker Desktop is missing and winget is unavailable. Install Docker Desktop manually."
    }
    $answer = Read-Host "Docker Desktop is missing. Install it with winget now? [y/N]"
    if ($answer -notmatch '^(y|yes)$') {
        Write-Error "Docker Desktop is required. Installation was cancelled."
    }
    & winget.exe install --exact --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
    Write-Host "Start Docker Desktop, accept its terms, enable WSL integration for $ubuntu, then rerun this script." -ForegroundColor Yellow
    exit 11
}

try {
    & wsl.exe -d $ubuntu -- docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw "Docker not ready" }
    Write-Host "OK: Docker Desktop is available inside WSL2" -ForegroundColor Green
} catch {
    if (Test-Path $dockerDesktop) {
        Start-Process $dockerDesktop
    }
    Write-Host "Docker Desktop was started. Enable Settings > Resources > WSL Integration for $ubuntu." -ForegroundColor Yellow
    Write-Host "Wait until Docker reports Ready, then rerun this script." -ForegroundColor Yellow
    exit 12
}

$windowsRoot = (Resolve-Path $PSScriptRoot).Path
$wslSource = (& wsl.exe -d $ubuntu -- wslpath -a $windowsRoot).Trim()
if (-not $wslSource) {
    Write-Error "Could not translate the release path into WSL."
}

$wslDestination = (& wsl.exe -d $ubuntu -- bash -lc 'printf "%s" "$HOME/automat_fb-beta"').Trim()
& wsl.exe -d $ubuntu -- test -f "$wslDestination/install.sh"
$destinationExists = $LASTEXITCODE -eq 0
if (-not $destinationExists) {
    Write-Host "Copying the beta from Windows storage into the WSL filesystem..." -ForegroundColor Cyan
    & wsl.exe -d $ubuntu -- bash -c 'mkdir -p "$1"; cp -a "$2/." "$1/"' bash $wslDestination $wslSource
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not copy the beta into WSL." }
} else {
    Write-Host "Using existing WSL installation at $wslDestination" -ForegroundColor Cyan
}

Write-Host "Running Linux installer inside WSL2..." -ForegroundColor Cyan
& wsl.exe -d $ubuntu -- bash "$wslDestination/install.sh" "--$Mode"
$installerExit = $LASTEXITCODE
if ($installerExit -ne 0) {
    Write-Error "The WSL installer stopped with exit code $installerExit. Follow its repair instruction and rerun this script with -Mode repair."
}

Write-Host "READY: Installation completed." -ForegroundColor Green
Write-Host "Run this command to start the product:"
Write-Host "wsl.exe -d $ubuntu -- bash -lc 'cd ~/automat_fb-beta/manager-app && npm start'" -ForegroundColor White
Write-Host "Then open http://localhost:5173 in Windows."
