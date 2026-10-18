"""Find installed packages the app doesn't need, so the PyInstaller build can exclude them.

The app is built with whatever Python it runs on, often one shared with other
projects. PyInstaller follows optional imports too (transformers alone refers
to datasets, accelerate, torchvision, ...), so without excludes it bundles
unrelated packages, or crashes while analysing one of them.

The packages the app needs are everything requirements.txt asks for plus their
dependencies, read from the installed packages' metadata. Every other installed
package's top-level modules are returned.

    python tools/build_excludes.py      # prints what would be excluded
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(path: Path) -> list[Requirement]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            out.append(Requirement(line))
        except InvalidRequirement:
            print(f"build_excludes: ignoring unparsable requirement {line!r}", file=sys.stderr)
    return out


def _installed() -> dict[str, md.Distribution]:
    # The first distribution on sys.path wins, the same one "import" would load.
    dists: dict[str, md.Distribution] = {}
    for d in md.distributions():
        name = d.metadata["Name"]
        if name:
            dists.setdefault(_norm(name), d)
    return dists


def needed_distributions(roots: list[Requirement], dists: dict[str, md.Distribution]) -> set[str]:
    """Names of the installed distributions in the dependency closure of ``roots``."""
    needed: set[str] = set()
    seen: set[tuple[str, frozenset[str]]] = set()
    stack = [(_norm(r.name), frozenset(r.extras)) for r in roots]
    while stack:
        name, extras = stack.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        dist = dists.get(name)
        if dist is None:
            continue  # not installed (e.g. optional PyTorch)
        needed.add(name)
        for spec in dist.requires or []:
            try:
                req = Requirement(spec)
            except InvalidRequirement:
                continue
            if req.marker is not None and not any(req.marker.evaluate({"extra": e}) for e in (extras or {""})):
                continue
            stack.append((_norm(req.name), frozenset(req.extras)))
    return needed


def top_level_modules(dist: md.Distribution) -> set[str]:
    text = dist.read_text("top_level.txt")
    if text:
        return {line.strip() for line in text.splitlines() if line.strip()}
    mods: set[str] = set()
    for f in dist.files or []:
        parts = Path(str(f)).parts
        if not parts or parts[0] == ".." or parts[0].endswith((".dist-info", ".egg-info", ".data")):
            continue
        first = parts[0]
        if len(parts) > 1:
            if first != "__pycache__":
                mods.add(first)
        elif first.endswith((".py", ".pyd", ".so")):
            mods.add(first.split(".", 1)[0])
    return mods


def vendored_modules(dist: md.Distribution) -> set[str]:
    """Modules a package ships its own copy of (e.g. setuptools/_vendor/more_itertools).

    PyInstaller's hooks map the standalone names onto those copies, so excluding
    the standalone name makes the analysis fail.
    """
    mods: set[str] = set()
    for f in dist.files or []:
        parts = Path(str(f)).parts
        for i, part in enumerate(parts[:-1]):
            if part == "_vendor":
                name = parts[i + 1]
                mods.add(name[:-3] if name.endswith(".py") else name)
                break
    return mods


def unused_modules(requirements: Path, drop: tuple[str, ...] = (), extra_roots: tuple[str, ...] = ()) -> list[str]:
    """Top-level modules of installed packages outside the app's dependency closure.

    ``drop`` removes requirements by name (e.g. torch for a build without
    PyTorch); ``extra_roots`` adds packages that are needed but not listed.
    """
    dists = _installed()
    dropped = {_norm(n) for n in drop}
    roots = [r for r in read_requirements(requirements) if _norm(r.name) not in dropped]
    roots += [Requirement(n) for n in extra_roots]
    needed = needed_distributions(roots, dists)
    keep: set[str] = set(sys.stdlib_module_names)
    for name in needed:
        keep |= top_level_modules(dists[name]) | vendored_modules(dists[name])
    unused: set[str] = set()
    for name, dist in dists.items():
        if name not in needed:
            unused |= top_level_modules(dist)
    return sorted(m for m in unused - keep if m.isidentifier())


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    mods = unused_modules(root / "requirements.txt")
    print(f"{len(mods)} modules would be excluded:")
    print(" ".join(mods))
