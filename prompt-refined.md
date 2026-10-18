# Build: Offline Live Caption App for Windows (Python)

## Goal

Build a Windows 10/11 desktop app in Python that captures audio in real time, transcribes it on the device with a speech-to-text model the user picks, and shows live captions on screen. Once a model is downloaded, the app must work fully offline, with no network calls at runtime.

## Tech stack (fixed)

- **Python** 3.11 or 3.12, Windows 10/11 x64.
- **GUI:** PySide6 + PySide6-Fluent-Widgets (`pip install "PySide6-Fluent-Widgets[full]"`, imported as `qfluentwidgets`). Do **not** use PyQt6: it is a different Qt binding, it can't share a process with PySide6, and this widget library is built for PySide6.
- **ASR backends:** `faster-whisper` (CTranslate2) as the primary backend. Use Hugging Face `transformers` + `torch` only for models that ship in transformers format.
- **Audio capture:** `PyAudioWPatch` or `soundcard`. Both support WASAPI loopback, which is needed to capture system audio.
- **Per-app audio capture:** the Windows process loopback API, plus `pycaw` to list the apps that are playing audio. See [Audio input](#audio-input).
- **Model downloads:** `huggingface_hub`.
- **Packaging:** PyInstaller, one-folder build. Don't bundle models with the app; the user downloads them from inside it.

## Models

Show these in a dropdown. Define them all in one catalog (a JSON file or a list of Python dataclasses). Each entry holds: display name, HF repo id, backend, supported languages, approximate size and license. Adding a model later should only take a new catalog entry.

| Display name | Repo to download | Backend | Notes |
|---|---|---|---|
| Taiwan Tongues ASR CE | `adi-gov-tw/Taiwan-Tongues-ASR-CE` | faster-whisper | The CTranslate2 inference build of `adi-gov-tw/Taiwan-Tongues-ASR-CE-pretrained-v1.0`. That `-pretrained` repo is the training checkpoint, so don't use it for inference. Covers Mandarin, Taiwanese Hokkien, Hakka and English. It was trained with the language codes `nan` (Hokkien) and `hak` (Hakka), which aren't standard Whisper codes. Read the model's tokenizer and config to see which codes it actually accepts. |
| Breeze ASR 25 | `MediaTek-Research/Breeze-ASR-25` | transformers | A fine-tune of Whisper large-v2 for Taiwanese Mandarin, English, and switching between the two mid-sentence. It ships only in transformers format. |
| Whisper tiny / base / small / medium | `Systran/faster-whisper-{size}` | faster-whisper | CTranslate2 conversions of `openai/whisper-{size}`. |
| Whisper large (v1) / large-v2 / large-v3 | `Systran/faster-whisper-large-v1` / `-large-v2` / `-large-v3` | faster-whisper | |
| Whisper large-v3-turbo *(added)* | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | faster-whisper | Close to large-v3 accuracy and much faster. A good default for real-time captioning on a GPU. |

## Audio input

The user picks the audio to caption from three kinds of source:

- **Microphone:** any input device.
- **System audio:** WASAPI loopback of any output device. This captions everything playing on the PC, like Windows Live Captions does.
- **Specific apps (multi-select):** the user ticks one or more running apps, such as Chrome, Teams or VLC. Only those apps' audio is captioned, and sound from every other app is ignored.

### Per-app capture

- **API:** use the Windows process loopback API: `ActivateAudioInterfaceAsync` with `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK` and `PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE`. Microsoft's *ApplicationLoopback* sample shows the full flow.
- **Process tree:** capture the whole process tree, starting from the app's root process. Browsers and Electron apps such as Teams and Discord play audio from child processes, so capturing only the main process would record silence.
- **Implementation:** there's no well-established Python package for this. Implement it with `comtypes`/`ctypes`. If that proves unreliable, propose a small native helper written in C++ from Microsoft's sample, and ask me before adding a native build step.
- **Windows version:** the API needs Windows 10 build 20348 or later, which in practice means Windows 11. On older builds, hide the per-app option and say why. Microphone and system audio must still work there.
- **App list:** list apps that have active audio sessions (`pycaw`, `IAudioSessionManager2`), each with its icon and friendly name. Show one entry per app, even when it runs several processes, by grouping them under the root process. Also let the user pick any running app with a visible window, because an app may not open its audio session until it starts playing. Refresh the list automatically, and also on a Refresh button.
- **Remembered selection:** save the selected apps by executable name, not process ID, and reselect them automatically when they start again.
- **App closes:** if a selected app closes, keep captioning the other selected apps and show a notice. Resume capturing it when it reopens.

### Combining sources

- **Mixing:** capture each selected app as its own stream, resample each one, and mix them into a single stream before VAD and ASR. Mix by summing the streams with a limiter so the result doesn't clip. All selected apps feed one shared caption stream.
- **Adding a microphone:** optionally let the user add one microphone to the app mix, for example to caption both sides of a call.

### General

- Convert all audio to 16 kHz mono float32 before inference.
- If a device is unplugged or changes, recover without crashing.

## Real-time transcription pipeline

Whisper-family models don't stream natively, so build streaming on top of them:

- **Threading:** an audio capture thread feeds a bounded queue, and an ASR worker (QThread, or a thread that sends Qt signals) reads from it. Never run capture or inference on the GUI thread.
- **Voice activity detection:** use Silero VAD so silence is never sent to the model. faster-whisper bundles it, and it works offline.
- **Partial and final captions:** keep a rolling buffer. Re-transcribe the current utterance every 0.5–1 s and show the result as a *partial* caption. When VAD detects the end of speech, or the buffer reaches 15–20 s, emit a *final* caption and start a new buffer.
- **Stable partials:** stop partial text from flickering by using a stabilization strategy such as LocalAgreement (the method used in `whisper_streaming`).
- **Context:** pass the previous final text to the model as the prompt.
- **Hallucination filter:** Whisper tends to invent text on near-silence. Drop known phrases such as "Thank you for watching", 謝謝觀看 and 字幕由…提供, and drop repeated lines.
- **Falling behind:** if inference can't keep up with real time, drop the oldest audio instead of letting latency grow. Show the current latency in the UI.
- **Language:** offer auto-detect or a fixed language. The choices come from the selected model's catalog entry.
- **Chinese output:** Whisper often writes Simplified Chinese. Add an optional OpenCC `s2twp` conversion to Traditional Chinese, on by default.

## Hardware

- Use an NVIDIA GPU (CUDA, float16) when one is available. Otherwise use the CPU (int8).
- Show the active device in the UI.
- Warn when a model will probably be too slow for real time on the current hardware (for example, large models or Breeze on CPU), and suggest a smaller model.
- If CUDA runs out of memory, fall back to CPU or ask the user to pick a smaller model. Don't crash.

## Model management

- **Dropdown:** each model shows its status (Downloaded / Not downloaded) and its size.
- **Download prompt:** selecting a model that isn't downloaded opens a Fluent dialog: "Model X (≈ N GB) isn't downloaded. Download now?" with **Download** and **Cancel**. After the download finishes, the app switches to that model automatically.
- **Downloads:** show a progress bar with bytes and speed. Downloads can be cancelled and resume after an interruption. Check free disk space before starting.
- **Storage:** keep models in `%LOCALAPPDATA%\AppLiveCaption\models`. The folder can be changed in Settings.
- **Offline loading:** load models from local disk only (`local_files_only=True`, and `HF_HUB_OFFLINE=1` at runtime). Having no network must never block launch or transcription.
- **Switching models mid-session:** pause capture, unload the old model and free GPU memory, load the new model in the background with a loading indicator, then resume. The UI must stay responsive the whole time.

## UI (Fluent Design)

Use `FluentWindow` with these navigation pages:

1. **Live Captions:** Start/Stop button, model dropdown, audio source picker, language dropdown, and a large caption area. The audio source picker is a button that opens a flyout. The flyout has a mode switch (Microphone / System audio / Apps) and, in Apps mode, a checklist of apps with their icons. The button's label summarizes the current selection, for example "Chrome + Teams". Final lines appear in the normal text color and the current partial line in a lighter color. A status line shows latency and the active device.
2. **Models:** every model in the catalog, each with download/delete, disk usage and license.
3. **History:** past session transcripts with timestamps. Copy, export as TXT or SRT, and clear.
4. **Settings:** theme (light / dark / follow system), Mica on Windows 11, caption font, size and colors, number of caption lines, model folder, default model and device, and the OpenCC toggle.

**Floating caption overlay**, toggled from the Live Captions page and from a system tray icon:

- Frameless, always on top, with a semi-transparent background.
- Draggable and resizable, and it remembers its position.
- Optional click-through mode.
- Shows the last 1–3 caption lines.

Save all settings with `qfluentwidgets` `QConfig` to a JSON file in `%APPDATA%\AppLiveCaption`.

UI text is in English. Wrap every string with `tr()` so a Traditional Chinese (zh-TW) translation can be added later.

## Error handling & logging

Show errors as Fluent `InfoBar`s, never as raw tracebacks. Handle these cases:

- no audio device, or the device is removed while captioning
- a selected app closes, or per-app capture isn't supported on this Windows build
- download failure
- corrupt or incomplete model files
- GPU out of memory
- a language the selected model doesn't support

Write logs to a rotating file in the app data folder.

## Out of scope for v1

Translation, speaker identification (diarization) and cloud speech-to-text APIs.

## Deliverables

- Source code in a clear package layout, for example:
  ```
  app/
    main.py
    config.py
    ui/          # main window, pages, overlay, tray
    audio/       # capture, resampling, VAD
    asr/         # backend interface, faster_whisper_backend.py, transformers_backend.py, streaming.py
    models/      # catalog, downloader, local storage
  ```
- `requirements.txt` with pinned versions.
- `README.md` covering setup, CUDA requirements, how to run, and how to build the .exe.
- A PyInstaller spec that produces a working one-folder build.

## How to work

Build in milestones, and make each one runnable before starting the next:

1. Audio capture from a microphone, from system loopback, and from several selected apps mixed together, printing the input level of each source to the console.
2. One faster-whisper model transcribing live audio in the console, with VAD and partial/final output.
3. The Fluent main window, with the Live Captions page connected to the pipeline.
4. The model catalog, download dialog and model switching, plus the transformers backend for Breeze ASR 25.
5. The overlay, History with export, and saved Settings.
6. Error handling and packaging.

Ask me before any decision that changes the scope or the tech stack.

## Acceptance criteria

- With the network disabled, the app launches and captions live audio with any model that is already downloaded.
- On a mid-range NVIDIA GPU, with Whisper large-v3-turbo or Breeze ASR 25, partial captions appear within 1–2 s of speech.
- With two apps selected (for example a browser playing a video and a Teams call), the captions include speech from both apps and nothing from apps that aren't selected.
- Switching models never freezes the UI.
- Selecting a model that isn't downloaded prompts a download. Once the download finishes, captioning uses that model automatically.
