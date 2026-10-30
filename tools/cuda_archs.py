"""List the NVIDIA GPU architectures a DLL has code for (its CUDA fatbins).

    python tools/cuda_archs.py path/to/ctranslate2.dll [--require 50,52]

Prints e.g. "SASS 53 60 61 70 75 80 86 / PTX 86": SASS is compiled code, which runs on that compute capability
(and newer minor versions of it); PTX is compiled by the driver for newer GPUs. CTranslate2's official Windows
build has no SASS for Maxwell (50, 52), which is why the legacy engine exists (tools/build_legacy_ctranslate2.ps1).
"""

from __future__ import annotations

import argparse
import struct
import sys

FATBIN_MAGIC = struct.pack("<I", 0xBA55ED50)


def archs(data: bytes) -> tuple[set[int], set[int]]:
    """(SASS architectures, PTX architectures), e.g. ({52, 86}, {86})."""
    sass, ptx = set(), set()
    pos = data.find(FATBIN_MAGIC)
    while pos != -1:
        header_size, fat_size = struct.unpack_from("<HQ", data, pos + 6)
        off, end = pos + header_size, pos + header_size + fat_size
        while off + 32 <= min(end, len(data)):
            kind, _version, entry_header, size = struct.unpack_from("<HHIQ", data, off)
            if entry_header == 0 or kind not in (1, 2):
                break
            arch = struct.unpack_from("<I", data, off + 28)[0]
            (ptx if kind == 1 else sass).add(arch)
            off += entry_header + size
        pos = data.find(FATBIN_MAGIC, pos + 4)
    return sass, ptx


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dll")
    p.add_argument("--require", default="", help="comma-separated SASS architectures that must be there, e.g. 50,52")
    args = p.parse_args()
    with open(args.dll, "rb") as fh:
        sass, ptx = archs(fh.read())
    print("SASS", *sorted(sass), "/ PTX", *sorted(ptx))
    missing = [a for a in (int(x) for x in args.require.split(",") if x) if a not in sass]
    if missing:
        sys.exit(f"missing SASS for: {', '.join(map(str, missing))}")


if __name__ == "__main__":
    main()
