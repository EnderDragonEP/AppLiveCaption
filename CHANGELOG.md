# Changelog

All notable changes to AppLiveCaption are listed here, newest first.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version numbers follow [Semantic Versioning](https://semver.org/).

## [1.3.0] - 2026-10-30

### Added

- **Translate to English.** A **Translate to English** button on the Live captions page writes the captions in English, whatever language is spoken, with Whisper's own translation. It can be switched while captioning. Whisper large-v3, large-v2 and medium translate best; Whisper large-v3-turbo (the default) and the Taiwan models weren't trained to translate and mostly keep the spoken language, so the app warns when one of them is asked to. The console's `transcribe` command has `--translate` too.
- **GPU support for older NVIDIA GPUs.** GeForce GTX 900 series GPUs (Maxwell) can now run models on the GPU: GPU support also downloads an engine built for them (about 20 MB), and the app uses it from the next start. If GPU support is already installed, only the engine is downloaded.

### Fixed

- GeForce GTX 10 series GPUs were turned away from GPU support; they now run models in int8 (or float32). Each GPU uses the fastest compute type it supports, shown in the status line.
- GPU support no longer says to update the driver for a GPU that's simply too old: GPUs older than the GTX 900 series say so, and first-time setup doesn't offer them the download.

## [1.2.0] - 2026-10-24

### Added

- **Keyword alerts.** A new **Keywords** tab watches the captions for words or names. When one is said, it's highlighted in the live captions, the overlay and History, and the app plays a sound or shows a Windows notification, and lists it under Recent matches. Chinese keywords match by sound, tones ignored, so a misheard character that sounds the same still counts; they can be typed as 漢字, Zhuyin or Pinyin (plain-letter Pinyin like "taibei" with **Match by sound** ticked, so English words stay English), and 模糊音 switches (zh = z, n = l, in = ing, ...) loosen the match.
- **Minimize to tray.** Minimizing the window now hides it to the tray icon (click the icon to bring it back), and the first time a tray message says where it went. Turn it off in **Settings → Minimize to tray**. Starting with `--minimized` also goes straight to the tray.
- **Traditional and Simplified Chinese.** The whole app is translated, including language names. The app uses one when Chinese comes before English in your Windows language list.
- **The website in Chinese.** The project page has Traditional and Simplified Chinese versions, with a language menu; a browser set to Chinese gets its version on the first visit.
- **Settings → Language** switches between English, 繁體中文, 简体中文 and following Windows. The change applies after a restart, and a **Restart now** button does that for you.

### Changed

- **Chinese output goes both ways.** The "Traditional Chinese output" switch is now **Settings → Chinese output**: Traditional Chinese, Simplified Chinese, or as recognized. Your earlier choice carries over.
- **The first start follows Windows.** When Simplified Chinese comes before Traditional in your Windows language list, a new install writes captions in Simplified Chinese with the Microsoft YaHei UI font; otherwise it keeps Traditional Chinese and Microsoft JhengHei UI.
- The app is about 20 MB smaller (installer 52 MB, portable zip 75 MB): it no longer includes files it never loads.

## [1.1.0] - 2026-10-23

### Added

- **An installer.** `AppLiveCaption-<version>-Setup.exe` installs the app for you only (no admin rights needed) or for all users, with a Start menu entry, an optional desktop shortcut and an uninstaller. The installed app keeps its settings and history in `%APPDATA%\AppLiveCaption`, and its logs, models and GPU support in `%LOCALAPPDATA%\AppLiveCaption`. Running the next Setup updates it in place. Uninstalling lets you tick which of your settings and history, downloaded models and GPU support to delete too; everything is kept by default. The portable zip is still available and keeps its data next to the exe as before.
- **Add your own models.** The Models page has an **Add model** button: paste a Hugging Face repository (its ID or link) or pick a folder on your PC that holds a faster-whisper (CTranslate2) model. The app checks the model first and tells you what's wrong if it can't be used. A model from a folder is copied into the models folder, with progress and Cancel/Resume like a download.
- **Remove models from the list.** Each model card has a **…** menu with **Delete files from disk** and **Remove from list**. Removing a model can delete its downloaded files too (ticked by default).
- **Bring the presets back.** **Settings → Reset model list** restores the preset models at any time and removes the ones you added, optionally with their files. When every model has been removed, the Models page shows a **Restore default models** button.
- **Unload a model.** The card of the model in use has an **Unload** button that frees the memory it uses. The Live page's model dropdown then shows **Select a model**.
- **Choose what loads at start-up.** **Settings → Default model** now offers **None** and **Previous selection** (the model you used last) besides the models in the list.
- **First-time setup.** At the first start, a short setup offers GPU support (on PCs with an NVIDIA GPU that needs it), suggests a model that's fast enough for the PC and downloads it, and asks which model to load at start-up. **Settings → First-time setup** opens it again. It replaces the start-up notification that suggested GPU support.
- A notification when you pick or start a model that isn't downloaded, next to its highlighted card on the Models page, and when you press **Start** with no model selected.
- Descriptions for the overlay settings (text size, text color, background color and opacity) and for the name field when adding a model.

### Changed

- **No model is loaded at start-up by default**, so the app doesn't take memory until you need a model. First-time setup asks which one to load, and **Settings → Default model** changes it later. Existing settings keep the default model they already have.
- **The number of caption lines follows the window's height.** The Live page and the overlay show as many of the newest lines as fit, so resizing them shows more or fewer lines.
- **A model card's link opens on Ctrl+click**, like a link in Office, instead of on any click, so a stray click doesn't open your browser. Hovering a card says where the link goes, and clicking a card three times in a row without Ctrl explains how to open it.
- Pressing **Start** with a downloaded model selected that isn't loaded (for example after loading it failed) now loads it and starts, instead of showing an error.
- Model cards show fewer tooltips, and tooltips no longer stay behind on the screen after scrolling the Models page.

### Removed

- The **Caption lines** and **Overlay lines** settings: the line count now follows the height of the window and the overlay. A saved line count is ignored.

## [1.0.0] - 2026-10-22

The first release.

- Live captions for your microphone, everything playing on the PC (any output device), or only the apps you choose, mixed together.
- Partial captions that update while someone speaks and settle into a final line when they pause.
- An always-on-top floating overlay that can be moved, resized and made click-through.
- Speech recognized on your PC with faster-whisper (CTranslate2): Whisper tiny to large-v3 and large-v3-turbo, Breeze ASR 25, Breeze ASR 26 (Taigi) and Taiwan Tongues ASR CE. Models are downloaded in the app from the Models page.
- GPU support for NVIDIA cards, downloaded in the app with one click (about 1 GB) instead of being bundled. Without it, models run on the CPU.
- Optional Simplified to Traditional Chinese conversion (OpenCC).
- History of every session, with copy and export to TXT or SRT.
- Portable: settings, history, logs, models and GPU support are kept next to the exe.
- A manual check for updates in Settings → About.

[1.3.0]: https://github.com/EnderDragonEP/AppLiveCaption/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/EnderDragonEP/AppLiveCaption/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/EnderDragonEP/AppLiveCaption/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/EnderDragonEP/AppLiveCaption/releases/tag/v1.0.0
