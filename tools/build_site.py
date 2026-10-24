"""Assemble the project website for GitHub Pages.

    python tools/build_site.py [output folder]     (default: _site)

Copies site/, adds the README's banner and screenshots (.asset) as img/ and
the app icon as favicon.ico, and fills in the app's version from
app/__init__.py. The page itself then asks GitHub for the latest release, so
its download buttons point straight at the installer and the zip.

site/index.html is the English page. For each site/i18n/<lang>.json it also
writes <lang>/index.html, with the page's text, image descriptions and labels
translated. The JSON maps English text to its translation: "text" for the text
between tags and for alt/title/aria-label/content attributes (whitespace runs
count as one space), "html" for whole snippets with markup inside, where the
words around the markup need a different order. English text with no
translation stays English and is listed when the site is built.

To preview it:  python tools/build_site.py && python -m http.server -d _site
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://enderdragonep.github.io/AppLiveCaption/"

# Kept as they are: scripts, styles and icons have no text to translate.
_PROTECTED = re.compile(r"(<script\b.*?</script>|<style\b.*?</style>|<svg\b.*?</svg>)", re.S)
_TEXT = re.compile(r"(^|>)([^<>]+)(?=<|$)")
_ATTR = re.compile(r'\b(alt|title|aria-label|content)="([^"]*)"')
# Relative links (img/..., styles.css, zh-TW/): one folder deeper on a translated page.
_RELATIVE = re.compile(r'(\s(?:src|href)=")(?![a-z][a-z0-9+.-]*:|#|/)')


def _norm(text: str) -> str:
    return " ".join(text.split())


class _Translator:
    def __init__(self, table: dict):
        self.text = {_norm(k): v for k, v in table.get("text", {}).items()}
        self.html = table.get("html", {})
        self.missing: list[str] = []

    def _lookup(self, english: str, report: bool) -> str | None:
        key = _norm(english)
        if not key:
            return None
        if key in self.text:
            return self.text[key]
        if report and key not in self.missing:
            self.missing.append(key)
        return None

    def _segment(self, m: re.Match) -> str:
        before, text = m.group(1), m.group(2)
        # Text with no letters (numbers, ·, a version) and translations already in place stay as they are.
        new = self._lookup(text, report=bool(re.search(r"[A-Za-z]", text)))
        if new is None:
            return m.group(0)
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        return before + lead + new + trail

    def _attr(self, m: re.Match) -> str:
        name, value = m.group(1), m.group(2)
        # (content="" is also the viewport, theme colors, ...: only sentences are reported)
        new = self._lookup(value, report=name != "content" or len(value.split()) >= 3)
        return m.group(0) if new is None else f'{name}="{new}"'

    def page(self, html: str) -> str:
        # Whole snippets first. They're set aside (as \0n\0) until the rest is done, so their translated
        # text isn't looked up again as if it were English.
        done: list[str] = []
        for english, translated in self.html.items():
            pattern = r"\s+".join(re.escape(word) for word in english.split())
            html, count = re.subn(pattern, lambda _m, t=translated: (done.append(t), f"\0{len(done) - 1}\0")[1], html)
            if not count:
                self.missing.append(f"(html) {_norm(english)}")
        parts = _PROTECTED.split(html)
        for i in range(0, len(parts), 2):  # (odd indexes are the protected parts)
            parts[i] = _ATTR.sub(self._attr, _TEXT.sub(self._segment, parts[i]))
        return re.sub(r"\0(\d+)\0", lambda m: done[int(m.group(1))], "".join(parts))


def _translate(template: str, code: str, table: dict) -> tuple[str, list[str]]:
    translator = _Translator(table)
    html = translator.page(template)
    html = html.replace('<html lang="en">', f'<html lang="{table["lang"]}">', 1)
    html = html.replace(f'<meta property="og:url" content="{SITE_URL}">',
                        f'<meta property="og:url" content="{SITE_URL}{code}/">', 1)
    html = html.replace('href="./" aria-current="page"', 'href="./"', 1)
    html = html.replace(f'href="{code}/"', f'href="{code}/" aria-current="page"', 1)
    return _RELATIVE.sub(r"\1../", html), translator.missing


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out, ignore=shutil.ignore_patterns("i18n"))
    shutil.copytree(ROOT / ".asset", out / "img")
    shutil.copy2(ROOT / "app" / "assets" / "app.ico", out / "favicon.ico")
    meta: dict = {}
    exec((ROOT / "app" / "__init__.py").read_text(encoding="utf-8"), meta)
    index = out / "index.html"
    template = index.read_text(encoding="utf-8").replace("{{version}}", meta["__version__"])
    index.write_text(template, encoding="utf-8")
    for path in sorted((ROOT / "site" / "i18n").glob("*.json")):
        code = path.stem
        html, missing = _translate(template, code, json.loads(path.read_text(encoding="utf-8")))
        (out / code).mkdir()
        (out / code / "index.html").write_text(html, encoding="utf-8")
        for text in missing:
            print(f"  {code}: not translated: {text}")
    print(f"Website written to {out}")


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "_site")
