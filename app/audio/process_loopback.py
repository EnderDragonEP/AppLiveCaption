"""Per-app capture with the Windows process loopback API.

Implements the flow from Microsoft's *ApplicationLoopback* sample with
comtypes: ``ActivateAudioInterfaceAsync`` on the virtual
``VAD\\Process_Loopback`` device with ``PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE``
so audio played by child processes (browser and Electron renderers) is
included.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import POINTER, byref, cast, c_ubyte, sizeof

import comtypes
import numpy as np
import psutil
from comtypes import COMObject

from . import wasapi_com as w
from .com import com_apartment
from .source import AudioSource, SourceState

log = logging.getLogger(__name__)


def process_loopback_supported() -> bool:
    if sys.platform != "win32":
        return False
    return sys.getwindowsversion().build >= w.PROCESS_LOOPBACK_MIN_BUILD


class _CompletionHandler(COMObject):
    """Signals an event when activation finishes.

    It implements IAgileObject so the audio stack may call it from any thread.
    """

    _com_interfaces_ = [w.IActivateAudioInterfaceCompletionHandler, w.IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()

    def ActivateCompleted(self, this, activateOperation):  # noqa: N802 - COM name
        self.done.set()
        return 0


class ProcessLoopbackError(RuntimeError):
    pass


def activate_process_loopback(pid: int, include_tree: bool = True, timeout: float = 5.0):
    """Activate an IAudioClient that captures the audio of ``pid`` (and its children)."""
    params = w.AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = w.AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.ProcessLoopbackParams.TargetProcessId = pid
    params.ProcessLoopbackParams.ProcessLoopbackMode = (
        w.PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
        if include_tree
        else w.PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE
    )
    pv = w.PROPVARIANT()
    pv.vt = w.VT_BLOB
    pv.u.blob.cbSize = sizeof(params)
    pv.u.blob.pBlobData = cast(byref(params), POINTER(c_ubyte))

    handler = _CompletionHandler()
    handler_itf = handler.QueryInterface(w.IActivateAudioInterfaceCompletionHandler)
    op = POINTER(w.IActivateAudioInterfaceAsyncOperation)()
    fn = w.activate_audio_interface_async()
    fn(w.VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(w.IAudioClient._iid_), byref(pv), handler_itf, byref(op))
    if not handler.done.wait(timeout):
        raise ProcessLoopbackError("Timed out activating process loopback")
    hr, unk = op.GetActivateResult()
    if hr < 0:
        raise ProcessLoopbackError(f"Process loopback activation failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")
    return unk.QueryInterface(w.IAudioClient)


class AppLoopbackSource(AudioSource):
    """Captures one app (a process tree) through process loopback."""

    kind = "app"
    RATE = 48000
    CHANNELS = 2

    def __init__(self, pid: int, exe: str, label: str, on_status=None):
        super().__init__(key=f"app:{exe.lower()}:{pid}", label=label, on_status=on_status)
        self.pid = pid
        self.exe = exe
        self.exited = False

    def run(self) -> None:
        with com_apartment():
            self._run_com()

    def _run_com(self) -> None:
        event = None
        client = None
        try:
            client = activate_process_loopback(self.pid)
            fmt, flags = self._initialize(client)
            event = w.kernel32.CreateEventW(None, False, False, None)
            client.SetEventHandle(event)
            capture = client.GetService(byref(w.IAudioCaptureClient._iid_)).QueryInterface(w.IAudioCaptureClient)
            client.Start()
            self._set_state(SourceState.RUNNING)
            log.info("Process loopback started for %s (pid %d), %d Hz %d ch %s",
                     self.exe, self.pid, fmt.nSamplesPerSec, fmt.nChannels,
                     "float" if fmt.wFormatTag == w.WAVE_FORMAT_IEEE_FLOAT else f"pcm{fmt.wBitsPerSample}")
            self._loop(client, capture, event, fmt)
        finally:
            if client is not None:
                try:
                    client.Stop()
                except Exception:  # noqa: BLE001
                    pass
            if event:
                w.kernel32.CloseHandle(event)
            client = capture = None

    def _initialize(self, client):
        stream_flags = (
            w.AUDCLNT_STREAMFLAGS_LOOPBACK
            | w.AUDCLNT_STREAMFLAGS_EVENTCALLBACK
            | w.AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
            | w.AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
        )
        # Process loopback clients don't report a mix format (GetMixFormat
        # returns E_NOTIMPL), so request one; AUTOCONVERTPCM makes the engine
        # convert. Fall back to the 16-bit format the official sample uses.
        candidates = [
            w.WAVEFORMATEX.make(w.WAVE_FORMAT_IEEE_FLOAT, self.CHANNELS, self.RATE, 32),
            w.WAVEFORMATEX.make(w.WAVE_FORMAT_PCM, self.CHANNELS, 44100, 16),
        ]
        last_exc: Exception | None = None
        for fmt in candidates:
            try:
                # 500 ms buffer: generous so a busy Python thread never overflows it.
                client.Initialize(w.AUDCLNT_SHAREMODE_SHARED, stream_flags, 5_000_000, 0, byref(fmt), None)
                return fmt, stream_flags
            except comtypes.COMError as exc:
                last_exc = exc
                log.debug("Initialize with format tag %d failed: %s", fmt.wFormatTag, exc)
        raise ProcessLoopbackError(f"Could not initialize process loopback: {last_exc}")

    def _loop(self, client, capture, event, fmt) -> None:
        rate = fmt.nSamplesPerSec
        channels = fmt.nChannels
        align = fmt.nBlockAlign
        is_float = fmt.wFormatTag == w.WAVE_FORMAT_IEEE_FLOAT
        next_alive_check = time.monotonic() + 1.0
        while not self._stop.is_set():
            w.kernel32.WaitForSingleObject(event, 100)
            now = time.monotonic()
            if now >= next_alive_check:
                next_alive_check = now + 1.0
                if not psutil.pid_exists(self.pid):
                    self.exited = True
                    self._set_state(SourceState.WAITING, "closed")
                    return
            while True:
                packet = capture.GetNextPacketSize()
                if not packet:
                    break
                data, frames, flags, _pos, _qpc = capture.GetBuffer()
                if frames:
                    if flags & w.AUDCLNT_BUFFERFLAGS_SILENT or not data:
                        block = np.zeros((frames, channels), np.float32)
                    else:
                        raw = ctypes.string_at(data, frames * align)
                        if is_float:
                            block = np.frombuffer(raw, np.float32).reshape(-1, channels)
                        else:
                            block = (np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0).reshape(-1, channels)
                    capture.ReleaseBuffer(frames)
                    self._emit(block, rate)
                else:
                    capture.ReleaseBuffer(0)
