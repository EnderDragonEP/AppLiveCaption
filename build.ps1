<#
.SYNOPSIS
    Builds the one-folder AppLiveCaption.exe with PyInstaller.

.DESCRIPTION
    Uses an installed Python 3.11 or 3.12 (the one "py -3.12" or "py -3.11"
    finds, otherwise "python" on PATH), runs the unit tests, then builds
    dist\AppLiveCaption\. Models and NVIDIA's CUDA libraries are not bundled;
    the app downloads them when the user asks.

    The app is portable and keeps its settings, history, logs, models and GPU
    support next to the exe. Everything in dist\AppLiveCaption\ other than the
    build output (AppLiveCaption.exe and _internal) is kept across builds.

    With -Installer it also builds dist\AppLiveCaption-<version>-Setup.exe with
    Inno Setup 6 (installer\AppLiveCaption.iss). The installed app keeps its
    data in the user's profile instead (%APPDATA% and %LOCALAPPDATA%).

.PARAMETER Python
    Path to the python.exe to build with, instead of finding one.

.PARAMETER Console
    Keep a console window in the built app, for debugging.

.PARAMETER Clean
    Delete earlier build output and PyInstaller's cache before building
    (settings, models and other files in dist\AppLiveCaption\ are kept).

.PARAMETER Install
    Run "pip install -r requirements.txt" for that Python before building.
    Packages you already have are kept when their version works.

.PARAMETER SkipTests
    Don't run the unit tests before building.

.PARAMETER Installer
    Also build the Setup.exe with Inno Setup 6 (winget install JRSoftware.InnoSetup).

.EXAMPLE
    .\build.ps1

.EXAMPLE
    .\build.ps1 -Clean

.EXAMPLE
    .\build.ps1 -Python C:\Python312\python.exe

.EXAMPLE
    .\build.ps1 -Installer
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$Console,
    [switch]$Clean,
    [switch]$Install,
    [switch]$SkipTests,
    [switch]$Installer
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$distDir = Join-Path $root 'dist\AppLiveCaption'
# PyInstaller replaces the whole output folder. Anything else in it (the app's
# settings, history, logs, models and GPU support, or your own files) waits
# here during the build.
$buildOutput = 'AppLiveCaption.exe', '_internal'
$keptDir = Join-Path $root 'dist\AppLiveCaption.kept'

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

function Save-UserFiles {
    if (-not (Test-Path -LiteralPath $distDir)) { return }
    $items = @(Get-ChildItem -LiteralPath $distDir -Force | Where-Object { $buildOutput -notcontains $_.Name })
    if (-not $items) { return }
    New-Item -ItemType Directory -Force $keptDir | Out-Null
    foreach ($item in $items) { Move-Item -LiteralPath $item.FullName -Destination $keptDir }
    Write-Host "Keeping $($items.Count) item(s) in dist\AppLiveCaption that aren't build output: $($items.Name -join ', ')"
}

function Restore-UserFiles {
    if (-not (Test-Path -LiteralPath $keptDir)) { return }
    New-Item -ItemType Directory -Force $distDir | Out-Null
    foreach ($item in @(Get-ChildItem -LiteralPath $keptDir -Force)) {
        if (Test-Path -LiteralPath (Join-Path $distDir $item.Name)) {
            Write-Warning "Not putting back $($item.Name): the build created one. Yours is in $keptDir."
            continue
        }
        Move-Item -LiteralPath $item.FullName -Destination $distDir
    }
    if (-not (Get-ChildItem -LiteralPath $keptDir -Force)) { Remove-Item -LiteralPath $keptDir }
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

function Find-InnoSetup {
    $onPath = Get-Command ISCC -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    # Where winget (current user) and the Inno Setup installer (all users) put it.
    foreach ($base in @("$env:LOCALAPPDATA\Programs", ${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if (-not $base) { continue }
        $exe = Join-Path $base 'Inno Setup 6\ISCC.exe'
        if (Test-Path $exe) { return $exe }
    }
    throw 'Inno Setup 6 was not found. Install it with:  winget install JRSoftware.InnoSetup   (or from jrsoftware.org)'
}

$timer = [Diagnostics.Stopwatch]::StartNew()
$savedEnv = @{
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
    if ($Installer) { $iscc = Find-InnoSetup }  # before the long build

    if ($Install) {
        Write-Host 'Installing requirements...'
        Invoke-Checked $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements.txt')
    }

    $modules = 'PySide6 qfluentwidgets faster_whisper ctranslate2 onnxruntime pyaudiowpatch comtypes pycaw psutil soxr opencc pypinyin numpy huggingface_hub PyInstaller'
    $check = "import importlib.util as u; print(' '.join(m for m in '$modules'.split() if u.find_spec(m) is None))"
    $missing = Get-Output $Python @('-c', $check)
    if ($null -eq $missing) { throw "Could not run $Python." }
    if ($missing) {
        throw "This Python is missing: $missing. Run:  & `"$Python`" -m pip install -r requirements.txt   (or .\build.ps1 -Install)"
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
    Restore-UserFiles  # left over if an earlier build was stopped mid-way
    Save-UserFiles
    if ($Clean) {
        Write-Host 'Removing earlier build output...'
        foreach ($dir in (Join-Path $root 'build'), $distDir) {
            if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
        }
    }
    $env:ALC_CONSOLE = if ($Console) { '1' } else { '0' }
    $pyiArgs = @('-m', 'PyInstaller', 'AppLiveCaption.spec', '--noconfirm')
    if ($Clean) { $pyiArgs += '--clean' }
    Write-Host "Building (console: $Console)..."
    Invoke-Checked $Python $pyiArgs

    $exe = Join-Path $distDir 'AppLiveCaption.exe'
    if (-not (Test-Path $exe)) {
        throw "The build finished but $exe is missing."
    }
    $output = $buildOutput | ForEach-Object { Join-Path $distDir $_ }
    $sizeMB = (Get-ChildItem $output -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB

    # 4. Installer
    if ($Installer) {
        $appVersion = Get-Output $Python @('-c', 'import app; print(app.__version__)')
        if (-not $appVersion) { throw 'Could not read the version from app\__init__.py.' }
        Write-Host "Building the installer for version $appVersion..."
        Invoke-Checked $iscc @('/Qp', "/DAppVersion=$appVersion", (Join-Path $root 'installer\AppLiveCaption.iss'))
        $setup = Join-Path $root "dist\AppLiveCaption-$appVersion-Setup.exe"
        if (-not (Test-Path $setup)) { throw "Inno Setup finished but $setup is missing." }
    }

    Write-Host ''
    Write-Host ('Done in {0:mm\:ss}. Output: {1} ({2:N0} MB)' -f $timer.Elapsed, $exe, $sizeMB) -ForegroundColor Green
    if ($Installer) {
        Write-Host ('Installer: {0} ({1:N0} MB)' -f $setup, ((Get-Item $setup).Length / 1MB)) -ForegroundColor Green
    }
    $failed = $false
}
catch {
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Restore-UserFiles
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
