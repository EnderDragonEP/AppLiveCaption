"""Optional Simplified → Traditional Chinese conversion (OpenCC s2twp).

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


class ChineseConverter:
    """Converts with OpenCC's ``s2twp`` profile (Taiwan standard + phrases)."""

    def __init__(self):
        self._cc = None
        self._failed = False
        self._lock = threading.Lock()

    def _get(self):
        if self._cc is None and not self._failed:
            with self._lock:
                if self._cc is None and not self._failed:
                    try:
                        import opencc

                        self._cc = opencc.OpenCC("s2twp")
                    except Exception:  # noqa: BLE001
                        log.exception("OpenCC unavailable; Chinese text will not be converted")
                        self._failed = True
        return self._cc

    def convert(self, text: str) -> str:
        if not text or not _HAN.search(text):
            return text
        cc = self._get()
        return cc.convert(text) if cc else text
