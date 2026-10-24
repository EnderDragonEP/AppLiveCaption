"""Console tools for testing capture and transcription without the GUI.

    python -m app.cli devices
    python -m app.cli apps
    python -m app.cli cuda [--download]
    python -m app.cli levels --mode system
    python -m app.cli levels --mode apps --apps chrome.exe,ms-teams.exe --mic
    python -m app.cli transcribe --mode system --model whisper-large-v3-turbo
"""

from __future__ import annotations

import argparse
import sys
import time
import wave

import numpy as np

from .asr.textconv import CHINESE_OFF, CHINESE_OUTPUTS, CHINESE_TRADITIONAL


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_devices(_args) -> None:
    from .audio.devices import list_input_devices, list_loopback_devices

    print("Microphones:")
    for d in list_input_devices():
        print(f"  {'*' if d.is_default else ' '} {d.name}  ({d.rate} Hz, {d.channels} ch)")
    print("System audio (loopback):")
    for d in list_loopback_devices():
        print(f"  {'*' if d.is_default else ' '} {d.name}  ({d.rate} Hz, {d.channels} ch)")


def cmd_apps(_args) -> None:
    from .audio.app_list import list_apps
    from .audio.process_loopback import process_loopback_supported

    print(f"Per-app capture supported: {process_loopback_supported()} (build {sys.getwindowsversion().build})")
    for a in list_apps():
        flags = ("playing" if a.playing else "session" if a.has_session else "") + (" window" if a.has_window else "")
        print(f"  {a.key:28} {a.name[:40]:40} pids={sorted(a.root_pids)} {flags.strip()}")


def _selection_from_args(args):
    from .audio.capture import CaptureSelection

    apps = [a.strip().lower() for a in (args.apps or "").split(",") if a.strip()]
    return CaptureSelection(
        mode=args.mode,
        mic_device=args.mic_device or "",
        system_device=args.output_device or "",
        apps=apps,
        app_names={a: a for a in apps},
        include_mic=args.mic,
    )


def _meter(db: float, width: int = 24) -> str:
    filled = int(max(0.0, min(1.0, (db + 60) / 60)) * width)
    return "#" * filled + "." * (width - filled)


def cmd_levels(args) -> None:
    from .audio.capture import CaptureManager

    def on_event(name, payload):
        print(f"[event] {name} {payload}")

    mgr = CaptureManager(on_event=on_event)
    mgr.start(_selection_from_args(args))
    recorded: list[np.ndarray] = []
    t0 = time.monotonic()
    try:
        while args.seconds <= 0 or time.monotonic() - t0 < args.seconds:
            time.sleep(0.5)
            audio, _ = mgr.output.pop_all(timeout=0)
            if args.record:
                recorded.append(audio)
            parts = [f"{src.label[:18]:>18} [{src.state.value[:4]}] {_meter(db)} {db:6.1f} dB" for src, db in mgr.levels()]
            mix = mgr.mixer.level_db if mgr.mixer else -120
            waiting = mgr.waiting_apps() if args.mode == "apps" else []
            line = " | ".join(parts) or "(no active sources)"
            print(f"{time.monotonic() - t0:6.1f}s  MIX {_meter(mix, 12)} {mix:6.1f} dB  ||  {line}"
                  + (f"  waiting: {', '.join(waiting)}" if waiting else ""), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()
    if args.record and recorded:
        audio = np.concatenate(recorded)
        with wave.open(args.record, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        print(f"Saved {audio.size / 16000:.1f} s of mixed audio to {args.record}")


def cmd_transcribe(args) -> None:
    from .asr.console import run_console_transcription

    run_console_transcription(args, _selection_from_args(args) if not args.wav else None)


def cmd_cuda(args) -> None:
    import threading

    from .asr import cuda_libs
    from .asr.cuda_setup import active_dirs
    from .asr.hardware import cuda_available, nvidia_gpu

    if args.download:
        cancel = threading.Event()
        last = [0.0]

        def on_progress(done, total, name):
            now = time.monotonic()
            if now - last[0] >= 1 or done == total:
                last[0] = now
                print(f"\r{name:24} {done / 2**20:7.0f} / {total / 2**20:.0f} MB", end="", flush=True)

        try:
            path = cuda_libs.download(on_progress, cancel)
        except KeyboardInterrupt:
            cancel.set()
            print("\nCancelled; run again to resume.")
            return
        except cuda_libs.DownloadError as exc:
            print(f"\nDownload failed ({exc.kind}): {exc}")
            sys.exit(1)
        print(f"\nInstalled in {path}")
    gpu = nvidia_gpu()
    print(f"NVIDIA GPU:      {gpu.name if gpu else 'none found'}")
    print(f"Downloaded libs: {cuda_libs.installed_dir() or 'not installed'} ({cuda_libs.BUNDLE_ID})")
    print(f"CUDA usable:     {cuda_available()}  {active_dirs()}")


def main(argv: list[str] | None = None) -> None:
    sys.modules.setdefault("torch", None)  # unused; see app/main.py
    _utf8_console()
    from .logging_setup import setup_logging

    setup_logging(console=True)
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list microphones and loopback devices").set_defaults(func=cmd_devices)
    sub.add_parser("apps", help="list apps that can be captured").set_defaults(func=cmd_apps)
    cu = sub.add_parser("cuda", help="show GPU support status")
    cu.add_argument("--download", action="store_true", help="download NVIDIA's CUDA libraries for the GPU")
    cu.set_defaults(func=cmd_cuda)

    def add_capture_args(sp):
        sp.add_argument("--mode", choices=["mic", "system", "apps"], default="system")
        sp.add_argument("--apps", help="comma-separated executable names, e.g. chrome.exe,ms-teams.exe")
        sp.add_argument("--mic", action="store_true", help="add the microphone to the app/system mix")
        sp.add_argument("--mic-device", help="microphone name (default device if omitted)")
        sp.add_argument("--output-device", help="loopback device name (default device if omitted)")
        sp.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")

    lv = sub.add_parser("levels", help="print the input level of each source")
    add_capture_args(lv)
    lv.add_argument("--record", help="save the mixed 16 kHz stream to this WAV file")
    lv.set_defaults(func=cmd_levels)

    tr = sub.add_parser("transcribe", help="live transcription in the console")
    add_capture_args(tr)
    tr.add_argument("--model", default="whisper-large-v3-turbo", help="catalog id of a downloaded model")
    tr.add_argument("--language", default="auto")
    tr.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    tr.add_argument("--wav", help="transcribe a 16-bit WAV file in simulated real time instead of live audio")
    tr.add_argument("--chinese", choices=CHINESE_OUTPUTS, default=CHINESE_TRADITIONAL,
                    help="convert Chinese captions to Traditional or Simplified Chinese, or leave them (off)")
    # The option's name before --chinese, still accepted.
    tr.add_argument("--no-opencc", dest="chinese", action="store_const", const=CHINESE_OFF, help=argparse.SUPPRESS)
    tr.set_defaults(func=cmd_transcribe)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
