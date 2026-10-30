"""GPU detection and real-time speed estimates."""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass, replace
from functools import lru_cache

from .cuda_setup import cuda_libraries_found, setup_cuda_dlls

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: int
    free_mb: int
    compute_capability: tuple[int, int] = (0, 0)  # (0, 0): unknown
    cuda_index: int = 0  # the GPU's number for CUDA (and CTranslate2's device_index)


# CTranslate2's official build has GPU code for compute capability 5.3 and newer. Maxwell GPUs (5.0 and 5.2: the
# GTX 900 series and older GTX 700/800 models) need the "legacy engine", a build of the same CTranslate2 version
# with their code added (see cuda_setup and tools/build_legacy_ctranslate2.ps1). Older GPUs can't run CUDA 12.
ENGINE_MIN_CC = (5, 3)
LEGACY_MIN_CC = (5, 0)


def needs_legacy_engine(gpu: GpuInfo | None) -> bool:
    return gpu is not None and LEGACY_MIN_CC <= gpu.compute_capability < ENGINE_MIN_CC


def wants_legacy_engine(gpu: GpuInfo | None = None) -> bool:
    """The GPU (default: this PC's) needs the legacy engine, and it can be downloaded."""
    from .cuda_libs import legacy_available

    return legacy_available() and needs_legacy_engine(gpu or nvidia_gpu())


def too_old_for_gpu_support(gpu: GpuInfo | None) -> bool:
    """No engine the app can get has GPU code for ``gpu`` (older than Maxwell, or Maxwell while the legacy
    engine isn't available)."""
    from .cuda_libs import legacy_available

    if gpu is None or gpu.compute_capability == (0, 0):
        return False
    return gpu.compute_capability < (LEGACY_MIN_CC if legacy_available() else ENGINE_MIN_CC)


class _PciInfo(ctypes.Structure):
    _fields_ = [("busIdLegacy", ctypes.c_char * 16), ("domain", ctypes.c_uint), ("bus", ctypes.c_uint),
                ("device", ctypes.c_uint), ("pciDeviceId", ctypes.c_uint), ("pciSubSystemId", ctypes.c_uint),
                ("busId", ctypes.c_char * 32)]


def _nvml_gpus() -> list[tuple[GpuInfo, tuple[int, int, int]]]:
    """Every NVIDIA GPU, through NVML (ships with the driver): (its info, its PCI location)."""
    try:
        nvml = ctypes.WinDLL("nvml.dll")
    except OSError:
        return []
    found = []
    try:
        if nvml.nvmlInit_v2() != 0:
            return []
        count = ctypes.c_uint()
        if nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return []

        class Memory(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

        for index in range(count.value):
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) != 0:
                continue
            name = ctypes.create_string_buffer(96)
            nvml.nvmlDeviceGetName(handle, name, 96)
            mem = Memory()
            nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
            major, minor = ctypes.c_int(), ctypes.c_int()
            if nvml.nvmlDeviceGetCudaComputeCapability(handle, ctypes.byref(major), ctypes.byref(minor)) != 0:
                major.value = minor.value = 0
            pci = _PciInfo()
            location = ((pci.domain, pci.bus, pci.device)
                        if nvml.nvmlDeviceGetPciInfo_v3(handle, ctypes.byref(pci)) == 0 else (index, 0, 0))
            found.append((GpuInfo(name.value.decode(errors="replace"), int(mem.total // 2**20),
                                  int(mem.free // 2**20), (major.value, minor.value)), location))
    except Exception:  # noqa: BLE001
        log.debug("NVML query failed", exc_info=True)
    finally:
        try:
            nvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass
    return found


def choose_gpu(found: list[tuple[GpuInfo, tuple[int, int, int]]], pci_order: bool) -> GpuInfo | None:
    """The GPU to run models on: the newest (then the one with the most memory), with ``cuda_index`` its number
    for CUDA, which counts GPUs in PCI order (cuda_setup sets CUDA_DEVICE_ORDER=PCI_BUS_ID). Without that order
    (``pci_order`` False: the user set CUDA_DEVICE_ORDER or CUDA_VISIBLE_DEVICES), CUDA's first GPU is used and
    NVML's first is the best guess for it."""
    if not found:
        return None
    if not pci_order:
        return found[0][0]
    ranked = [gpu for gpu, _location in sorted(found, key=lambda f: f[1])]
    best = max(range(len(ranked)), key=lambda i: (ranked[i].compute_capability, ranked[i].total_mb, -i))
    return replace(ranked[best], cuda_index=best)


def _pci_order() -> bool:
    return (os.environ.get("CUDA_DEVICE_ORDER", "PCI_BUS_ID") == "PCI_BUS_ID"
            and "CUDA_VISIBLE_DEVICES" not in os.environ)


@lru_cache(maxsize=1)
def nvidia_gpu() -> GpuInfo | None:
    """The NVIDIA GPU models run on, found through the driver alone (no CUDA libraries needed)."""
    return choose_gpu(_nvml_gpus(), _pci_order())


def engine_runs_on(gpu: GpuInfo | None) -> bool:
    """CTranslate2 (as loaded: the official build or the legacy engine) has GPU code for ``gpu``."""
    from .cuda_setup import legacy_engine_loaded

    if gpu is None or gpu.compute_capability == (0, 0):
        return True  # (not known: let CTranslate2 decide)
    return gpu.compute_capability >= (LEGACY_MIN_CC if legacy_engine_loaded() else ENGINE_MIN_CC)


@lru_cache(maxsize=1)
def cuda_available() -> bool:
    """True when CTranslate2 can use an NVIDIA GPU: a GPU, its driver, the CUDA 12 libraries and, for a
    Maxwell GPU, the legacy engine.

    The GPU is never touched without the libraries: cuDNN would abort the process.
    """
    if not cuda_libraries_found():
        return False
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0 and engine_runs_on(nvidia_gpu())
    except Exception:  # noqa: BLE001
        log.info("CUDA not available", exc_info=True)
        return False


def pick_compute_type(supported: set[str]) -> str:
    """The fastest compute type CTranslate2 supports on the GPU: float16 needs tensor cores (compute capability
    7.0 and newer), int8 compute capability 6.1 (GTX 10 series); older GPUs compute in float32."""
    if "float16" in supported:
        return "float16"
    if "int8_float32" in supported:
        return "int8"
    return "float32"


def expected_compute_type(gpu: GpuInfo | None) -> str:
    """What pick_compute_type will choose for ``gpu``, from its compute capability alone (CTranslate2's rules:
    float16 from 7.0, int8 from 6.1), without loading CTranslate2 or touching CUDA."""
    if gpu is None or gpu.compute_capability == (0, 0) or gpu.compute_capability >= (7, 0):
        return "float16"
    return "int8" if gpu.compute_capability >= (6, 1) else "float32"


@lru_cache(maxsize=4)
def _supported_compute_types(device_index: int) -> frozenset[str]:
    import ctranslate2

    return frozenset(ctranslate2.get_supported_compute_types("cuda", device_index))


def cuda_compute_type() -> str:
    """The compute type models use on the GPU (only asked once CUDA is available; a failure isn't remembered)."""
    gpu = nvidia_gpu()
    try:
        return pick_compute_type(set(_supported_compute_types(gpu.cuda_index if gpu else 0)))
    except Exception:  # noqa: BLE001
        log.info("couldn't read the GPU's compute types", exc_info=True)
        return "float32"


def cuda_device_index() -> int:
    gpu = nvidia_gpu()
    return gpu.cuda_index if gpu else 0


def gpu_info() -> GpuInfo | None:
    """The GPU models run on, with its free memory now."""
    return choose_gpu(_nvml_gpus(), _pci_order()) if cuda_available() else None


def refresh_cuda() -> bool:
    """Look for the CUDA libraries again (after they were downloaded)."""
    setup_cuda_dlls(refresh=True)
    cuda_available.cache_clear()
    _supported_compute_types.cache_clear()
    return cuda_available()


def resolve_device(preference: str) -> str:
    """Map the "auto" / "cuda" / "cpu" setting to the device to use."""
    if preference == "cpu":
        return "cpu"
    if cuda_available():
        return "cuda"
    return "cpu"


# Approximate VRAM needed in float16, in MB, and how much more (or less) the other compute types need.
_VRAM_NEED = {"tiny": 400, "base": 500, "small": 1000, "medium": 2300, "turbo": 2200, "large": 4000}
_VRAM_SCALE = {"float16": 1.0, "int8": 0.6, "float32": 2.0}


def realtime_risk(tier: str, device: str, gpu: GpuInfo | None, compute_type: str | None = None) -> str | None:
    """Return "cpu_slow" or "vram_low" when the model will likely lag behind real time.

    ``compute_type``: the GPU's (default: what it will be, from its compute capability)."""
    if device == "cpu":
        return "cpu_slow" if tier in ("medium", "turbo", "large") else None
    if gpu is None or not gpu.total_mb:
        return None
    scale = _VRAM_SCALE.get(compute_type or expected_compute_type(gpu), 1.0)
    return "vram_low" if _VRAM_NEED.get(tier, 4000) * scale > gpu.total_mb * 0.9 else None


# The models first-time setup suggests, best first (all languages, fast enough for live captions).
SUGGESTED_MODELS = ("whisper-large-v3-turbo", "whisper-small", "whisper-base", "whisper-tiny")


def suggest_model(models: list[tuple[str, str]], device: str, gpu: GpuInfo | None) -> str | None:
    """The model to suggest from ``models`` ((id, tier) pairs): the best of SUGGESTED_MODELS that keeps up in
    real time on ``device``. Falls back to the first suggested (or listed) model when none would."""
    tiers = dict(models)
    listed = [m for m in SUGGESTED_MODELS if m in tiers]
    fast = [m for m in listed if realtime_risk(tiers[m], device, gpu) is None]
    return (fast or listed or [m for m, _ in models] or [None])[0]
