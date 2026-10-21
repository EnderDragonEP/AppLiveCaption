# AppLiveCaption

Offline live captions for Windows 10/11. AppLiveCaption captures audio from a
microphone, from everything playing on the PC, or from specific apps only, and
transcribes it on your own machine with a speech-to-text model you pick. Once a
model is downloaded, it works with no network connection.

- **Sources:** microphone, system audio (WASAPI loopback of any output device),
  or a ticked set of apps (Chrome, Teams, VLC, …) mixed together, optionally plus
  one microphone.
- **Models:** Whisper tiny → large-v3, large-v3-turbo, Taiwan Tongues ASR CE
  (Mandarin / Taiwanese Hokkien / Hakka / English), Breeze ASR 25 (Taiwanese
  Mandarin + English code-switching) and Breeze ASR 26 (Taiwanese Hokkien /
  Taigi, written as Mandarin characters).
- **Live output:** partial captions that update while someone speaks (lighter
  color) and final captions when they pause, a floating always-on-top overlay,
  and a history you can copy or export as TXT or SRT.
- **Chinese:** optional Simplified → Traditional conversion (OpenCC `s2twp`), on
  by default.

## Requirements

| Requirement | Details |
| --- | --- |
| OS | Windows 10 or 11, 64-bit. Captioning individual apps needs Windows build 20348 or later (in practice Windows 11); on older builds that option is hidden and microphone/system audio still work. |
| Python | 3.11 or 3.12 (64-bit) |
| GPU (optional) | NVIDIA GPU with a recent driver (R525 or newer, any driver that supports CUDA 12). Without one, models run on the CPU (int8). |
| Disk | About 1 GB for the Python packages, 1.3 GB for GPU support (downloaded in the app, only with an NVIDIA GPU), plus 0.1–3 GB per model |

### CUDA

You do **not** need to install the CUDA Toolkit, and the app doesn't use
PyTorch; only the NVIDIA display driver is required. To run on the GPU,
faster-whisper (CTranslate2) needs NVIDIA's cuBLAS 12 and cuDNN 9 libraries.
They take 1.3 GB, so neither the built app nor `requirements.txt` includes them.
On a PC with an NVIDIA GPU, the **Models** page shows an **NVIDIA GPU support**
card that downloads them once (about 1 GB), and the app suggests it at start-up
while models run on the CPU. `python -m app.cli cuda --download` does the same
from a terminal.

The download ([app/asr/cuda_libs.py](app/asr/cuda_libs.py)):

- fetches NVIDIA's own pip packages (`nvidia-cublas-cu12` 12.4.5.8,
  `nvidia-cudnn-cu12` 9.1.0.70, `nvidia-cuda-nvrtc-cu12` 12.4.127) from PyPI
  and resumes where it stopped after an interruption,
- checks each one against the SHA-256 pinned in the source before unpacking it,
- keeps only the ten DLLs CTranslate2 loads, in
  `%LOCALAPPDATA%\AppLiveCaption\cuda`.

A model that is already loaded moves to the GPU as soon as the download
finishes. The card's delete button removes the libraries again; because Windows
locks DLLs that are loaded, that happens the next time the app starts. On a PC
without internet access, copy that `cuda` folder from another PC.

The versions are pinned to CUDA 12.4 / cuDNN 9.1.0.70 to match the cuDNN loader
that CTranslate2 4.6 bundles (`cudnn64_9.dll`); cuDNN must not be older than
that loader. The app also uses the same libraries when they're already on the
PC, in this order: the downloaded folder, NVIDIA's pip packages (handy when
running from source), PyTorch's `torch\lib` (when PyTorch is installed for
something else), or a CUDA 12.x Toolkit (`CUDA_PATH_V12_*`)
([app/asr/cuda_setup.py](app/asr/cuda_setup.py)). Only a complete set counts;
otherwise models run on the CPU.

## Setup

The app uses the packages of your installed Python; no virtual environment is
needed.

```powershell
git clone <this repo> AppLiveCaption
cd AppLiveCaption
py -3.12 -m pip install -r requirements.txt
```

`requirements.txt` is written to share a Python with other software:

- Packages only this app uses (PySide6, faster-whisper, onnxruntime, the audio
  libraries, …) are pinned to the tested versions.
- Packages that other software commonly shares (NumPy, huggingface_hub,
  CTranslate2, …) take a version range, so the versions you already have are
  kept whenever they work. PyTorch isn't needed; if it's installed for other
  projects, it's left alone (the app even keeps CTranslate2 from importing it).
- To see what pip would change before installing anything, run
  `py -3.12 -m pip install --dry-run -r requirements.txt`.
- Chinese conversion uses `opencc-python-reimplemented`, which gives the same
  results as the `OpenCC` package. Don't install both: they share the `opencc`
  folder and overwrite each other's files.

If you'd rather keep the app's packages separate, a virtual environment still
works: create one, install `requirements.txt` into it, and run the app or
`build.ps1 -Python .venv\Scripts\python.exe` with it.

## Run

Double-click **`AppLiveCaption.pyw`** in the project folder. It runs the app
from source with no console window and no build, using the Python that Windows
opens `.pyw` files with (the `py` launcher's default, normally the newest
installed Python). If that Python is missing packages, a message box lists them
with the exact `pip install` command to run.

From a terminal:

```powershell
python -m app.main                 # or: pyw AppLiveCaption.pyw
python -m app.main --start         # start captioning as soon as the model has loaded
python -m app.main --minimized     # start minimized, e.g. when launched at sign-in
```

1. Pick a model in the **Model** dropdown. Models that aren't downloaded yet are
   grayed out; picking one opens its card on the **Models** page, where you can
   download it. The app switches to it when the download finishes.
2. Click the source button (it reads *System audio* at first) to choose
   **Microphone**, **System audio** or **Apps**. In Apps mode, tick the apps to
   caption; only their audio is used. Apps you tick are remembered by
   executable name and picked up again whenever they start.
3. Choose **Auto-detect** or a fixed language, then press **Start**.
4. **Overlay** shows the last 1–3 lines in a floating window. Drag it to move it,
   drag its edges to resize it. Click-through mode (pin button, Settings, or the
   tray menu) lets clicks pass through to the window below; turn it off from the
   tray icon.

The status line shows the current latency and the device in use. Switching
models while captioning pauses capture, loads the new model in the background
and resumes.

### Recommended models

| Hardware | Model |
| --- | --- |
| NVIDIA GPU (6 GB+) | Whisper large-v3-turbo (default) — partials in ~0.3 s on an RTX 4070 Laptop |
| Taiwanese Mandarin / code-switching | Breeze ASR 25 (GPU) |
| Taiwanese Hokkien (Taigi) | Breeze ASR 26 (GPU) |
| Hakka | Taiwan Tongues ASR CE (GPU) |
| CPU only | Whisper base or small |

The app warns when the chosen model will probably be too slow for real time on
the current hardware. If the GPU runs out of memory, the model is moved to the
CPU and the app suggests a smaller one. If recognition still can't keep up, the
oldest audio is dropped instead of letting the delay grow.

## Models

All models are listed in [app/models/catalog.json](app/models/catalog.json).
Adding a model only takes a new entry there (display name, Hugging Face repo,
backend, languages, approximate size, license). Every model runs on
faster-whisper, so it must be in CTranslate2 format; a model published only in
Hugging Face transformers format can be converted with CTranslate2's
`ct2-transformers-converter` (`--quantization float16`).

| Model | Repository | Backend |
| --- | --- | --- |
| Whisper large-v3-turbo | `dropbox-dash/faster-whisper-large-v3-turbo` | faster-whisper |
| Taiwan Tongues ASR CE | `adi-gov-tw/Taiwan-Tongues-ASR-CE-v1.0` | faster-whisper |
| Breeze ASR 25 | `SoybeanMilk/faster-whisper-Breeze-ASR-25` | faster-whisper |
| Breeze ASR 26 (Taigi) | `SoybeanMilk/faster-whisper-Breeze-ASR-26` | faster-whisper |
| Whisper tiny / base / small / medium | `Systran/faster-whisper-{size}` | faster-whisper |
| Whisper large v1 / v2 / v3 | `Systran/faster-whisper-large-{v1,v2,v3}` | faster-whisper |

Notes:

- Two repositories were renamed on Hugging Face and the catalog uses the current
  names: `mobiuslabsgmbh/faster-whisper-large-v3-turbo` →
  `dropbox-dash/faster-whisper-large-v3-turbo`, and
  `adi-gov-tw/Taiwan-Tongues-ASR-CE` → `adi-gov-tw/Taiwan-Tongues-ASR-CE-v1.0`.
- **Taiwan Tongues ASR CE:** its tokenizer and `config.json` contain only the 99
  standard Whisper language tokens; there are no `nan` (Hokkien) or `hak` (Hakka)
  tokens. Hokkien and Hakka are recognised under `zh`, so the app offers
  *Auto-detect*, *Mandarin / Taiwanese Hokkien / Hakka* (`zh`) and *English*.
  Its license is the TRAIL v0.1 responsible-AI license.
- **Breeze ASR 25:** MediaTek Research publishes it only in transformers
  format (`MediaTek-Research/Breeze-ASR-25`), so the app uses SoybeanMilk's
  float16 CTranslate2 conversion. Its `model.bin` is byte-for-byte identical to a
  conversion made locally with `ct2-transformers-converter --quantization
  float16`, and on 27 test utterances it gave the same transcripts as the
  original on PyTorch for all Mandarin and English speech, at about half the
  decoding time.
- **Breeze ASR 26:** a fine-tune of Whisper large-v2 for Taiwanese Hokkien
  (Taigi). It writes Taigi speech as **Mandarin Chinese characters**, not Taigi
  orthography (台語正字). The app uses SoybeanMilk's CTranslate2 conversion of
  `MediaTek-Research/Breeze-ASR-26`; on 16 Common Voice Taigi clips it matched
  the original on PyTorch exactly (30.7% character error rate for both, against
  39% for Breeze ASR 25 on the same clips). That repository has no
  `tokenizer.json`, so the catalog's `extra_files` takes it from
  `SoybeanMilk/faster-whisper-Breeze-ASR-25`, whose vocabulary is identical;
  without one, faster-whisper would fetch a tokenizer from the internet.
  Whisper's language detection doesn't recognise Taigi (in testing it guessed
  Lao, Georgian and others), so the catalog sets `"auto_detect": false`: the
  model always decodes as `zh`, and its language dropdown only offers
  *Taiwanese Hokkien (Taigi)*.

Click a model's card on the **Models** page to open its page on Hugging Face.

Models are stored in `%LOCALAPPDATA%\AppLiveCaption\models` (changeable in
Settings). Downloads show bytes and speed, can be cancelled, resume from where
they stopped, and check free disk space first.

## Offline operation

The app sets `HF_HUB_OFFLINE=1` at start-up and loads models from their local
folders only (`local_files_only=True`). The downloader lifts that flag only for
its own requests while a download runs. GPU support is downloaded the same
way, only when you click **Download** on its card. No network access is needed
to launch or caption.

The only other network request is **Check for updates** (Settings → About, or
the tray menu), and only when you click it: it asks the GitHub API for the
latest release of this repository and offers to open its download page if it's
newer than the running version. Publish releases with tags like `v1.0.1`.

## Data locations

| What | Where |
| --- | --- |
| Settings | `%APPDATA%\AppLiveCaption\config.json` |
| History | `%APPDATA%\AppLiveCaption\history\*.json` |
| Logs (rotating) | `%APPDATA%\AppLiveCaption\logs\app.log` (Settings → Advanced → Logs opens the folder) |
| Models | `%LOCALAPPDATA%\AppLiveCaption\models` |
| GPU support (NVIDIA's CUDA libraries) | `%LOCALAPPDATA%\AppLiveCaption\cuda` |

## Console tools

Handy for testing capture and recognition without the GUI:

```powershell
python -m app.cli devices                                  # microphones and loopback devices
python -m app.cli apps                                     # apps that can be captured
python -m app.cli cuda [--download]                        # GPU support status / download it
python -m app.cli levels --mode system                     # input level per source
python -m app.cli levels --mode apps --apps chrome.exe,ms-teams.exe --mic --record mix.wav
python -m app.cli transcribe --mode system --model whisper-large-v3-turbo
python -m app.cli transcribe --model breeze-asr-25 --wav speech.wav   # simulated real time
```

## Build the .exe

```powershell
.\build.ps1
```

`build.ps1` uses your installed Python 3.12 or 3.11 (what `py -3.12` or
`py -3.11` finds, otherwise `python` on PATH), checks that the required packages
are installed, runs the unit tests, and builds with PyInstaller. The one-folder
build is written to `dist\AppLiveCaption\`; run
`dist\AppLiveCaption\AppLiveCaption.exe`. Models and GPU support are not bundled;
users download them from inside the app.

| Option | Effect |
| --- | --- |
| `-Python <path>` | Build with this `python.exe` instead of finding one. |
| `-Console` | Keep a console window in the built app, for debugging. |
| `-Clean` | Delete earlier build output and PyInstaller's cache first. |
| `-Install` | Run `pip install -r requirements.txt` for that Python before building. |
| `-SkipTests` | Skip the unit tests. |

If PowerShell refuses to run the script because of the execution policy, run
`powershell -ExecutionPolicy Bypass -File .\build.ps1` instead. `Get-Help
.\build.ps1 -Detailed` lists the options.

The build only bundles what the app needs, even when your Python has many other
packages installed: the spec reads `requirements.txt`, follows the installed
packages' dependencies, and excludes everything else
([tools/build_excludes.py](tools/build_excludes.py); run it to see the list).
Without that, PyInstaller would follow optional imports into unrelated packages
(CTranslate2 alone would pull in an installed PyTorch).

The folder is about 210 MB (about 80 MB zipped). NVIDIA's CUDA libraries are not
bundled (the app downloads them, see [CUDA](#cuda)), and the spec leaves out
what the app never loads: PyAV and FFmpeg (faster-whisper only needs them to
read audio files; `tools/pyi_rth_no_av.py` stands in for the import),
Hugging Face's Xet client, Qt's software OpenGL renderer, Qt Quick/QML, Qt's own
translations, unused image formats and plugins, and the optional SciPy/Pillow
extras of the widget library.
A build takes about 1–2 minutes. Without the script, the equivalent is
`pyinstaller AppLiveCaption.spec --noconfirm` (set `ALC_CONSOLE=1` in the
environment for a console window).

### Version

The app's name and version live in [app/\_\_init\_\_.py](app/__init__.py)
(`__version__`, currently 1.0.0). Settings → About shows them, and the build
writes them into the `.exe`'s file properties (Properties → Details). To release
a new version, change `__version__` there and rebuild.

## Translating the UI

All UI text is English and goes through `tr()`. To add Traditional Chinese:

```powershell
pyside6-lupdate (Get-ChildItem app -Recurse -Filter *.py).FullName -ts app\i18n\app_zh_TW.ts
pyside6-linguist app\i18n\app_zh_TW.ts          # translate
pyside6-lrelease app\i18n\app_zh_TW.ts -qm app\i18n\app_zh_TW.qm
```

The app loads `app\i18n\app_<locale>.qm` for the Windows display language
automatically; the PyInstaller spec bundles any `.qm` files it finds.

## How it works

```text
app/
  main.py              entry point (sets offline mode, theme, splash)
  config.py            QConfig settings
  cli.py               console tools
  audio/               capture (PyAudioWPatch, process loopback via comtypes),
                       app list (pycaw), resampling (soxr), mixer + limiter, Silero VAD
  asr/                 backend interface, faster_whisper_backend.py,
                       streaming.py (partials/finals, LocalAgreement, prompts),
                       hallucination filter, OpenCC, GPU support
                       (cuda_libs.py downloads it, cuda_setup.py finds it)
  models/              catalog, downloader, local storage
  core/                controller (worker thread) and history
  ui/                  main window, pages, source flyout, overlay, tray
```

- **Per-app capture** uses the Windows process loopback API
  (`ActivateAudioInterfaceAsync` with `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`
  and `PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE`), implemented in pure
  Python with comtypes (`app/audio/process_loopback.py`). Each app is captured
  from its root process so child renderer processes are included.
- **Mixing:** every source is converted to 16 kHz mono, then a wall-clock mixer
  sums them and a peak limiter prevents clipping. Silence is inserted for
  sources that deliver nothing (loopback endpoints send no data while nothing
  plays), so the end of an utterance is still detected.
- **Streaming:** Silero VAD (the ONNX model bundled with faster-whisper) gates
  the audio. The current utterance is re-transcribed every ~0.6 s for the partial
  caption; LocalAgreement fixes the start of the partial once two hypotheses
  agree. After 0.6 s of silence, or at 18 s, a final caption is decoded with beam
  search and the next utterance starts, prompted with the previous final caption.
- **Hallucination filter:** drops stock phrases such as "Thank you for watching",
  謝謝觀看 and 字幕由…提供, collapses repeated phrases, drops repeated lines, and
  retries without the prompt when Whisper echoes it.

## Known limitations

- Speech that overlaps between several selected apps is transcribed as one
  stream, so the louder speaker usually wins.
- Translation, speaker identification and cloud speech APIs are out of scope.

## Disclaimer

This software is provided "as is", without warranty of any kind. Use it at your own risk; the author is not responsible for any damage or data loss resulting from its use.

Captions are generated automatically and can be incomplete or wrong. Don't rely on them where accuracy matters, such as medical, legal or safety-critical situations.

AppLiveCaption started as a personal project. It is not affiliated with or endorsed by Microsoft, OpenAI, MediaTek, Hugging Face or NVIDIA. The speech recognition models are made by their respective authors and used under their own licenses.

Claude Code was used in the development of this software.

## License

[GPL-3.0](LICENSE) © 2026 Ender Yang. Dependency information is in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
