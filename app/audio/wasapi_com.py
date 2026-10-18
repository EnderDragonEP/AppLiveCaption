"""Minimal WASAPI COM declarations used by per-app (process loopback) capture.

Only the pieces needed for ``ActivateAudioInterfaceAsync`` with
``AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`` are declared here, following
Microsoft's ApplicationLoopback sample.
"""

from __future__ import annotations

import ctypes
from ctypes import HRESULT, POINTER, Structure, Union, c_byte, c_int, c_long, c_longlong, c_ubyte, c_uint32, c_uint64, c_ushort, c_void_p
from ctypes import wintypes

from comtypes import COMMETHOD, GUID, IUnknown

REFERENCE_TIME = c_longlong

# --- constants ---------------------------------------------------------------
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000

AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2

AUDCLNT_S_BUFFER_EMPTY = 0x08890001
AUDCLNT_E_DEVICE_INVALIDATED = ctypes.c_long(0x88890004).value

AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT = 0
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3

VT_BLOB = 65

# Windows 10 build 20348 (Server 2022) introduced process loopback; in
# practice that means Windows 11 (22000+) on the desktop.
PROCESS_LOOPBACK_MIN_BUILD = 20348


# --- structures --------------------------------------------------------------
class WAVEFORMATEX(Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]

    @classmethod
    def make(cls, tag: int, channels: int, rate: int, bits: int) -> "WAVEFORMATEX":
        align = channels * bits // 8
        return cls(tag, channels, rate, rate * align, align, bits, 0)


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", c_int)]


class AUDIOCLIENT_ACTIVATION_PARAMS(Structure):
    _fields_ = [
        ("ActivationType", c_int),
        ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS),
    ]


class BLOB(Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", POINTER(c_ubyte))]


class _PropVariantUnion(Union):
    _fields_ = [("blob", BLOB), ("_pad", c_byte * 16)]


class PROPVARIANT(Structure):
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", wintypes.WORD),
        ("wReserved2", wintypes.WORD),
        ("wReserved3", wintypes.WORD),
        ("u", _PropVariantUnion),
    ]


# --- interfaces --------------------------------------------------------------
class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize",
                  (["in"], wintypes.DWORD, "ShareMode"),
                  (["in"], wintypes.DWORD, "StreamFlags"),
                  (["in"], REFERENCE_TIME, "hnsBufferDuration"),
                  (["in"], REFERENCE_TIME, "hnsPeriodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["in"], POINTER(GUID), "AudioSessionGuid")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "pNumBufferFrames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(REFERENCE_TIME), "phnsLatency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "pNumPaddingFrames")),
        COMMETHOD([], HRESULT, "IsFormatSupported",
                  (["in"], wintypes.DWORD, "ShareMode"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["out"], POINTER(c_void_p), "ppClosestMatch")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(c_void_p), "ppDeviceFormat")),
        COMMETHOD([], HRESULT, "GetDevicePeriod",
                  (["out"], POINTER(REFERENCE_TIME), "phnsDefaultDevicePeriod"),
                  (["out"], POINTER(REFERENCE_TIME), "phnsMinimumDevicePeriod")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
        COMMETHOD([], HRESULT, "GetService",
                  (["in"], POINTER(GUID), "riid"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppv")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(c_void_p), "ppData"),
                  (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                  (["out"], POINTER(wintypes.DWORD), "pdwFlags"),
                  (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                  (["out"], POINTER(c_uint64), "pu64QPCPosition")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult",
                  (["out"], POINTER(c_long), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    # The operation is declared as a plain pointer on purpose: the handler
    # only signals an event, and wrapping the borrowed pointer in a comtypes
    # object would Release() it when the wrapper is collected.
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted", (["in"], c_void_p, "activateOperation")),
    ]


class IAgileObject(IUnknown):
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


# --- functions ---------------------------------------------------------------
_mmdevapi = None


def activate_audio_interface_async():
    """Return the ``ActivateAudioInterfaceAsync`` function from Mmdevapi.dll."""
    global _mmdevapi
    if _mmdevapi is None:
        _mmdevapi = ctypes.WinDLL("Mmdevapi.dll")
        fn = _mmdevapi.ActivateAudioInterfaceAsync
        fn.argtypes = [
            wintypes.LPCWSTR,
            POINTER(GUID),
            POINTER(PROPVARIANT),
            POINTER(IActivateAudioInterfaceCompletionHandler),
            POINTER(POINTER(IActivateAudioInterfaceAsyncOperation)),
        ]
        fn.restype = HRESULT
    return _mmdevapi.ActivateAudioInterfaceAsync


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.argtypes = [c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
