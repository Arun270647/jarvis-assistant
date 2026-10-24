import json
import re
import sys
import time
import datetime

import numpy as np

from src.core import config_store, paths

CORRECTIONS_LOG = "stt_corrections.jsonl"      # in the cache folder
WARMUP_CLIP = "benchmark_stt_clip.wav"         # in the cache folder (scripts/benchmark_stt.py records it)


def load_config():
    return config_store.load_config()


def _load_profile():
    return config_store.load_profile()


def _build_initial_prompt(profile: dict, name: str = "Jarvis") -> str:
    """Build whisper initial_prompt from user_profile.json vocabulary and the assistant's name.

    Biases whisper decoding toward project-specific proper nouns.
    """
    words = {"Claude", name}

    for key, app_cfg in profile.get("apps", {}).items():
        words.add(key)
        for alias in app_cfg.get("aliases", []):
            words.add(alias)

    for key, proj_cfg in profile.get("projects", {}).items():
        words.add(key)
        for alias in proj_cfg.get("aliases", []):
            words.add(alias)

    prompt = f"{name} voice assistant. " + ", ".join(sorted(words)) + "."
    return prompt


def _build_correction_map(profile: dict) -> dict:
    """Build fuzzy correction map from user_profile.json.

    Maps common STT mis-transcriptions to canonical forms.
    Keys are lowercase variants, values are the correct form.
    """
    corrections = {}

    # Claude variants
    for variant in ["cloud", "clod", "claud", "clawed", "cloth", "claw", "klod", "klaude"]:
        corrections[variant] = "Claude"

    # Build from profile: project keys and aliases
    for key, proj_cfg in profile.get("projects", {}).items():
        canonical = key
        all_forms = [key] + proj_cfg.get("aliases", [])

        for form in all_forms:
            form_lower = form.lower()
            # Generate common mis-hearing variants
            # Swap similar-sounding consonants, drop/add letters
            _add_phonetic_variants(form_lower, canonical, corrections)

    # App names
    for key in profile.get("apps", {}):
        corrections.setdefault(key.lower(), key)

    return corrections


def _add_phonetic_variants(word_lower: str, canonical: str, corrections: dict):
    """Add the word itself and some phonetic near-miss variants."""
    corrections[word_lower] = canonical

    # Common substitutions for proper nouns
    subs = [
        ("j", "x"), ("j", "g"), ("j", "zh"),
        ("v", "b"), ("v", "f"),
        ("s", "z"), ("s", "c"),
        ("ar", "a"), ("is", "as"), ("is", "us"),
    ]
    for old, new in subs:
        if old in word_lower:
            variant = word_lower.replace(old, new, 1)
            corrections.setdefault(variant, canonical)


class Transcriber:
    """Wrapper for faster-whisper STT and Silero VAD. The Whisper model stays loaded.

    stt.model_path is a model name ("small", downloaded into models/whisper on first use) or a local
    CTranslate2 model folder. Speech detection is Silero VAD through ONNX Runtime (src/stt/vad.py).
    """

    def __init__(self):
        cfg = load_config()
        profile = _load_profile()
        self.stt_cfg = cfg["stt"]
        self.beam_size = self.stt_cfg.get("beam_size", 1)
        self._model_path, self._download_root = paths.whisper_model(self.stt_cfg["model_path"])
        self._model = None
        from src.core.config_store import assistant_name
        self._initial_prompt = _build_initial_prompt(profile, assistant_name(cfg))
        self._correction_map = _build_correction_map(profile)

        print(f"[STT] Initial prompt: {self._initial_prompt}")
        print(f"[STT] Correction map: {len(self._correction_map)} entries")
        print(f"[STT] Beam size: {self.beam_size}")

    def load(self):
        """Load whisper model and run warmup. Call once at pipeline init."""
        from faster_whisper import WhisperModel
        self._model = WhisperModel(
            self._model_path,
            device=self.stt_cfg["device"],
            compute_type=self.stt_cfg["compute_type"],
            download_root=self._download_root,
        )

        # Warmup with real speech so CTranslate2 compiles the exact CUDA
        # kernels used in live transcription. Silent audio doesn't exercise
        # the decoder properly and leaves the first real call slow.
        warmup_audio = self._get_warmup_audio()
        print(f"[STT] Running warmup transcription ({len(warmup_audio)/16000:.1f}s of audio, beam={self.beam_size})...")
        t0 = time.time()
        segments, _ = self._model.transcribe(
            warmup_audio,
            beam_size=self.beam_size,
            language="en",
            initial_prompt=self._initial_prompt,
        )
        warmup_text = " ".join(s.text.strip() for s in segments)
        warmup_ms = (time.time() - t0) * 1000
        print(f"[STT] Warmup done ({warmup_ms:.0f}ms): \"{warmup_text}\"")

    def _get_warmup_audio(self) -> np.ndarray:
        """Get a real speech clip for warmup. Synthesize one if no file exists."""
        import wave as wave_mod

        # Try existing benchmark clip
        clip_path = paths.cache_dir() / WARMUP_CLIP
        if clip_path.exists():
            with wave_mod.open(str(clip_path), "rb") as wf:
                audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            # Use first 2s to keep warmup fast
            audio = audio[:32000]
            return audio.astype(np.float32) / 32768.0

        # Synthesize a phrase with the voice engine if one can start
        try:
            from src.tts import create_tts_engine
            tts = create_tts_engine()
            audio = tts.synthesize("Open notepad and list files in jarvis")
            # Resample from the engine's rate (22050 or 24000 Hz) to 16kHz
            ratio = 16000 / tts.sample_rate
            indices = np.round(np.arange(0, len(audio), 1 / ratio)).astype(int)
            indices = indices[indices < len(audio)]
            audio_16k = audio[indices]
            return audio_16k.astype(np.float32) / 32768.0
        except Exception:
            pass

        # Fallback: 1s of silence (better than nothing)
        return np.zeros(16000, dtype=np.float32)

    def _load_vad(self):
        """Silero VAD through ONNX Runtime (src/stt/vad.py; the model file is in the models folder)."""
        from src.stt.vad import SileroVAD
        return SileroVAD(), None

    def transcribe_audio(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe audio with vocabulary bias and fuzzy correction."""
        if self._model is None:
            raise RuntimeError("Whisper not loaded. Call load() at pipeline init.")

        audio_float = audio.astype(np.float32) / 32768.0
        segments, info = self._model.transcribe(
            audio_float,
            beam_size=self.beam_size,
            language="en",
            initial_prompt=self._initial_prompt,
        )
        text_parts = [seg.text.strip() for seg in segments]
        raw_text = " ".join(text_parts)

        corrected_text = self._apply_corrections(raw_text)
        return corrected_text

    def _apply_corrections(self, text: str) -> str:
        """Apply fuzzy corrections to known vocabulary.

        Splits text into words, checks each against the correction map,
        and logs every correction made.
        """
        words = text.split()
        corrected = []
        corrections_made = []

        for word in words:
            # Strip punctuation for matching, preserve it for output
            clean = re.sub(r'[^a-zA-Z]', '', word).lower()
            if clean in self._correction_map:
                replacement = self._correction_map[clean]
                if clean != replacement.lower():
                    corrections_made.append((word, replacement))
                    # Preserve original casing pattern if it was already correct
                    corrected.append(replacement)
                else:
                    corrected.append(word)
            else:
                corrected.append(word)

        result = " ".join(corrected)

        if corrections_made:
            print(f"[STT CORRECT] {corrections_made}")
            self._log_corrections(text, result, corrections_made)

        return result

    def _log_corrections(self, original: str, corrected: str, pairs: list):
        """Log correction pairs to stt_corrections.jsonl in the cache folder (a training dataset)."""
        log = paths.cache_dir() / CORRECTIONS_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "original": original,
            "corrected": corrected,
            "pairs": [[p[0], p[1]] for p in pairs],
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def transcribe_audio(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe a 16kHz mono int16 numpy array using faster-whisper."""
    from faster_whisper import WhisperModel

    cfg = load_config()
    stt_cfg = cfg["stt"]
    model_path, download_root = paths.whisper_model(stt_cfg["model_path"])

    print(f"Loading faster-whisper {model_path} on {stt_cfg['device']}...")
    model = WhisperModel(
        model_path,
        device=stt_cfg["device"],
        compute_type=stt_cfg["compute_type"],
        download_root=download_root,
    )

    audio_float = audio.astype(np.float32) / 32768.0
    segments, info = model.transcribe(audio_float, beam_size=5, language="en")
    text_parts = []
    for segment in segments:
        text_parts.append(segment.text.strip())
        print(f"  [{segment.start:.1f}s - {segment.end:.1f}s] {segment.text.strip()}")

    full_text = " ".join(text_parts)
    print(f"\nFull transcription: {full_text}")
    print(f"Language: {info.language} (prob: {info.language_probability:.2f})")

    del model
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    return full_text


def transcribe_file(wav_path: str) -> str:
    """Transcribe a WAV file."""
    import wave
    with wave.open(wav_path, "rb") as wf:
        assert wf.getnchannels() == 1, f"Expected mono, got {wf.getnchannels()} channels"
        assert wf.getsampwidth() == 2, f"Expected 16-bit, got {wf.getsampwidth() * 8}-bit"
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    audio = np.frombuffer(frames, dtype=np.int16)
    print(f"Loaded {wav_path}: {len(audio)} samples at {sr}Hz ({len(audio)/sr:.1f}s)")
    return transcribe_audio(audio, sr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.stt.transcriber <wav_file>")
        sys.exit(1)
    transcribe_file(sys.argv[1])
