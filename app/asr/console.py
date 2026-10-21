"""Live transcription in the console (milestone 2 test harness)."""

from __future__ import annotations

import os
import sys
import time
import wave

import numpy as np

os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _load_wav(path: str) -> np.ndarray:
    from ..audio.resample import StreamResampler

    with wave.open(path) as wf:
        rate, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise SystemExit("only 16-bit PCM WAV files are supported")
    x = (np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0).reshape(-1, ch)
    return StreamResampler(rate).process(x, rate)


def run_console_transcription(args, selection) -> None:
    from ..models.catalog import get_model
    from ..models.storage import is_downloaded, model_path
    from .factory import create_backend
    from .hardware import gpu_info, realtime_risk, resolve_device
    from .streaming import StreamingTranscriber

    info = get_model(args.model)
    if info is None:
        raise SystemExit(f"unknown model id {args.model!r}")
    if not is_downloaded(info):
        raise SystemExit(f"{info.name} is not downloaded (expected in {model_path(info)})")
    device = resolve_device(args.device)
    gpu = gpu_info() if device == "cuda" else None
    print(f"Model: {info.name}  device: {device}{' (' + gpu.name + ')' if gpu else ''}")
    risk = realtime_risk(info.tier, device, gpu)
    if risk:
        print(f"Warning: {info.name} will probably be too slow for real time here ({risk}); try a smaller model.")

    backend = create_backend(info)
    t0 = time.monotonic()
    backend.load(str(model_path(info)), device)
    print(f"Loaded in {time.monotonic() - t0:.1f} s")
    language = None if args.language in ("", "auto") else args.language
    language = language or info.default_language
    st = StreamingTranscriber(backend, language=language, convert_chinese=not args.no_opencc,
                              base_prompt=info.initial_prompt)

    width = 100

    def show(events):
        for ev in events:
            if ev.kind == "partial":
                line = f"  … {ev.text}"[-width:]
                sys.stdout.write("\r" + line.ljust(width) + f" [{ev.latency:.1f}s]")
            elif ev.kind == "final":
                sys.stdout.write("\r" + " " * (width + 8) + "\r")
                if ev.text:
                    sys.stdout.write(f"[{ev.start:7.1f}-{ev.end:7.1f}] {ev.text}  "
                                     f"({ev.language}, latency {ev.latency:.1f}s, decode {ev.decode_time:.2f}s)\n")
            elif ev.kind == "dropped":
                sys.stdout.write(f"\n(fell behind, dropped {ev.end:.1f} s of audio)\n")
            sys.stdout.flush()

    if args.wav:
        audio = _load_wav(args.wav)
        chunk = 1600
        start = time.monotonic()
        for i in range(0, audio.size, chunk):
            # Feed in real time, as the capture thread would.
            target = start + (i + chunk) / 16000
            pause = target - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            show(st.process(audio[i:i + chunk], time.monotonic()))
        show(st.process(np.zeros(16000, np.float32), time.monotonic()))
        show(st.flush())
        return

    from ..audio.capture import CaptureManager

    mgr = CaptureManager(on_event=lambda name, payload: print(f"\n[event] {name} {payload}"))
    mgr.start(selection)
    print("Listening… (Ctrl+C to stop)")
    t_start = time.monotonic()
    try:
        while args.seconds <= 0 or time.monotonic() - t_start < args.seconds:
            audio, t_end = mgr.output.pop_all(timeout=0.2)
            show(st.process(audio, t_end))
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()
        show(st.flush())
        backend.unload()
