#!/usr/bin/env bash
# Build the Windows app from WSL: copy the source to a folder on the Windows drive, run build.ps1 there
# with the Windows Python, and bring the results back into this repo's dist/.
#
#     tools/build_windows.sh [--zip] [build.ps1 options, e.g. -Installer -Clean]
#
#   --zip   also make dist/AppLiveCaption.zip, the portable release (the exe, _internal and the notices)
#
# PyInstaller can't cross-compile, and the app is Windows-only, so the build must run on Windows. It runs
# in %LOCALAPPDATA%\AppLiveCaption-build (or $ALC_WIN_BUILD_DIR, a Windows path), not on the Linux
# filesystem: Windows reaches that only through a slow network path that PyInstaller and Defender dislike.
# That folder's build/ and dist/ are kept between runs, so PyInstaller's cache makes later builds faster.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
make_zip=0
args=()
for arg in "$@"; do
    if [[ $arg == --zip ]]; then make_zip=1; else args+=("$arg"); fi
done

command -v powershell.exe >/dev/null || { echo "powershell.exe not found: run this in WSL with Windows interop on." >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync is missing: sudo apt install rsync" >&2; exit 1; }

win_dir=${ALC_WIN_BUILD_DIR:-$(powershell.exe -NoProfile -Command 'Write-Output "$env:LOCALAPPDATA\AppLiveCaption-build"' | tr -d '\r')}
dir=$(wslpath -u "$win_dir")
mkdir -p "$dir"

echo "Copying the source to $win_dir ..."
# The app's own data (when it runs from source), git and build output stay out. --delete removes source
# files deleted here; the excluded build/ and dist/ on the Windows side are left alone.
rsync -a --delete \
    --exclude .git/ --exclude .venv/ --exclude __pycache__/ --exclude '*.pyc' --exclude .ruff_cache/ \
    --exclude /build/ --exclude /dist/ --exclude /_site/ \
    --exclude /config.json --exclude /models.json --exclude /history/ --exclude /logs/ --exclude /models/ \
    --exclude /cuda/ \
    "$repo/" "$dir/"

echo "Building on Windows ..."
# (Started from the Windows folder, so PowerShell doesn't run with a \\wsl.localhost working directory.)
(cd "$dir" && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$win_dir\\build.ps1" "${args[@]}")

echo "Copying the results to dist/ ..."
mkdir -p "$repo/dist/AppLiveCaption"
# Only the build output: other files in dist/AppLiveCaption (a portable copy's settings, models, ...) stay.
rsync -a --delete "$dir/dist/AppLiveCaption/_internal/" "$repo/dist/AppLiveCaption/_internal/"
cp "$dir/dist/AppLiveCaption/AppLiveCaption.exe" "$repo/dist/AppLiveCaption/"
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$repo/app/__init__.py")
setup="$dir/dist/AppLiveCaption-$version-Setup.exe"
if [[ " ${args[*]-} " == *" -Installer "* && -f $setup ]]; then
    cp "$setup" "$repo/dist/"
fi

if (( make_zip )); then
    echo "Making the portable zip ..."
    python3 - "$repo" <<'EOF'
import sys, zipfile
from pathlib import Path

repo = Path(sys.argv[1])
src = repo / "dist" / "AppLiveCaption"
files = [src / "AppLiveCaption.exe", repo / "THIRD_PARTY_NOTICES.md", *sorted((src / "_internal").rglob("*"))]
with zipfile.ZipFile(repo / "dist" / "AppLiveCaption.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in files:
        name = "AppLiveCaption/" + (f.name if f.parent == repo else f.relative_to(src).as_posix())
        z.write(f, name)
EOF
fi

echo
du -sh "$repo/dist/AppLiveCaption/AppLiveCaption.exe" "$repo/dist/AppLiveCaption/_internal" | sed 's/^/  /'
ls -l --block-size=M "$repo"/dist/*.exe "$repo"/dist/*.zip 2>/dev/null | awk '{print "  " $5 "  " $9}'
