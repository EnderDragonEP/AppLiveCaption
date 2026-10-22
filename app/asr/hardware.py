"""GPU detection and real-time speed estimates."""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from functools import lru_cache

from .cuda_setup import cuda_libraries_found, setup_cuda_dlls

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: int
    free_mb: int


def _nvml_gpu() -> GpuInfo | None:
    """Query the first NVIDIA GPU through NVML (ships with the driver)."""
    try:
        nvml = ctypes.WinDLL("nvml.dll")
    except OSError:
        return None
    try:
        if nvml.nvmlInit_v2() != 0:
            return None
        handle = ctypes.c_void_p()
        if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            return None
        name = ctypes.create_string_buffer(96)
        nvml.nvmlDeviceGetName(handle, name, 96)

        class Memory(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

        mem = Memory()
        nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
        return GpuInfo(name.value.decode(errors="replace"), int(mem.total // 2**20), int(mem.free // 2**20))
    except Exception:  # noqa: BLE001
        log.debug("NVML query failed", exc_info=True)
        return None
    finally:
        try:
            nvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass


@lru_cache(maxsize=1)
def nvidia_gpu() -> GpuInfo | None:
    """The NVIDIA GPU, found through the driver alone (no CUDA libraries needed)."""
    return _nvml_gpu()


@lru_cache(maxsize=1)
def cuda_available() -> bool:
    """True when CTranslate2 can use an NVIDIA GPU: a GPU, its driver and the CUDA 12 libraries.

    The GPU is never touched without the libraries: cuDNN would abort the process.
    """
    if not cuda_libraries_found():
        return False
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0 and "float16" in ctranslate2.get_supported_compute_types("cuda")
    except Exception:  # noqa: BLE001
        log.info("CUDA not available", exc_info=True)
        return False


def gpu_info() -> GpuInfo | None:
    return _nvml_gpu() if cuda_available() else None


def refresh_cuda() -> bool:
    """Look for the CUDA libraries again (after they were downloaded)."""
    setup_cuda_dlls(refresh=True)
    cuda_available.cache_clear()
    return cuda_available()


def resolve_device(preference: str) -> str:
    """Map the "auto" / "cuda" / "cpu" setting to the device to use."""
    if preference == "cpu":
        return "cpu"
    if cuda_available():
        return "cuda"
    return "cpu"


# Approximate VRAM needed in float16, in MB.
_VRAM_NEED = {"tiny": 400, "base": 500, "small": 1000, "medium": 2300, "turbo": 2200, "large": 4000}


def realtime_risk(tier: str, device: str, gpu: GpuInfo | None) -> str | None:
    """Return "cpu_slow" or "vram_low" when the model will likely lag behind real time."""
    if device == "cpu":
        return "cpu_slow" if tier in ("medium", "turbo", "large") else None
    if gpu is not None and gpu.total_mb and _VRAM_NEED.get(tier, 4000) > gpu.total_mb * 0.9:
        return "vram_low"
    return None


# The models first-time setup suggests, best first (all languages, fast enough for live captions).
SUGGESTED_MODELS = ("whisper-large-v3-turbo", "whisper-small", "whisper-base", "whisper-tiny")


def suggest_model(models: list[tuple[str, str]], device: str, gpu: GpuInfo | None) -> str | None:
    """The model to suggest from ``models`` ((id, tier) pairs): the best of SUGGESTED_MODELS that keeps up in
    real time on ``device``. Falls back to the first suggested (or listed) model when none would."""
    tiers = dict(models)
    listed = [m for m in SUGGESTED_MODELS if m in tiers]
    fast = [m for m in listed if realtime_risk(tiers[m], device, gpu) is None]
    return (fast or listed or [m for m, _ in models] or [None])[0]
