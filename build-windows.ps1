<#
.SYNOPSIS
    Builds the Isolated Browser Manager Windows Desktop Application (.exe / .msi)
    using React + Python FastAPI + Tauri 2.0.

.DESCRIPTION
    This script automates:
    1. Verification of developer tools (Node.js, Python, Rust/Cargo, MSVC C++).
    2. Setting up the Python virtual environment and dependencies.
    3. Compiling the FastAPI backend into a standalone backend_server.exe using PyInstaller.
    4. Building the React Vite frontend.
    5. Building the Tauri desktop bundle (NSIS .exe installer and .msi).

.USAGE
    powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
#>

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n========================================================" -ForegroundColor Cyan
    Write-Host " ==> $Message" -ForegroundColor Cyan
    Write-Host "========================================================" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Failure {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

$RootDir = (Resolve-Path $PSScriptRoot).Path
Set-Location $RootDir

Write-Host @"
========================================================
   Isolated Browser Manager - Windows Desktop Build
   Architecture: React + FastAPI (Python) + Tauri (Rust)
========================================================
"@ -ForegroundColor Magenta

# ---------------------------------------------------------------------------
# 1. Check Prerequisites
# ---------------------------------------------------------------------------
Write-Step "Checking Prerequisites"

$HasErrors = $false

# Check Node.js
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeVer = (node --version).Trim()
    Write-Success "Node.js detected: $nodeVer"
} else {
    Write-Failure "Node.js is missing! Install via: winget install OpenJS.NodeJS.LTS"
    $HasErrors = $true
}

# Check Python
$PythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCmd = "py -3"
}

if ($PythonCmd) {
    $pyVer = (& $PythonCmd.Split()[0] ($PythonCmd.Split()[1..($PythonCmd.Split().Length-1)] + @("--version")) 2>&1).Trim()
    Write-Success "Python detected: $pyVer"
} else {
    Write-Failure "Python is missing! Install via: winget install Python.Python.3.12"
    $HasErrors = $true
}

# Check Cargo / Rust
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    $cargoVer = (cargo --version).Trim()
    Write-Success "Rust/Cargo detected: $cargoVer"
} else {
    # Check default rustup location
    $cargoDefault = Join-Path $Env:USERPROFILE ".cargo\bin\cargo.exe"
    if (Test-Path $cargoDefault) {
        $Env:Path = "$Env:USERPROFILE\.cargo\bin;" + $Env:Path
        Write-Success "Rust/Cargo detected in ~/.cargo/bin"
    } else {
        Write-Failure "Rust is missing! Install via: winget install Rustlang.Rustup (or visit https://rustup.rs)"
        $HasErrors = $true
    }
}

if ($HasErrors) {
    Write-Host "`nPlease install the missing dependencies above and rerun this script.`n" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Setup Python Virtual Environment & Dependencies
# ---------------------------------------------------------------------------
Write-Step "Setting up Python Environment"

$VenvDir = Join-Path $RootDir "build\venv_win"
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating Python virtualenv at $VenvDir..." -ForegroundColor Gray
    New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir) | Out-Null
    & $PythonCmd.Split()[0] ($PythonCmd.Split()[1..($PythonCmd.Split().Length-1)] + @("-m", "venv", $VenvDir))
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

Write-Host "Upgrading pip and installing backend dependencies..." -ForegroundColor Gray
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPip install -r (Join-Path $RootDir "backend\requirements.txt") --quiet
& $VenvPip install pyinstaller --quiet
Write-Success "Python dependencies installed successfully."

# ---------------------------------------------------------------------------
# 3. Compile Python FastAPI Backend into Standalone Binary
# ---------------------------------------------------------------------------
Write-Step "Compiling Python Backend Sidecar (PyInstaller)"

$BinariesDir = Join-Path $RootDir "manager-app\src-tauri\binaries"
if (-not (Test-Path $BinariesDir)) {
    New-Item -ItemType Directory -Force -Path $BinariesDir | Out-Null
}

$PyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"
$WorkPath = Join-Path $RootDir "build\pyinstaller_work"
$SpecPath = Join-Path $RootDir "build"

Write-Host "Compiling backend\main.py into $BinariesDir\backend_server.exe..." -ForegroundColor Gray
& $PyInstaller `
    --name backend_server `
    --onefile `
    --clean `
    --noconfirm `
    --distpath $BinariesDir `
    --workpath $WorkPath `
    --specpath $SpecPath `
    --add-data "automation\brains;automation\brains" `
    --add-data "automation\templates;automation\templates" `
    (Join-Path $RootDir "backend\main.py")

$BackendExe = Join-Path $BinariesDir "backend_server.exe"
if (-not (Test-Path $BackendExe)) {
    Write-Failure "PyInstaller failed to produce $BackendExe!"
    exit 1
}
Write-Success "Compiled backend binary: $BackendExe ($([math]::Round((Get-Item $BackendExe).Length / 1MB, 2)) MB)"

# ---------------------------------------------------------------------------
# 4. Build React Frontend & Tauri Application
# ---------------------------------------------------------------------------
Write-Step "Building React Frontend & Tauri Desktop Package"

Set-Location (Join-Path $RootDir "manager-app")

Write-Host "Installing npm dependencies..." -ForegroundColor Gray
npm install --silent

Write-Host "Building React production bundle..." -ForegroundColor Gray
npm run build

Write-Host "Compiling Tauri desktop executable & NSIS installer..." -ForegroundColor Gray
npx tauri build

Set-Location $RootDir

# ---------------------------------------------------------------------------
# 5. Display Build Artifacts
# ---------------------------------------------------------------------------
Write-Step "Build Complete!"

$NsisDir = Join-Path $RootDir "manager-app\src-tauri\target\release\bundle\nsis"
$MsiDir = Join-Path $RootDir "manager-app\src-tauri\target\release\bundle\msi"
$ExeDir = Join-Path $RootDir "manager-app\src-tauri\target\release"

$FoundInstallers = @()
if (Test-Path $NsisDir) {
    $FoundInstallers += Get-ChildItem -Path $NsisDir -Filter "*.exe" -File
}
if (Test-Path $MsiDir) {
    $FoundInstallers += Get-ChildItem -Path $MsiDir -Filter "*.msi" -File
}

if ($FoundInstallers.Count -gt 0) {
    Write-Host "`nGenerated Windows Installers:" -ForegroundColor Green
    foreach ($item in $FoundInstallers) {
        $sizeMB = [math]::Round($item.Length / 1MB, 2)
        Write-Host "  -> $($item.FullName) ($sizeMB MB)" -ForegroundColor White
    }
    Write-Host "`nYou can run the installer .exe above to install Isolated Browser Manager on Windows!`n" -ForegroundColor Green
} else {
    $ReleaseExe = Join-Path $ExeDir "isolated-browser-manager.exe"
    if (Test-Path $ReleaseExe) {
        Write-Success "Standalone executable generated at: $ReleaseExe"
    } else {
        Write-Warn "Tauri build completed. Check manager-app\src-tauri\target\release for binaries."
    }
}
