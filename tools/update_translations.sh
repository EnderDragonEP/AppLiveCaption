#!/usr/bin/env bash
# Update the translations from WSL with the Windows Python's Qt tools (the README's "Translating the UI"):
#
#     tools/update_translations.sh            # collect new UI text into every app/i18n/*.ts, then compile .qm
#     tools/update_translations.sh --release  # only compile the .ts files to .qm (after translating)
#
# Translate in between with Qt Linguist (pyside6-linguist) or any editor: fill in each <translation>
# and remove its type="unfinished".
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
command -v powershell.exe >/dev/null || { echo "powershell.exe not found: run this in WSL with Windows interop on." >&2; exit 1; }

scripts=$(powershell.exe -NoProfile -Command \
    '(Split-Path (Get-Command python).Source) + "\Scripts"' | tr -d '\r')
win_repo=$(wslpath -w "$repo")
ts_files=(app/i18n/*.ts)
[[ -e ${ts_files[0]} ]] || { echo "No app/i18n/*.ts yet: run pyside6-lupdate with -ts app/i18n/app_<locale>.ts first." >&2; exit 1; }

run() {  # run PowerShell in the repo folder
    powershell.exe -NoProfile -Command "Set-Location '$win_repo'; $1" | tr -d '\r'
}

if [[ ${1-} != --release ]]; then
    sources=$(find app -name '*.py' | sort | tr '/' '\\' | tr '\n' ' ')
    for ts in "${ts_files[@]}"; do
        run "& '$scripts\\pyside6-lupdate.exe' $sources -no-obsolete -ts '${ts//\//\\}'"
    done
fi
for ts in "${ts_files[@]}"; do
    run "& '$scripts\\pyside6-lrelease.exe' '${ts//\//\\}' -qm '${ts%.ts}.qm'"
done
grep -cH 'type="unfinished"' "${ts_files[@]}" | sed 's/:/: unfinished /' || true
