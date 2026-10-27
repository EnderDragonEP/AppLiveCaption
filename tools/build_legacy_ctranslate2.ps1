<#
Builds the "legacy engine" for older NVIDIA GPUs: CTranslate2's ctranslate2.dll with GPU code for Maxwell
(compute capability 5.0 and 5.2: GeForce GTX 900 series and older GTX 700/800 models) added. The app downloads
it for those GPUs (LEGACY_ENGINE in app/asr/cuda_libs.py) and loads it instead of the bundled ctranslate2.dll.

    powershell -ExecutionPolicy Bypass -File tools\build_legacy_ctranslate2.ps1 [-Version 4.6.2]

It follows CTranslate2's own Windows wheel build (python/tools/prepare_build_environment_windows.sh) with the same
flags, except the GPU architectures: the official build's "Common" list starts at 5.3. One kernel (AWQ dequantization,
unused by Whisper) gets the guard for older GPUs that CTranslate2's other AWQ kernels already have. The version must be the one
the app bundles (requirements.txt), whose ctranslate2.dll the legacy one stands in for.

Needs, installed once (they can be uninstalled afterwards):
  - Visual Studio 2022 Build Tools, "Desktop development with C++" with the MSVC 14.38 toolset (CUDA 12.4
    doesn't accept newer compilers):
        winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --norestart
            --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended
            --add Microsoft.VisualStudio.Component.VC.14.38.17.8.x86.x64"
  - CUDA 12.4 toolkit, only these parts (CUDA 13 can't compile for Maxwell):
        cuda_12.4.0_551.61_windows.exe -s nvcc_12.4 cudart_12.4 cublas_dev_12.4 curand_dev_12.4
  - Intel oneAPI MKL 2025.3 (component intel.oneapi.win.mkl.devel of the oneAPI Base Toolkit 2025.3.0.372 offline
    installer), the version whose libiomp5md.dll CTranslate2 4.6 ships
  - CMake (4.x works: older projects' CMake files get CMAKE_POLICY_VERSION_MINIMUM) and Git
cuDNN 9.1's headers come from NVIDIA's nvidia-cudnn-cu12 wheel (downloaded, not installed); oneDNN 3.1.1 is built
here. Everything goes in -WorkDir.

Output: <WorkDir>\out\ctranslate2-<Version>-maxwell-win_amd64.zip with its SHA-256 and size. Upload it to the
GitHub release legacy-gpu-ct2-<Version>, then put the SHA-256 and size in LEGACY_ENGINE.
#>
param(
    [string]$Version = "4.6.2",
    [string]$WorkDir = "$env:LOCALAPPDATA\AppLiveCaption-build\ct2",
    [string]$Archs = "5.0;5.2;5.3;6.0;6.1;7.0;7.5;8.0;8.6+PTX",
    [int]$Jobs = [Environment]::ProcessorCount
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot

$cuda = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
$oneapi = Join-Path ${env:ProgramFiles(x86)} "Intel\oneAPI"
$cudnnWheel = "https://files.pythonhosted.org/packages/3f/d0/f90ee6956a628f9f04bf467932c0a25e5a7e706a684b896593c06c82f460/nvidia_cudnn_cu12-9.1.0.70-py3-none-win_amd64.whl"
$cudnnSha256 = "6278562929433d68365a07a4a1546c237ba2849852c0d4b2262a486e805b977a"
$onednnVersion = "3.1.1"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Run($exe) {
    # Native tools write warnings to stderr, which Windows PowerShell would turn into errors: judge by the exit code.
    $ErrorActionPreference = "Continue"
    & $exe @args 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$exe failed ($LASTEXITCODE)" }
}

# -- tools ---------------------------------------------------------------------------------------------------
Step "Checking the build tools"
if (-not (Test-Path "$cuda\bin\nvcc.exe")) { throw "CUDA 12.4 not found in $cuda (see the notes at the top)" }
if (-not (Test-Path "$oneapi\mkl\latest\include\mkl.h")) { throw "Intel oneAPI MKL not found in $oneapi" }
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.14.38.17.8.x86.x64 -property installationPath
if (-not $vs) { throw "Visual Studio 2022 with the MSVC 14.38 toolset not found" }
foreach ($tool in "cmake", "git") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found" }
}
New-Item -ItemType Directory -Force "$WorkDir\deps", "$WorkDir\out" | Out-Null
Set-Location $WorkDir
# The MSVC 14.38 compiler's environment, for nvcc and CMake.
cmd /c "`"$vs\VC\Auxiliary\Build\vcvars64.bat`" -vcvars_ver=14.38 >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { Set-Item "env:$($Matches[1])" $Matches[2] }
}
$env:CUDA_PATH = $cuda
Write-Host "cl: $((Get-Command cl).Source)"

# -- cuDNN headers ---------------------------------------------------------------------------------------------
Step "cuDNN 9.1 headers"
$cudnn = "$WorkDir\deps\cudnn"
if (-not (Test-Path "$cudnn\nvidia\cudnn\include\cudnn.h")) {
    $whl = "$WorkDir\deps\nvidia_cudnn_cu12-9.1.0.70-py3-none-win_amd64.whl"
    if (-not (Test-Path $whl)) { Run curl.exe -L -s -S -o $whl $cudnnWheel }
    if ((Get-FileHash $whl -Algorithm SHA256).Hash -ne $cudnnSha256) { throw "the cuDNN wheel failed its checksum" }
    Remove-Item -Recurse -Force $cudnn -ErrorAction SilentlyContinue  # (an interrupted earlier run)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($whl, $cudnn)
}

# -- oneDNN ------------------------------------------------------------------------------------------------------
Step "oneDNN $onednnVersion"
$onednn = "$WorkDir\deps\onednn"
if (-not (Test-Path "$onednn\include\dnnl.h")) {
    $tar = "$WorkDir\deps\oneDNN-$onednnVersion.tar.gz"
    if (-not (Test-Path $tar)) {
        Run curl.exe -L -s -S -o $tar "https://github.com/oneapi-src/oneDNN/archive/refs/tags/v$onednnVersion.tar.gz"
    }
    Run tar -xf $tar -C "$WorkDir\deps"
    $src = "$WorkDir\deps\oneDNN-$onednnVersion"
    Run cmake -S $src -B "$src\build" -G "Visual Studio 17 2022" -A x64 -T "v143,version=14.38" `
        -DCMAKE_POLICY_VERSION_MINIMUM="3.5" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$onednn" -DONEDNN_LIBRARY_TYPE=STATIC `
        -DONEDNN_BUILD_EXAMPLES=OFF -DONEDNN_BUILD_TESTS=OFF -DONEDNN_ENABLE_WORKLOAD=INFERENCE `
        -DONEDNN_ENABLE_PRIMITIVE="CONVOLUTION;REORDER" -DONEDNN_BUILD_GRAPH=OFF
    Run cmake --build "$src\build" --config Release --target install --parallel $Jobs
}

# -- CTranslate2 -----------------------------------------------------------------------------------------------
Step "CTranslate2 $Version"
$src = "$WorkDir\CTranslate2-$Version"
if (-not (Test-Path "$src\CMakeLists.txt")) {
    Run git clone --depth 1 --branch "v$Version" --recurse-submodules --shallow-submodules `
        https://github.com/OpenNMT/CTranslate2.git $src
}
# Maxwell has no half-precision arithmetic (it needs compute capability 5.3). CTranslate2 already guards its other AWQ
# kernels for older GPUs; this one gets the same guard. (AWQ is for AWQ-quantized language models, never Whisper.)
$awq = "$src\src\ops\awq\dequantize_gpu.cu"
$code = [IO.File]::ReadAllText($awq)
if (-not $code.Contains("__CUDA_ARCH__ < 530")) {
    $nl = if ($code.Contains("`r`n")) { "`r`n" } else { "`n" }
    $open = "int out_c)$nl     {$nl"
    $close = "*(C_ptr2 + i) = B_shared[i];$nl       }$nl     }$nl"
    if (-not ($code.Contains($open) -and $code.Contains($close))) { throw "$awq changed: update the Maxwell guard" }
    $code = $code.Replace($open, "$open#if defined(__CUDA_ARCH__) && __CUDA_ARCH__ < 530$nl       assert(false);$nl#else$nl")
    $code = $code.Replace($close, "*(C_ptr2 + i) = B_shared[i];$nl       }$nl#endif$nl     }$nl")
    [IO.File]::WriteAllText($awq, $code)
}
$build = "$src\build"
$install = "$WorkDir\install-$Version"
# CMake wants forward slashes (FindCUDA reads backslashes as escapes).
function Fwd($path) { $path.Replace("\", "/") }
$prefixes = ("$oneapi\compiler\latest", "$oneapi\compiler\latest\windows\compiler\lib\intel64_win", $onednn | ForEach-Object { Fwd $_ }) -join ";"
Run cmake -S $src -B $build -G "Visual Studio 17 2022" -A x64 -T "v143,version=14.38" `
    -DCMAKE_POLICY_VERSION_MINIMUM="3.5" `
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$(Fwd $install)" `
    -DCMAKE_PREFIX_PATH="$prefixes" -DINTEL_ROOT="$(Fwd (Split-Path $oneapi))" `
    -DBUILD_CLI=OFF -DWITH_DNNL=ON -DWITH_CUDA=ON -DWITH_CUDNN=ON `
    -DCUDA_TOOLKIT_ROOT_DIR="$(Fwd $cuda)" -DCUDA_DYNAMIC_LOADING=ON `
    -DCUDA_NVCC_FLAGS="-Xfatbin=-compress-all" -DCUDA_ARCH_LIST="$Archs" `
    -DCUDNN_INCLUDE_DIR="$(Fwd "$cudnn\nvidia\cudnn\include")" `
    -DCUDNN_LIBRARIES="$(Fwd "$cudnn\nvidia\cudnn\lib\x64\cudnn.lib")"
Run cmake --build $build --config Release --target install --parallel $Jobs

# -- check and package ---------------------------------------------------------------------------------------------
Step "Checking and packaging"
$dll = "$install\bin\ctranslate2.dll"
Run python "$repo\tools\cuda_archs.py" $dll --require "50,52,53,60,61,70,75,80,86"
$name = "ctranslate2-$Version-maxwell-win_amd64"
$stage = "$WorkDir\out\$name"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage | Out-Null
Copy-Item $dll $stage
Copy-Item "$src\LICENSE" $stage
$zip = "$WorkDir\out\$name.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$stage\*" -DestinationPath $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $zip).Length
"$hash  $name.zip" | Set-Content "$zip.sha256" -Encoding ascii
Write-Host "`n$zip"
Write-Host "SHA-256: $hash"
Write-Host "Size:    $size bytes"
Write-Host "Upload it to the release legacy-gpu-ct2-$Version, then pin these in LEGACY_ENGINE (app/asr/cuda_libs.py)."
