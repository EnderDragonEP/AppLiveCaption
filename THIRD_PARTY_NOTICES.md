# Third-party notices

AppLiveCaption is licensed under the [GNU General Public License v3.0](LICENSE).
It is built on the open-source components below. The released `AppLiveCaption.exe` folder contains all of them; running from source only needs them installed (see `requirements.txt`).

## Main components

| Component | Version | License | Source |
| --- | --- | --- | --- |
| [PySide6](https://pyside.org) and shiboken6 | 6.12.0 | LGPL-3.0 | <https://code.qt.io/cgit/pyside/pyside-setup.git/> |
| [Qt 6](https://www.qt.io/) (via PySide6) | 6.12.0 | LGPL-3.0 | <https://download.qt.io/official_releases/qt/> |
| [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) | 1.11.3 | GPL-3.0 | <https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/PySide6> |
| [PySideSix-Frameless-Window](https://github.com/zhiyiYo/PyQt-Frameless-Window) | 0.8.2 | LGPL-3.0 | <https://github.com/zhiyiYo/PyQt-Frameless-Window/tree/PySide6> |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (includes the Silero VAD model) | 1.2.1 | MIT | <https://github.com/SYSTRAN/faster-whisper> |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | 4.6.2 | MIT | <https://github.com/OpenNMT/CTranslate2> |
| [ONNX Runtime](https://onnxruntime.ai) | 1.31.0 | MIT | <https://github.com/microsoft/onnxruntime> |
| [NVIDIA cuBLAS, cuDNN and NVRTC](https://developer.nvidia.com/cuda-toolkit) (nvidia-cublas-cu12, nvidia-cudnn-cu12, nvidia-cuda-nvrtc-cu12) | cuBLAS 12.4.5.8, cuDNN 9.1.0.70, NVRTC 12.4.127 | NVIDIA Software License | see [below](#nvidia-cuda-and-cudnn) |
| [PyAV](https://github.com/PyAV-Org/PyAV) | 15.1.0 | BSD-3-Clause | <https://github.com/PyAV-Org/PyAV> |
| [FFmpeg](https://ffmpeg.org/) and its codec libraries (via PyAV) | 7.1.1 | LGPL-3.0-or-later; x264, x265 GPL-2.0-or-later | see [below](#ffmpeg-via-pyav) |
| [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) | 0.2.12.9 | Apache-2.0 | <https://github.com/s0d3s/PyAudioWPatch> |
| [PortAudio](http://www.portaudio.com/) (via PyAudioWPatch) | 19.7 | MIT | <https://github.com/PortAudio/portaudio> |
| [python-soxr](https://github.com/dofuuz/python-soxr) with libsoxr | 1.1.0 | LGPL-2.1-or-later | <https://github.com/dofuuz/python-soxr> |
| [Python](https://www.python.org/) | 3.12 | PSF License Agreement | <https://www.python.org/downloads/source/> |
| [PyInstaller](https://pyinstaller.org/) bootloader | 6.22.3 | GPL-2.0-or-later with Bootloader Exception | <https://github.com/pyinstaller/pyinstaller> |

## Supporting packages

| Package | Version | License | Source |
| --- | --- | --- | --- |
| annotated-doc | 0.0.4 | MIT | <https://github.com/fastapi/annotated-doc> |
| anyio | 4.8.0 | MIT | <https://github.com/agronholm/anyio> |
| certifi | 2025.1.31 | MPL-2.0 | <https://github.com/certifi/python-certifi> |
| click | 8.3.1 | BSD-3-Clause | <https://github.com/pallets/click> |
| colorama | 0.4.6 | BSD-3-Clause | <https://github.com/tartley/colorama> |
| comtypes | 1.4.17 | MIT | <https://github.com/enthought/comtypes> |
| darkdetect | 0.8.0 | BSD-3-Clause | <https://github.com/albertosottile/darkdetect> |
| filelock | 3.20.0 | Unlicense | <https://github.com/tox-dev/py-filelock> |
| fsspec | 2025.12.0 | BSD-3-Clause | <https://github.com/fsspec/filesystem_spec> |
| h11 | 0.16.0 | MIT | <https://github.com/python-hyper/h11> |
| hf-xet | 1.4.3 | Apache-2.0 | <https://github.com/huggingface/xet-core> |
| httpcore | 1.0.9 | BSD-3-Clause | <https://github.com/encode/httpcore> |
| httpx | 0.28.1 | BSD-3-Clause | <https://github.com/encode/httpx> |
| huggingface_hub | 1.11.0 | Apache-2.0 | <https://github.com/huggingface/huggingface_hub> |
| idna | 3.10 | BSD-3-Clause | <https://github.com/kjd/idna> |
| markdown-it-py | 3.0.0 | MIT | <https://github.com/executablebooks/markdown-it-py> |
| mdurl | 0.1.2 | MIT | <https://github.com/executablebooks/mdurl> |
| NumPy (includes OpenBLAS) | 2.3.5 | BSD-3-Clause | <https://github.com/numpy/numpy> |
| opencc-python-reimplemented | 0.1.7 | Apache-2.0 | <https://github.com/yichen0831/opencc-python> |
| packaging | 24.2 | Apache-2.0 OR BSD-2-Clause | <https://github.com/pypa/packaging> |
| psutil | 7.2.2 | BSD-3-Clause | <https://github.com/giampaolo/psutil> |
| pycaw | 20260927 | MIT | <https://github.com/AndreMiras/pycaw> |
| Pygments | 2.20.0 | BSD-2-Clause | <https://github.com/pygments/pygments> |
| pywin32 | 310 | PSF-2.0 / BSD-3-Clause style | <https://github.com/mhammond/pywin32> |
| PyYAML | 6.0.3 | MIT | <https://github.com/yaml/pyyaml> |
| rich | 14.3.4 | MIT | <https://github.com/Textualize/rich> |
| setuptools | 75.8.1 | MIT | <https://github.com/pypa/setuptools> |
| shellingham | 1.5.4 | ISC | <https://github.com/sarugaku/shellingham> |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | <https://github.com/python-trio/sniffio> |
| tokenizers | 0.22.2 | Apache-2.0 | <https://github.com/huggingface/tokenizers> |
| tqdm | 4.67.1 | MPL-2.0 AND MIT | <https://github.com/tqdm/tqdm> |
| typer | 0.24.2 | MIT | <https://github.com/fastapi/typer> |
| typing_extensions | 4.15.0 | PSF-2.0 | <https://github.com/python/typing_extensions> |

The versions are the ones used to build the exe.

## Speech recognition models (not included)

The models are not part of the app. Each one is downloaded from Hugging Face on request and is used under its own license, shown on the Models page:

| Model | License |
| --- | --- |
| Whisper tiny, base, small, medium, large v1/v2/v3, large-v3-turbo (CTranslate2 conversions of OpenAI Whisper) | MIT |
| Breeze ASR 25 (MediaTek Research; CTranslate2 conversion by SoybeanMilk) | Apache-2.0 |
| Taiwan Tongues ASR CE (adi-gov-tw) | TRAIL v0.1 (TWM Responsible AI License) |

## GPL-3.0 components

PySide6-Fluent-Widgets is Copyright (c) zhiyiYo. It is used under the GNU General Public License v3.0, the same license as AppLiveCaption; its full text is in [LICENSE](LICENSE).

## Qt, PySide6 and PySideSix-Frameless-Window (LGPL-3.0)

The exe contains the Qt 6 libraries, Copyright (c) The Qt Company Ltd. and other contributors, and PySide6 and shiboken6, Copyright (c) The Qt Company Ltd. PySideSix-Frameless-Window is Copyright (c) zhiyiYo. They are used under the
[GNU Lesser General Public License v3.0](https://www.gnu.org/licenses/lgpl-3.0.html).
Qt's source code is available from <https://download.qt.io/official_releases/qt/> and PySide6's from <https://code.qt.io/cgit/pyside/pyside-setup.git/>.
Because AppLiveCaption's complete source code and build script are public, you can rebuild the exe with modified versions of these libraries.

## FFmpeg (via PyAV)

PyAV includes FFmpeg 7.1.1 (libavcodec, libavformat, libavutil, libavfilter, libavdevice, libswscale, libswresample), built under the
[GNU Lesser General Public License v3.0 or later](https://www.gnu.org/licenses/lgpl-3.0.html), together with codec libraries under their own licenses:
x264 and x265 (GPL-2.0-or-later), LAME, TwoLAME, libiconv and GMP (LGPL), libvpx, libaom, dav1d, SVT-AV1, OpenH264, Opus, Ogg, Vorbis, Speex and libwebp (BSD-style), opencore-amr (Apache-2.0), libsrt (MPL-2.0), zlib (zlib License) and OpenSSL 1.1.
The source code for these builds is available from FFmpeg (<https://ffmpeg.org/download.html>) and from PyAV's build scripts (<https://github.com/PyAV-Org/pyav-ffmpeg>).

## NVIDIA CUDA and cuDNN

The exe contains NVIDIA's cuBLAS, cuDNN and NVRTC libraries, Copyright (c) NVIDIA Corporation, from NVIDIA's `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` and `nvidia-cuda-nvrtc-cu12` packages.
They are redistributed under the [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/) and the [cuDNN Software License Agreement](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html). They are not open source.

## Python (PSF License Agreement)

The exe contains the Python runtime and parts of its standard library, used under the
[PSF License Agreement](https://docs.python.org/3.12/license.html).

Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation; All Rights Reserved.

## PyInstaller bootloader

The exe starts through PyInstaller's bootloader, licensed under the GNU General Public License v2.0 or later with this exception:

> In addition to the permissions in the GNU General Public License, the authors give you unlimited permission to link or embed compiled bootloader and related files into combinations with other programs, and to distribute those combinations without any restriction coming from the use of those files. (The General Public License restrictions do apply in other respects; for example, they cover modification of the files, and distribution when not linked into a combined executable.)
