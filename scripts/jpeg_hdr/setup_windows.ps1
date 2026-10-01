$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location -LiteralPath $repoRoot
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Setup step failed: $LASTEXITCODE" } }
if (!$env:IMG2UHDR_JPEG_HOME) { $env:IMG2UHDR_JPEG_HOME = Join-Path $repoRoot '.jpeg-hdr' }
$runtimeDir = [IO.Path]::GetFullPath($env:IMG2UHDR_JPEG_HOME)
$env:IMG2UHDR_JPEG_HOME = $runtimeDir
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
if (!(Test-Path -LiteralPath '.venv-jpeg/Scripts/python.exe')) {
    py -3.12 -m venv .venv-jpeg
    Check-Exit
}
$pythonExe = Join-Path $repoRoot '.venv-jpeg/Scripts/python.exe'
& $pythonExe -m pip install --upgrade pip
Check-Exit
& $pythonExe -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
Check-Exit
& $pythonExe -m pip install -r "$PSScriptRoot/requirements-windows.txt"
Check-Exit
function Get-PinnedSource($name, $url, $revision) {
    $targetDir = Join-Path $runtimeDir $name
    if (!(Test-Path -LiteralPath $targetDir)) {
        git clone $url $targetDir
        Check-Exit
        git -C $targetDir checkout $revision
        Check-Exit
    }
    $actualRevision = git -C $targetDir rev-parse HEAD
    Check-Exit
    if ($actualRevision -ne $revision) { throw "$name is not at the tested revision $revision" }
}
Get-PinnedSource 'IntrinsicHDR' 'https://github.com/compphoto/IntrinsicHDR.git' '8f21f95c4369b8c7c39c6869dd2c484370744d4d'
Get-PinnedSource 'sam2' 'https://github.com/facebookresearch/sam2.git' '2b90b9f5ceec907a1c18123530e92e794ad901a4'
$env:SAM2_BUILD_CUDA = '0'
& $pythonExe -m pip install --no-build-isolation --no-deps -e (Join-Path $runtimeDir 'sam2')
Check-Exit
& $pythonExe -m pip install --no-deps -e $repoRoot
Check-Exit
& $pythonExe "$PSScriptRoot/download_assets.py"
Check-Exit
& $pythonExe -m pip check
Check-Exit
Write-Host 'Ready. Run .venv-jpeg/Scripts/img2uhdr.exe jpeg-hdr input.jpg --output outputs/photo_ultrahdr.jpg'
