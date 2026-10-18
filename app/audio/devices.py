"""Audio device enumeration (WASAPI endpoints through PyAudioWPatch)."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass

import pyaudiowpatch as pyaudio

log = logging.getLogger(__name__)

DEFAULT_DEVICE = ""  # an empty device name means "follow the Windows default"

# PortAudio only enumerates devices when it is initialised, so a fresh
# PyAudio instance is created whenever an up-to-date list is needed. This lock
# serialises those calls; PortAudio's init/terminate are not thread-safe.
_pa_lock = threading.RLock()


@contextmanager
def portaudio():
    with _pa_lock:
        pa = pyaudio.PyAudio()
        try:
            yield pa
        finally:
            pa.terminate()


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    channels: int
    rate: int
    is_loopback: bool
    is_default: bool


def _wasapi_index(pa) -> int:
    return pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]


def list_input_devices() -> list[DeviceInfo]:
    """Microphones and other capture endpoints (excluding loopback devices)."""
    out: list[DeviceInfo] = []
    try:
        with portaudio() as pa:
            api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default = api.get("defaultInputDevice", -1)
            for d in pa.get_device_info_generator_by_host_api(host_api_index=api["index"]):
                if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                    out.append(DeviceInfo(d["index"], d["name"], d["maxInputChannels"],
                                          int(d["defaultSampleRate"]), False, d["index"] == default))
    except Exception:  # noqa: BLE001
        log.exception("Could not list input devices")
    return out


def list_loopback_devices() -> list[DeviceInfo]:
    """WASAPI loopback endpoints, one per output device."""
    out: list[DeviceInfo] = []
    try:
        with portaudio() as pa:
            try:
                default_name = pa.get_default_wasapi_loopback()["name"]
            except Exception:  # noqa: BLE001
                default_name = None
            for d in pa.get_loopback_device_info_generator():
                out.append(DeviceInfo(d["index"], d["name"], d["maxInputChannels"],
                                      int(d["defaultSampleRate"]), True, d["name"] == default_name))
    except Exception:  # noqa: BLE001
        log.exception("Could not list loopback devices")
    return out


def strip_loopback_suffix(name: str) -> str:
    return name.replace(" [Loopback]", "")


def resolve_device(pa, name: str, loopback: bool) -> dict | None:
    """Find a device by name in an open PyAudio instance.

    An empty name resolves to the current Windows default device.
    """
    if loopback:
        if not name:
            try:
                return pa.get_default_wasapi_loopback()
            except Exception:  # noqa: BLE001
                return None
        for d in pa.get_loopback_device_info_generator():
            if d["name"] == name:
                return d
        return None
    api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    if not name:
        idx = api.get("defaultInputDevice", -1)
        return pa.get_device_info_by_index(idx) if idx is not None and idx >= 0 else None
    for d in pa.get_device_info_generator_by_host_api(host_api_index=api["index"]):
        if d["name"] == name and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
            return d
    return None


@dataclass
class Endpoints:
    inputs: list[str]
    outputs: list[str]
    default_input: str | None
    default_output: str | None


def list_endpoints() -> Endpoints:
    """Active capture/render endpoints from MMDevice (always current, even while
    PortAudio streams are open). Names match PortAudio's WASAPI device names;
    a loopback device is the output name plus " [Loopback]"."""
    from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities

    from .com import com_apartment

    inputs: list[str] = []
    outputs: list[str] = []
    default_in = default_out = None
    with com_apartment():
        try:
            enumerator = AudioUtilities.GetDeviceEnumerator()
            for flow, bucket in ((EDataFlow.eCapture.value, inputs), (EDataFlow.eRender.value, outputs)):
                coll = enumerator.EnumAudioEndpoints(flow, DEVICE_STATE.ACTIVE.value)
                for i in range(coll.GetCount()):
                    dev = AudioUtilities.CreateDevice(coll.Item(i))
                    if dev and dev.FriendlyName:
                        bucket.append(dev.FriendlyName)
                try:
                    d = AudioUtilities.CreateDevice(enumerator.GetDefaultAudioEndpoint(flow, ERole.eConsole.value))
                    if flow == EDataFlow.eCapture.value:
                        default_in = d.FriendlyName
                    else:
                        default_out = d.FriendlyName
                except Exception:  # noqa: BLE001 - no default device of this kind
                    pass
        except Exception:  # noqa: BLE001
            log.exception("Could not list audio endpoints")
    return Endpoints(inputs, outputs, default_in, default_out)


def current_default_names() -> tuple[str | None, str | None]:
    """(default input name, default loopback name) as Windows reports them now."""
    try:
        with portaudio() as pa:
            mic = resolve_device(pa, DEFAULT_DEVICE, loopback=False)
            lb = resolve_device(pa, DEFAULT_DEVICE, loopback=True)
            return (mic["name"] if mic else None, lb["name"] if lb else None)
    except Exception:  # noqa: BLE001
        return (None, None)
