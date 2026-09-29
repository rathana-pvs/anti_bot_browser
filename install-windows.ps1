param(
    [ValidateSet("check", "install", "repair")]
    [string]$Mode = "install",
    [ValidateRange(0, 1024)]
    [int]$WslMemoryGB = 0,
    [switch]$KeepWslDefaults,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-Elevated {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Mode", $Mode,
        "-NonInteractive"
    )
    if ($WslMemoryGB -gt 0) { $arguments += @("-WslMemoryGB", $WslMemoryGB) }
    if ($KeepWslDefaults) { $arguments += "-KeepWslDefaults" }
    Write-Host "Requesting administrator approval..." -ForegroundColor Yellow
    $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    exit $process.ExitCode
}

function Get-HostMemoryGB {
    Add-Type -AssemblyName Microsoft.VisualBasic
    $computer = [Microsoft.VisualBasic.Devices.ComputerInfo]::new()
    return [int][Math]::Round($computer.TotalPhysicalMemory / 1GB)
}

function Get-RecommendedWslMemoryGB([int]$HostMemoryGB) {
    if ($HostMemoryGB -lt 24) {
        return [Math]::Max(4, [int][Math]::Floor($HostMemoryGB * 0.5))
    }
    return [Math]::Max(8, [int][Math]::Floor($HostMemoryGB * 0.75))
}

function Set-WslMemoryLimit([string]$ConfigPath, [int]$MemoryGB) {
    $memoryLine = "memory=${MemoryGB}GB"
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        $content = "[wsl2]`r`n${memoryLine}`r`n"
    } else {
        $content = [IO.File]::ReadAllText($ConfigPath)
        $backupPath = "${ConfigPath}.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $ConfigPath -Destination $backupPath
        Write-Host "Backup created: $backupPath" -ForegroundColor DarkGray

        $memoryPattern = [regex]::new('^\s*memory\s*=.*$', [Text.RegularExpressions.RegexOptions]'IgnoreCase, Multiline')
        if ($memoryPattern.IsMatch($content)) {
            $content = $memoryPattern.Replace($content, $memoryLine, 1)
        } else {
            $sectionPattern = [regex]::new('^(\s*\[wsl2\]\s*\r?\n)', [Text.RegularExpressions.RegexOptions]'IgnoreCase, Multiline')
            if ($sectionPattern.IsMatch($content)) {
                $content = $sectionPattern.Replace($content, "`$1${memoryLine}`r`n", 1)
            } else {
                $content = $content.TrimEnd() + "`r`n`r`n[wsl2]`r`n${memoryLine}`r`n"
            }
        }
    }
    [IO.File]::WriteAllText($ConfigPath, $content, [Text.UTF8Encoding]::new($false))
}

function Configure-WslResources {
    $hostMemoryGB = Get-HostMemoryGB
    $recommendedGB = Get-RecommendedWslMemoryGB $hostMemoryGB
    $configPath = Join-Path $Env:USERPROFILE ".wslconfig"
    $existingMemory = $null

    if (Test-Path -LiteralPath $configPath) {
        $match = [regex]::Match([IO.File]::ReadAllText($configPath), '^\s*memory\s*=\s*([^\r\n#;]+)', 'IgnoreCase, Multiline')
        if ($match.Success) { $existingMemory = $match.Groups[1].Value.Trim() }
    }

    Write-Host "Windows memory: ${hostMemoryGB} GB; recommended WSL maximum: ${recommendedGB} GB" -ForegroundColor Cyan
    if ($existingMemory -and $WslMemoryGB -eq 0) {
        Write-Host "Existing .wslconfig memory setting preserved: $existingMemory" -ForegroundColor Green
        return $false
    }
    if ($Mode -eq "check") {
        if (-not $existingMemory) {
            Write-Host "INFO: WSL uses Microsoft's default memory ceiling. Recommended for this host: ${recommendedGB} GB."
        }
        return $false
    }
    if ($KeepWslDefaults) {
        Write-Host "Keeping Microsoft's default WSL memory policy." -ForegroundColor Yellow
        return $false
    }

    $selectedGB = $WslMemoryGB
    if ($selectedGB -eq 0) {
        $choice = if ($NonInteractive) { "Y" } else { Read-Host "Set WSL maximum to the recommended ${recommendedGB} GB? [Y]es / [N]o / [C]ustom (default: Y)" }
        if ($choice -match '^(n|no)$') {
            Write-Host "Keeping Microsoft's default WSL memory policy." -ForegroundColor Yellow
            return $false
        }
        if ($choice -match '^(c|custom)$') {
            $custom = Read-Host "Enter WSL memory maximum in GB"
            if ($custom -notmatch '^\d+$') { throw "WSL memory must be a whole number of GB." }
            $selectedGB = [int]$custom
        } else {
            $selectedGB = $recommendedGB
        }
    }

    $maximumSafeGB = [Math]::Max(4, $hostMemoryGB - 4)
    if ($selectedGB -lt 4 -or $selectedGB -gt $maximumSafeGB) {
        throw "WSL memory must be between 4 GB and ${maximumSafeGB} GB on this machine."
    }

    Set-WslMemoryLimit $configPath $selectedGB
    Write-Host "Configured WSL maximum memory: ${selectedGB} GB" -ForegroundColor Green
    Write-Host "Restarting WSL so the new resource limit is applied..." -ForegroundColor Yellow
    & wsl.exe --shutdown
    Start-Sleep -Seconds 2
    return $true
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
        if ($NonInteractive) { Restart-Elevated }
        Write-Error "Installing WSL requires Administrator permission. Reopen PowerShell as Administrator."
    }
    Write-Host "Enabling Windows Subsystem for Linux and Virtual Machine Platform..." -ForegroundColor Yellow
    & dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
    & dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
    Write-Host "Restart Windows, then rerun this script to install Ubuntu." -ForegroundColor Yellow
    exit 10
}

$distros = @(& wsl.exe --list --quiet | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
$ubuntu = $distros | Where-Object { $_ -match "Ubuntu" } | Select-Object -First 1
if (-not $ubuntu) {
    if ($Mode -eq "check") {
        Write-Error "No Ubuntu WSL distribution was found. Run with -Mode install."
    }
    if (-not (Test-Administrator)) {
        if ($NonInteractive) { Restart-Elevated }
        Write-Error "Installing Ubuntu requires Administrator permission. Reopen PowerShell as Administrator."
    }
    Write-Host "Installing Ubuntu for WSL2..." -ForegroundColor Yellow
    & wsl.exe --install -d Ubuntu
    Write-Host "Open Ubuntu once to create its user, then rerun this script." -ForegroundColor Yellow
    exit 10
}
Write-Host "OK: WSL distribution detected: $ubuntu" -ForegroundColor Green

$wslResourcesChanged = Configure-WslResources

$dockerAvailable = $false
for ($attempt = 1; $attempt -le 10 -and -not $dockerAvailable; $attempt++) {
    try {
        & wsl.exe -d $ubuntu -- docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerAvailable = $true }
    } catch {}
    if (-not $dockerAvailable -and $wslResourcesChanged) { Start-Sleep -Seconds 2 }
    if (-not $wslResourcesChanged) { break }
}

if (-not $dockerAvailable) {
    & wsl.exe -d $ubuntu -u root -- service docker start *> $null
    try {
        & wsl.exe -d $ubuntu -- docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerAvailable = $true }
    } catch {}
}

if ($dockerAvailable) {
    Write-Host "OK: Docker is available inside WSL2 ($ubuntu)" -ForegroundColor Green
} else {
    $dockerDesktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerDesktop)) {
        if ($Mode -eq "check") {
            Write-Error "Docker is missing. Install Docker Desktop or install docker.io inside $ubuntu."
        }
        if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
            Write-Error "Docker Desktop is missing and winget is unavailable. Install Docker Desktop manually."
        }
        $answer = if ($NonInteractive) { "y" } else { Read-Host "Docker Desktop is missing. Install it with winget now? [y/N]" }
        if ($answer -notmatch '^(y|yes)$') {
            Write-Error "Docker is required. Installation was cancelled."
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
}

$windowsRoot = (Resolve-Path $PSScriptRoot).Path
$escapedRoot = $windowsRoot.Replace('\', '\\')
$wslSource = (& wsl.exe -d $ubuntu -- wslpath -a $escapedRoot).Trim()
if (-not $wslSource) {
    Write-Error "Could not translate the release path into WSL."
}

$wslDestination = (& wsl.exe -d $ubuntu -- bash -lc 'printf "%s" "$HOME/automat_fb-beta"').Trim()
& wsl.exe -d $ubuntu -- test -f "$wslDestination/install.sh"
$destinationExists = $LASTEXITCODE -eq 0
Write-Host $(if ($destinationExists) { "Updating the application runtime in WSL..." } else { "Installing the application runtime in WSL..." }) -ForegroundColor Cyan
& wsl.exe -d $ubuntu -- mkdir -p $wslDestination
if ($LASTEXITCODE -ne 0) { Write-Error "Could not create destination directory in WSL." }
& wsl.exe -d $ubuntu -- cp -a "$wslSource/." "$wslDestination/"
if ($LASTEXITCODE -ne 0) { Write-Error "Could not copy the application payload into WSL." }
& wsl.exe -d $ubuntu -- rm -rf "$wslDestination/manager-app/node_modules" "$wslDestination/build"
& wsl.exe -d $ubuntu -- bash -c "find '$wslDestination' -maxdepth 3 -name '*.sh' -exec sed -i 's/\r$//' {} +"

Write-Host "Running Linux installer inside WSL2..." -ForegroundColor Cyan
$linuxInstallerArgs = @("--$Mode")
if ($NonInteractive) {
    Write-Host "Preparing Linux system packages..." -ForegroundColor Cyan
    & wsl.exe -d $ubuntu -u root -- apt-get update
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not update Linux system packages." }
    & wsl.exe -d $ubuntu -u root -- apt-get install -y ca-certificates curl gnupg jq zip unzip rsync python3 python3-venv python3-pip build-essential iproute2
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not install Linux system packages." }
    $linuxInstallerArgs += @("--non-interactive", "--desktop", "--skip-system")
}
& wsl.exe -d $ubuntu -- bash "$wslDestination/install.sh" @linuxInstallerArgs
$installerExit = $LASTEXITCODE
if ($installerExit -ne 0) {
    Write-Error "The WSL installer stopped with exit code $installerExit. Follow its repair instruction and rerun this script with -Mode repair."
}

Write-Host "READY: Installation completed." -ForegroundColor Green
if (-not $NonInteractive) {
    Write-Host "Run this command to start the product:"
    Write-Host "wsl.exe -d $ubuntu -- bash -lc 'cd ~/automat_fb-beta/manager-app && npm start'" -ForegroundColor White
    Write-Host "Then open http://localhost:5173 in Windows."
}
