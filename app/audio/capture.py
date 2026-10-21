"""Capture manager: turns the user's source selection into running sources.

It owns the mixer, watches the sources and recovers from failures:

* a microphone or output device that disappears is reopened (falling back to
  the Windows default device if the chosen one stays missing),
* sources that follow the Windows default device move when the default changes,
* selected apps that close are dropped from the mix and re-attached when they
  start again.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .app_list import find_running
from .com import com_apartment
from .devices import DEFAULT_DEVICE
from .mixer import Mixer, TimedQueue
from .pa_sources import MicrophoneSource, PortAudioSource, SystemLoopbackSource
from .process_loopback import AppLoopbackSource, process_loopback_supported
from .source import AudioSource, SourceState

log = logging.getLogger(__name__)

MODE_MIC = "mic"
MODE_SYSTEM = "system"
MODE_APPS = "apps"


def _printable(name: str) -> str:
    """App names saved by older versions could contain control characters."""
    return "".join(c for c in name.split("\0", 1)[0] if c.isprintable()).strip()


@dataclass
class CaptureSelection:
    mode: str = MODE_SYSTEM
    mic_device: str = DEFAULT_DEVICE
    system_device: str = DEFAULT_DEVICE
    apps: list[str] = field(default_factory=list)  # lower-case exe names
    app_names: dict[str, str] = field(default_factory=dict)  # exe -> friendly name
    include_mic: bool = False  # add one microphone to the app mix

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "mic_device": self.mic_device,
            "system_device": self.system_device,
            "apps": list(self.apps),
            "app_names": dict(self.app_names),
            "include_mic": self.include_mic,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "CaptureSelection":
        d = d or {}
        sel = cls()
        sel.mode = d.get("mode", MODE_SYSTEM) if d.get("mode") in (MODE_MIC, MODE_SYSTEM, MODE_APPS) else MODE_SYSTEM
        sel.mic_device = d.get("mic_device", DEFAULT_DEVICE) or DEFAULT_DEVICE
        sel.system_device = d.get("system_device", DEFAULT_DEVICE) or DEFAULT_DEVICE
        sel.apps = [a.lower() for a in d.get("apps", []) if isinstance(a, str)]
        sel.app_names = {k.lower(): _printable(v) for k, v in (d.get("app_names") or {}).items() if isinstance(v, str)}
        sel.include_mic = bool(d.get("include_mic", False))
        return sel


# Event names sent to ``on_event``.
EV_DEVICE_LOST = "device_lost"
EV_DEVICE_RESTORED = "device_restored"
EV_DEVICE_FALLBACK = "device_fallback"
EV_DEVICE_MISSING = "device_missing"
EV_DEVICE_CHANGED = "device_changed"
EV_APP_CLOSED = "app_closed"
EV_APP_RESUMED = "app_resumed"
EV_APP_ERROR = "app_error"
EV_UNSUPPORTED = "per_app_unsupported"

EventCallback = Callable[[str, dict], None]


def _default_endpoint_names() -> tuple[str | None, str | None]:
    """Friendly names of the default capture and render endpoints (fresh, via MMDevice)."""
    from pycaw.constants import EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities

    names: list[str | None] = []
    enumerator = AudioUtilities.GetDeviceEnumerator()
    for flow in (EDataFlow.eCapture.value, EDataFlow.eRender.value):
        try:
            dev = enumerator.GetDefaultAudioEndpoint(flow, ERole.eConsole.value)
            names.append(AudioUtilities.CreateDevice(dev).FriendlyName)
        except Exception:  # noqa: BLE001 - no device of that kind
            names.append(None)
    return names[0], names[1]


def _active_endpoint_names() -> set[str]:
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities

    out: set[str] = set()
    try:
        for dev in AudioUtilities.GetAllDevices(EDataFlow.eAll.value, DEVICE_STATE.ACTIVE.value):
            if dev and dev.FriendlyName:
                out.add(dev.FriendlyName)
    except Exception:  # noqa: BLE001
        log.debug("endpoint enumeration failed", exc_info=True)
    return out


class CaptureManager:
    def __init__(self, on_event: EventCallback | None = None):
        self.on_event = on_event
        self.selection = CaptureSelection()
        self.mixer: Mixer | None = None
        self._pa_sources: list[PortAudioSource] = []
        self._app_sources: dict[tuple[str, int], AppLoopbackSource] = {}
        self._closed_apps: set[str] = set()
        self._seen_keys: set[str] = set()
        self._app_retry_at: dict[tuple[str, int], float] = {}
        self._pa_restart_at: float | None = None
        self._pa_failures = 0
        self._pa_lost_notified = False
        self._missing_notified = False
        self._fallback_notified: set[str] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False

    # ------------------------------------------------------------------
    @property
    def output(self) -> TimedQueue:
        assert self.mixer is not None
        return self.mixer.output

    def start(self, selection: CaptureSelection) -> None:
        self.stop()
        self.selection = selection
        self.mixer = Mixer()
        self._closed_apps.clear()
        self._seen_keys = set()
        self._app_retry_at.clear()
        self._pa_failures = 0
        self._pa_restart_at = None
        self._pa_lost_notified = False
        self._missing_notified = False
        self._fallback_notified = set()
        self._start_pa_sources()
        if selection.mode == MODE_APPS and not process_loopback_supported():
            self._emit(EV_UNSUPPORTED, {})
        self.mixer.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, name="capture-supervisor", daemon=True)
        self._thread.start()
        self.running = True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        self._thread = None
        with self._lock:
            for src in self._pa_sources:
                src.stop()
            for src in self._app_sources.values():
                src.stop()
            self._pa_sources = []
            self._app_sources = {}
        if self.mixer:
            self.mixer.stop()
        self.running = False

    def sources(self) -> list[AudioSource]:
        with self._lock:
            return list(self._pa_sources) + list(self._app_sources.values())

    def levels(self) -> list[tuple[AudioSource, float]]:
        """Each source with its current level in dBFS (silent sources decay to the floor)."""
        out = []
        for src in self.sources():
            db = src.level_db if src.idle_seconds < 0.3 else -120.0
            out.append((src, db))
        return out

    # ------------------------------------------------------------------
    def _emit(self, event: str, payload: dict) -> None:
        log.info("capture event %s %s", event, payload)
        if self.on_event:
            try:
                self.on_event(event, payload)
            except Exception:  # noqa: BLE001
                log.exception("capture event handler failed")

    def _wanted_pa_sources(self) -> list[PortAudioSource]:
        sel = self.selection
        out: list[PortAudioSource] = []
        if sel.mode == MODE_MIC:
            out.append(MicrophoneSource(sel.mic_device))
        elif sel.mode == MODE_SYSTEM:
            out.append(SystemLoopbackSource(sel.system_device))
            if sel.include_mic:
                out.append(MicrophoneSource(sel.mic_device))
        elif sel.mode == MODE_APPS and sel.include_mic:
            out.append(MicrophoneSource(sel.mic_device))
        return out

    def _start_pa_sources(self) -> None:
        assert self.mixer is not None
        with self._lock:
            for src in self._pa_sources:
                src.stop()
                self.mixer.remove(src.key)
            # PortAudio only re-enumerates devices once every instance is
            # terminated, so all PortAudio sources are restarted together.
            self._pa_sources = self._wanted_pa_sources()
            for src in self._pa_sources:
                self.mixer.add(src)
                src.start()

    # ------------------------------------------------------------------
    def _supervise(self) -> None:
        with com_apartment():
            next_default_check = 0.0
            next_app_scan = 0.0
            while not self._stop.wait(0.5):
                now = time.monotonic()
                try:
                    self._check_pa_sources(now)
                    if now >= next_default_check:
                        next_default_check = now + 2.0
                        self._check_default_devices()
                    if self.selection.mode == MODE_APPS and process_loopback_supported() and now >= next_app_scan:
                        next_app_scan = now + 1.5
                        self._scan_apps(now)
                except Exception:  # noqa: BLE001 - keep supervising no matter what
                    log.exception("capture supervisor iteration failed")

    def _check_pa_sources(self, now: float) -> None:
        with self._lock:
            sources = list(self._pa_sources)
        if not sources:
            return
        failed = [s for s in sources if s.state == SourceState.ERROR]
        if failed:
            if self._pa_restart_at is None:
                self._pa_failures += 1
                delay = min(5.0, 0.5 * 2 ** (self._pa_failures - 1))
                self._pa_restart_at = now + delay
                missing = any(s.message == "not_found" for s in failed)
                if missing and not self._missing_notified:
                    self._missing_notified = True
                    self._emit(EV_DEVICE_MISSING, {"kind": failed[0].kind})
                elif not missing and not self._pa_lost_notified:
                    self._pa_lost_notified = True
                    self._emit(EV_DEVICE_LOST, {"kind": failed[0].kind, "device": failed[0].display_device})
            elif now >= self._pa_restart_at:
                self._pa_restart_at = None
                self._start_pa_sources()
            return
        if all(s.state == SourceState.RUNNING for s in sources):
            told_fallback = False
            for s in sources:
                if s.fell_back and s.key not in self._fallback_notified:
                    # The chosen device is missing and the Windows default is used instead.
                    self._fallback_notified.add(s.key)
                    told_fallback = True
                    self._emit(EV_DEVICE_FALLBACK, {"kind": s.kind, "device": s.display_device})
                elif not s.fell_back:
                    self._fallback_notified.discard(s.key)
            if (self._pa_lost_notified or self._missing_notified) and not told_fallback:
                self._emit(EV_DEVICE_RESTORED, {"kind": sources[0].kind, "device": sources[0].display_device})
            self._pa_failures = 0
            self._pa_lost_notified = False
            self._missing_notified = False

    def _check_default_devices(self) -> None:
        with self._lock:
            sources = [s for s in self._pa_sources if s.state == SourceState.RUNNING]
        if not sources:
            return
        mic_default, out_default = _default_endpoint_names()
        restart = False
        active = None
        for s in sources:
            if s.device_name == DEFAULT_DEVICE:
                want = mic_default if s.kind == "mic" else (f"{out_default} [Loopback]" if out_default else None)
                if want and s.opened_name and want != s.opened_name:
                    restart = True
            elif s.fell_back:
                # The chosen device was missing; switch back once it returns.
                if active is None:
                    active = _active_endpoint_names()
                if s.device_name.replace(" [Loopback]", "") in active:
                    restart = True
        if restart:
            self._emit(EV_DEVICE_CHANGED, {})
            self._start_pa_sources()

    def _scan_apps(self, now: float) -> None:
        assert self.mixer is not None
        keys = set(self.selection.apps)
        running = find_running(keys) if keys else {}
        with self._lock:
            # Drop sources whose process exited or that the user deselected.
            for ident, src in list(self._app_sources.items()):
                key, pid = ident
                if src.state == SourceState.WAITING or key not in keys or not src.alive:
                    src.stop()
                    self.mixer.remove(src.key)
                    del self._app_sources[ident]
                    if src.state == SourceState.ERROR and key in keys:
                        self._app_retry_at[ident] = now + 5.0
                        self._emit(EV_APP_ERROR, {"app": key, "name": self._name(key), "error": src.message})
            for key in keys:
                pids = running.get(key, set())
                for pid in pids:
                    ident = (key, pid)
                    if ident in self._app_sources or self._app_retry_at.get(ident, 0) > now:
                        continue
                    src = AppLoopbackSource(pid, key, self._name(key))
                    self._app_sources[ident] = src
                    self.mixer.add(src)
                    src.start()
                if pids:
                    if key in self._closed_apps:
                        self._closed_apps.discard(key)
                        self._emit(EV_APP_RESUMED, {"app": key, "name": self._name(key)})
                    self._seen_keys.add(key)
                elif key in self._seen_keys and key not in self._closed_apps:
                    # It was captured earlier in this session and has now closed.
                    self._closed_apps.add(key)
                    self._emit(EV_APP_CLOSED, {"app": key, "name": self._name(key)})

    def _name(self, key: str) -> str:
        return self.selection.app_names.get(key) or key

    def waiting_apps(self) -> list[str]:
        """Selected apps that are not running right now."""
        with self._lock:
            active = {k for k, _ in self._app_sources}
        return [k for k in self.selection.apps if k not in active]
