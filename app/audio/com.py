"""COM apartment helper for threads that call WASAPI/pycaw."""

from __future__ import annotations

from contextlib import contextmanager

import comtypes


@contextmanager
def com_apartment():
    """Initialise COM (MTA) on this thread for the duration of the block.

    If the thread already has an apartment of another kind (the Qt GUI thread
    is STA), the existing one is used as is.
    """
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        owned = True
    except OSError:
        owned = False
    try:
        yield
    finally:
        if owned:
            comtypes.CoUninitialize()
