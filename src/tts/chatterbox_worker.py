"""Chatterbox-Turbo speech worker (runs in a separate Python, not in Jarvis's own).

Chatterbox needs torch 2.6 / Python 3.11, which live in their own environment
(config tts.chatterbox.python). Jarvis starts this script there through
src/tts/chatterbox.py and talks to it with JSON lines:

  stdin  {"op": "say", "id": 3, "text": "Hello.", "out": "C:/.../3.wav"}
         {"op": "cancel_all", "upto": 7}        # drop queued jobs with id <= 7
  stdout {"ready": true, "sr": 24000, "load_s": 18.2}
         {"id": 3, "ok": true, "path": "...", "ms": 8400, "dur": 1.6}
         {"id": 3, "ok": false, "error": "..."}  /  {"id": 3, "cancelled": true}

Library chatter (model loading, progress bars) goes to stderr so stdout stays clean.
The worker exits when stdin closes, so it never outlives Jarvis.

This file must not import anything from Jarvis: it runs under another interpreter.
"""
import json
import os
import queue
import sys
import threading
import time

PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr          # anything the libraries print goes to stderr


def send(msg: dict) -> None:
    PROTOCOL.write(json.dumps(msg) + "\n")
    PROTOCOL.flush()


def main() -> int:
    device = os.environ.get("JARVIS_TTS_DEVICE", "cpu")
    temperature = float(os.environ.get("JARVIS_TTS_TEMPERATURE", "0.8"))
    threads = int(os.environ.get("JARVIS_TTS_THREADS", "0"))

    jobs: "queue.Queue" = queue.Queue()
    cutoff = [0]                 # ids <= cutoff were cancelled

    def read_stdin():
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("op") == "cancel_all":
                cutoff[0] = max(cutoff[0], int(msg.get("upto", 0)))
            elif msg.get("op") == "say":
                jobs.put(msg)
        jobs.put(None)           # stdin closed: Jarvis is gone

    threading.Thread(target=read_stdin, daemon=True).start()

    t0 = time.time()
    try:
        import numpy as np
        import soundfile as sf
        import torch
        if threads > 0:
            torch.set_num_threads(threads)
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_pretrained(device)
    except Exception as e:
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1
    send({"ready": True, "sr": int(model.sr), "load_s": round(time.time() - t0, 1), "device": device})

    while True:
        job = jobs.get()
        if job is None:
            return 0
        jid = int(job["id"])
        if jid <= cutoff[0]:
            send({"id": jid, "cancelled": True})
            continue
        try:
            t = time.time()
            with torch.inference_mode():
                wav = model.generate(job["text"], temperature=temperature)
            audio = wav.squeeze(0).detach().cpu().numpy()
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            sf.write(job["out"], pcm, model.sr, subtype="PCM_16")
            send({"id": jid, "ok": True, "path": job["out"], "ms": round((time.time() - t) * 1000),
                  "dur": round(len(pcm) / model.sr, 2)})
        except Exception as e:
            send({"id": jid, "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    sys.exit(main())
