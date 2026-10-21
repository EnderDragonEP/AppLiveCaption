"""PyInstaller runtime hook: stand in for PyAV, which the build leaves out.

faster-whisper imports PyAV (``av``) at start-up, but only uses it in
``decode_audio()`` to read audio files. The app never calls that: it passes
NumPy arrays. Leaving out PyAV and its FFmpeg libraries saves 84 MB.
"""

import sys
import types


def _missing(name):
    # AttributeError, not ImportError: hasattr() probes (e.g. for __wrapped__) must just return False.
    raise AttributeError(f"PyAV (av.{name}) is not included in the built app")


_av = types.ModuleType("av")
_av.__getattr__ = _missing  # any real use fails with a clear message
sys.modules.setdefault("av", _av)
