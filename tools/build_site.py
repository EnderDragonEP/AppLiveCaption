"""Assemble the project website for GitHub Pages.

    python tools/build_site.py [output folder]     (default: _site)

Copies site/, adds the README's banner and screenshots (.asset) as img/ and
the app icon as favicon.ico, and fills in the app's version from
app/__init__.py. The page itself then asks GitHub for the latest release, so
its download buttons point straight at the zip.

To preview it:  python tools/build_site.py && python -m http.server -d _site
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    shutil.copytree(ROOT / ".asset", out / "img")
    shutil.copy2(ROOT / "app" / "assets" / "app.ico", out / "favicon.ico")
    meta: dict = {}
    exec((ROOT / "app" / "__init__.py").read_text(encoding="utf-8"), meta)
    index = out / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("{{version}}", meta["__version__"]), encoding="utf-8")
    print(f"Website written to {out}")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "_site")
