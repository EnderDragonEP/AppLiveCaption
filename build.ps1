<#
.SYNOPSIS
    Builds the one-folder AppLiveCaption.exe with PyInstaller.

.DESCRIPTION
    Uses an installed Python 3.11 or 3.12 (the one "py -3.12" or "py -3.11"
    finds, otherwise "python" on PATH), runs the unit tests, then builds
    dist\AppLiveCaption\. Models are not bundled; the app downloads them on
    first use.

.PARAMETER Python
    Path to the python.exe to build with, instead of finding one.

.PARAMETER NoTorch
    Leave out PyTorch and transformers. Breeze ASR 25 won't be available, but the
    build is much smaller. The Whisper models can still use the GPU if the
    nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels are installed.

.PARAMETER Console
    Keep a console window in the built app, for debugging.

.PARAMETER Clean
    Delete earlier build output and PyInstaller's cache before building.

.PARAMETER Install
    Run "pip install -r requirements.txt" for that Python before building.
    Packages you already have are kept when their version works.

.PARAMETER SkipTests
    Don't run the unit tests before building.

.EXAMPLE
    .\build.ps1

.EXAMPLE
    .\build.ps1 -NoTorch -Clean

.EXAMPLE
    .\build.ps1 -Python C:\Python312\python.exe
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$NoTorch,
    [switch]$Console,
    [switch]$Clean,
    [switch]$Install,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$distDir = Join-Path $root 'dist\AppLiveCaption'

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
    }
}

function Get-Output {
    # The command's stdout, or $null if it fails. Native stderr must not turn
    # into a terminating error here.
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2> $null
        if ($LASTEXITCODE -eq 0) { return ($out -join "`n").Trim() }
        return $null
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Find-Python {
    $probe = @('-c', 'import sys; print(sys.executable)')
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in '3.12', '3.11') {
            $exe = Get-Output 'py' (@("-$version") + $probe)
            if ($exe) { return $exe }
        }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) {
        $exe = Get-Output $onPath.Source $probe
        if ($exe) { return $exe }
    }
    throw 'Python 3.11 or 3.12 (64-bit) was not found. Install it from python.org, or pass -Python <path to python.exe>.'
}

$timer = [Diagnostics.Stopwatch]::StartNew()
$savedEnv = @{
    ALC_INCLUDE_TORCH = $env:ALC_INCLUDE_TORCH
    ALC_CONSOLE       = $env:ALC_CONSOLE
    HF_HUB_OFFLINE    = $env:HF_HUB_OFFLINE
}

$failed = $true
Push-Location $root
try {
    # 1. Python
    if (-not $Python) { $Python = Find-Python }
    if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
    # No quotes inside the code: Windows PowerShell 5.1 strips them from native arguments.
    $version = Get-Output $Python @('-c', 'import sys; print(*sys.version_info[:2], sep=chr(46))')
    if (-not $version -or [version]$version -lt [version]'3.11') {
        throw "AppLiveCaption needs Python 3.11 or newer; $Python is Python $version."
    }
    Write-Host "Using Python $version`: $Python"

    if ($Install) {
        Write-Host 'Installing requirements...'
        Invoke-Checked $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements.txt')
    }

    $modules = 'PySide6 qfluentwidgets faster_whisper ctranslate2 onnxruntime pyaudiowpatch comtypes pycaw psutil soxr opencc numpy huggingface_hub PyInstaller'
    if (-not $NoTorch) { $modules += ' torch transformers' }
    $check = "import importlib.util as u; print(' '.join(m for m in '$modules'.split() if u.find_spec(m) is None))"
    $missing = Get-Output $Python @('-c', $check)
    if ($null -eq $missing) { throw "Could not run $Python." }
    $installHint = "Run:  & `"$Python`" -m pip install -r requirements.txt   (or .\build.ps1 -Install)"
    if ($missing) {
        $hint = $installHint
        if (-not $NoTorch -and ($missing -match 'torch|transformers')) { $hint += ', or build without Breeze ASR 25 using -NoTorch' }
        throw "This Python is missing: $missing. $hint"
    }
    # Older PyInstaller builds an app in which transformers (Breeze ASR 25) can't load.
    $pyiVersion = Get-Output $Python @('-c', 'import PyInstaller as p; print(*p.__version__.split(chr(46))[:2], sep=chr(46))')
    if (-not $NoTorch -and $pyiVersion -and [version]$pyiVersion -lt [version]'6.22') {
        throw "PyInstaller $pyiVersion is too old for a build with Breeze ASR 25 (needs 6.22 or newer). $installHint, or build with -NoTorch."
    }

    # 2. Tests
    if (-not $SkipTests) {
        Write-Host 'Running unit tests...'
        $env:HF_HUB_OFFLINE = '1'
        Invoke-Checked $Python @('-m', 'unittest', 'discover', '-s', 'tests')
    }

    # 3. Build
    if (Get-Process -Name 'AppLiveCaption' -ErrorAction SilentlyContinue) {
        throw 'AppLiveCaption.exe is running. Close it first so its files can be replaced.'
    }
    if ($Clean) {
        Write-Host 'Removing earlier build output...'
        foreach ($dir in (Join-Path $root 'build'), $distDir) {
            if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
        }
    }
    $env:ALC_INCLUDE_TORCH = if ($NoTorch) { '0' } else { '1' }
    $env:ALC_CONSOLE = if ($Console) { '1' } else { '0' }
    $pyiArgs = @('-m', 'PyInstaller', 'AppLiveCaption.spec', '--noconfirm')
    if ($Clean) { $pyiArgs += '--clean' }
    Write-Host "Building (PyTorch included: $(-not $NoTorch), console: $Console)..."
    Invoke-Checked $Python $pyiArgs

    $exe = Join-Path $distDir 'AppLiveCaption.exe'
    if (-not (Test-Path $exe)) {
        throw "The build finished but $exe is missing."
    }
    $sizeGB = (Get-ChildItem $distDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1GB
    Write-Host ''
    Write-Host ('Done in {0:mm\:ss}. Output: {1} ({2:N1} GB)' -f $timer.Elapsed, $exe, $sizeGB) -ForegroundColor Green
    $failed = $false
}
catch {
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    foreach ($name in $savedEnv.Keys) {
        if ($null -eq $savedEnv[$name]) {
            Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path "Env:$name" -Value $savedEnv[$name]
        }
    }
    Pop-Location
}
if ($failed) { exit 1 }
