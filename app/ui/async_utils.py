"""Run blocking calls off the GUI thread and get the result back as a callback."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], relay: _Relay):
        super().__init__()
        self.fn = fn
        self.relay = relay

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001
            log.exception("background task failed")
            self.relay.failed.emit(str(exc))
            return
        self.relay.done.emit(result)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None], parent: QObject,
              on_error: Callable[[str], None] | None = None) -> None:
    """Call ``fn`` on the thread pool; ``on_done(result)`` runs on the GUI thread.

    The callback is dropped if ``parent`` is destroyed first.
    """
    relay = _Relay(parent)
    relay.done.connect(on_done)
    relay.done.connect(relay.deleteLater)
    if on_error:
        relay.failed.connect(on_error)
    relay.failed.connect(relay.deleteLater)
    QThreadPool.globalInstance().start(_Task(fn, relay))
