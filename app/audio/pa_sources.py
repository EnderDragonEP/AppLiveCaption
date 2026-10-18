"""Microphone and system-audio (WASAPI loopback) sources via PyAudioWPatch."""

from __future__ import annotations

import logging
import time

import numpy as np
import pyaudiowpatch as pyaudio

from .devices import _pa_lock, resolve_device, strip_loopback_suffix
from .source import AudioSource, SourceState

log = logging.getLogger(__name__)


class DeviceNotFound(RuntimeError):
    pass


class PortAudioSource(AudioSource):
    """Captures one WASAPI endpoint in callback mode.

    ``device_name`` is the PortAudio/WASAPI device name; an empty string
    follows the Windows default device. When the device fails the thread ends
    in ERROR state and the capture manager reopens it.
    """

    loopback = False

    def __init__(self, device_name: str, key: str, label: str, on_status=None):
        super().__init__(key=key, label=label, on_status=on_status)
        self.device_name = device_name
        self.opened_name: str | None = None  # the device actually opened
        self.fell_back = False  # a named device was missing and the default was used

    def run(self) -> None:
        pa = None
        stream = None
        try:
            with _pa_lock:
                pa = pyaudio.PyAudio()
                info = resolve_device(pa, self.device_name, self.loopback)
                if info is None and self.device_name:
                    # The chosen device is gone: fall back to the default one.
                    info = resolve_device(pa, "", self.loopback)
                    self.fell_back = info is not None
                if info is None:
                    raise DeviceNotFound("no device")
                channels = max(1, int(info["maxInputChannels"]))
                rate = int(info["defaultSampleRate"])
                self.opened_name = info["name"]

                def callback(in_data, frame_count, time_info, status):
                    if in_data:
                        block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, channels)
                        self._emit(block, rate)
                    return (None, pyaudio.paContinue)

                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=info["index"],
                    frames_per_buffer=max(256, rate // 50),
                    stream_callback=callback,
                )
            log.info("Opened %s '%s' (%d Hz, %d ch)", self.kind, self.opened_name, rate, channels)
            self._set_state(SourceState.RUNNING, "fallback" if self.fell_back else "")
            started = time.monotonic()
            while not self._stop.is_set():
                if self._sleep(0.25):
                    break
                if not stream.is_active():
                    raise RuntimeError("stream stopped")
                # A microphone delivers data continuously; a long gap means
                # the endpoint died without PortAudio noticing. Loopback is
                # silent (no packets) whenever nothing plays, so skip it.
                if not self.loopback and time.monotonic() - started > 3 and self.idle_seconds > 3:
                    raise RuntimeError("no data from device")
        except DeviceNotFound:
            self._set_state(SourceState.ERROR, "not_found")
        except Exception as exc:  # noqa: BLE001
            log.warning("%s '%s' failed: %s", self.kind, self.device_name or "default", exc)
            self._set_state(SourceState.ERROR, "lost")
        finally:
            with _pa_lock:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                if pa is not None:
                    try:
                        pa.terminate()
                    except Exception:  # noqa: BLE001
                        pass

    @property
    def display_device(self) -> str:
        return strip_loopback_suffix(self.opened_name or self.device_name or "")


class MicrophoneSource(PortAudioSource):
    kind = "mic"
    loopback = False

    def __init__(self, device_name: str, label: str = "Microphone", on_status=None):
        super().__init__(device_name, key="mic", label=label, on_status=on_status)


class SystemLoopbackSource(PortAudioSource):
    kind = "system"
    loopback = True

    def __init__(self, device_name: str, label: str = "System audio", on_status=None):
        super().__init__(device_name, key="system", label=label, on_status=on_status)
