"""Optional conversion of Chinese captions to Traditional or Simplified Chinese with OpenCC.

Works with either the ``opencc-python-reimplemented`` package (what
requirements.txt installs) or the ``OpenCC`` package; both provide the
``opencc`` module with the same ``OpenCC(profile).convert(text)`` API.
"""

from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

_HAN = re.compile(r"[㐀-鿿]")

# Settings → Chinese output.
CHINESE_OFF, CHINESE_TRADITIONAL, CHINESE_SIMPLIFIED = "off", "traditional", "simplified"
CHINESE_OUTPUTS = (CHINESE_OFF, CHINESE_TRADITIONAL, CHINESE_SIMPLIFIED)
# OpenCC profiles: Taiwan standard with Taiwan phrases (軟體, 資料夾), and Simplified with Mainland
# phrases (软件, 文件夹), so the Taiwan models' Traditional output reads naturally either way.
_PROFILES = {CHINESE_TRADITIONAL: "s2twp", CHINESE_SIMPLIFIED: "tw2sp"}


class ChineseConverter:
    """Converts Chinese text to ``output`` (CHINESE_TRADITIONAL or CHINESE_SIMPLIFIED; CHINESE_OFF leaves it)."""

    def __init__(self, output: str = CHINESE_TRADITIONAL):
        self.output = output
        self._cc: dict[str, object] = {}
        self._failed = False
        self._lock = threading.Lock()

    def _get(self, profile: str):
        if profile not in self._cc and not self._failed:
            with self._lock:
                if profile not in self._cc and not self._failed:
                    try:
                        import opencc

                        self._cc[profile] = opencc.OpenCC(profile)
                    except Exception:  # noqa: BLE001
                        log.exception("OpenCC unavailable; Chinese text will not be converted")
                        self._failed = True
        return self._cc.get(profile)

    def convert(self, text: str) -> str:
        profile = _PROFILES.get(self.output)
        if not profile or not text or not _HAN.search(text):
            return text
        cc = self._get(profile)
        return cc.convert(text) if cc else text
