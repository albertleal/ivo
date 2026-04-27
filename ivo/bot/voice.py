"""Voice in/out using whisper-cli (STT) and kokoro-onnx (local TTS).

STT is still subprocess-driven (whisper.cpp); TTS is in-process via the
``kokoro_onnx`` Python package so synthesis stays fully local — no audio
or text leaves the host.

Required on PATH:
  - whisper-cli  (whisper.cpp)  — transcribe OGG/WAV → text
  - ffmpeg                      — audio format conversion

Required model files (override paths via env):
  - WHISPER_MODEL  → whisper.cpp ggml model
  - KOKORO_MODEL   → kokoro-v1.0.onnx
  - KOKORO_VOICES  → voices-v1.0.bin
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("bot.voice")

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Vendored model artefacts under <repo>/data/models/ (gitignored).
# Legacy fallback: <repo>/models/ (older layout).
_DATA_MODELS = _REPO_ROOT / "data" / "models"
_LEGACY_MODELS = _REPO_ROOT / "models"

WHISPER_CLI = os.getenv("WHISPER_CLI", "whisper-cli")
WHISPER_MODEL = os.getenv(
    "WHISPER_MODEL",
    str(_DATA_MODELS / "ggml-base.bin")
    if (_DATA_MODELS / "ggml-base.bin").exists()
    else str(_LEGACY_MODELS / "ggml-base.bin"),
)

KOKORO_MODEL = os.getenv("KOKORO_MODEL", str(_DATA_MODELS / "kokoro" / "kokoro-v1.0.onnx"))
KOKORO_VOICES = os.getenv("KOKORO_VOICES", str(_DATA_MODELS / "kokoro" / "voices-v1.0.bin"))
# Bilingual: auto-detect ES vs EN per reply and pick voice + lang.
# Override per-language via env. "auto" lang means detect on each call.
TTS_VOICE_ES = os.getenv("TTS_VOICE_ES", "em_alex")
TTS_VOICE_EN = os.getenv("TTS_VOICE_EN", "am_michael")
TTS_LANG = os.getenv("TTS_LANG", "auto")  # 'auto' | 'es' | 'en-us' | 'en-gb'
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
TTS_MAX_CHARS = 3000

# Strip these so TTS doesn't read them aloud.
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F600-\U0001F64F]+"
)
_ATTACH_BLOCK_RE = re.compile(
    r"<attachments>.*?</attachments>\s*",
    re.IGNORECASE | re.DOTALL,
)

# Lazy-loaded Kokoro instance (model + voices is ~340 MB; load once).
_kokoro: Any = None
_kokoro_lock = asyncio.Lock()


def _pick_voice_lang(text: str) -> tuple[str, str]:
    """Choose (voice, lang) for ``text``.

    If ``TTS_LANG`` is fixed (es / en-us / en-gb), honour it. Otherwise
    auto-detect: anything langdetect classifies as Spanish goes through the
    Spanish voice; everything else falls back to the English voice.
    """
    if TTS_LANG and TTS_LANG.lower() != "auto":
        voice = TTS_VOICE_ES if TTS_LANG.lower().startswith("es") else TTS_VOICE_EN
        return voice, TTS_LANG
    # Cheap heuristic first — Spanish-only diacritics catch most replies.
    if re.search(r"[áéíóúñÁÉÍÓÚÑ¿¡]", text):
        return TTS_VOICE_ES, "es"
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0
        code = detect(text)
    except Exception:
        code = "en"
    if code == "es":
        return TTS_VOICE_ES, "es"
    return TTS_VOICE_EN, "en-us"


def voice_available() -> tuple[bool, list[str]]:
    """Return (ok, missing) for the voice toolchain."""
    missing: list[str] = []
    for binary in (WHISPER_CLI, "ffmpeg"):
        if shutil.which(binary) is None:
            missing.append(binary)
    if not Path(WHISPER_MODEL).exists():
        missing.append(f"whisper-model:{WHISPER_MODEL}")
    if not Path(KOKORO_MODEL).exists():
        missing.append(f"kokoro-model:{KOKORO_MODEL}")
    if not Path(KOKORO_VOICES).exists():
        missing.append(f"kokoro-voices:{KOKORO_VOICES}")
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        missing.append("python:kokoro_onnx")
    return (not missing, missing)


# ── STT ──────────────────────────────────────────────────────────────────────


async def _ogg_to_wav(ogg_path: str) -> str:
    wav_path = ogg_path.rsplit(".", 1)[0] + ".wav"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", ogg_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        wav_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')}")
    return wav_path


async def transcribe_ogg(ogg_path: str) -> str:
    """Transcribe an OGG/OGA voice file to text (returns '' on blank audio)."""
    wav_path: str | None = None
    try:
        wav_path = await _ogg_to_wav(ogg_path)
        proc = await asyncio.create_subprocess_exec(
            WHISPER_CLI,
            "-m", WHISPER_MODEL,
            "-f", wav_path,
            "--no-prints", "--no-timestamps",
            "-l", "auto",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli failed: {stderr.decode(errors='replace')}")
        text = stdout.decode(errors="replace").strip()
        if text == "[BLANK_AUDIO]":
            return ""
        return text
    finally:
        if wav_path and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except OSError:
                pass


# ── TTS ──────────────────────────────────────────────────────────────────────


def _clean_for_tts(text: str) -> str:
    # Strip the attachment protocol block — TTS must never read paths aloud.
    clean = _ATTACH_BLOCK_RE.sub("", text)
    clean = clean.replace("**", "").replace("*", "").replace("`", "")
    clean = _EMOJI_RE.sub("", clean)
    keep: list[str] = []
    for line in clean.split("\n"):
        s = line.lstrip()
        if s.startswith(("●", "│", "└", "$ ", "./", "/Users/", "cat ", "echo ")):
            continue
        keep.append(line)
    clean = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
    return clean[:TTS_MAX_CHARS]


async def _get_kokoro() -> Any:
    """Lazy-load the Kokoro model on first use (heavy ~340 MB load)."""
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    async with _kokoro_lock:
        if _kokoro is not None:
            return _kokoro
        from kokoro_onnx import Kokoro

        def _load() -> Any:
            log.info("loading kokoro model from %s", KOKORO_MODEL)
            return Kokoro(KOKORO_MODEL, KOKORO_VOICES)

        _kokoro = await asyncio.to_thread(_load)
        log.info("kokoro ready (lang=%s voice_es=%s voice_en=%s)", TTS_LANG, TTS_VOICE_ES, TTS_VOICE_EN)
    return _kokoro


async def synthesize(text: str) -> str:
    """Synthesize ``text`` to OGG/Opus using Kokoro. Caller deletes the file."""
    clean = _clean_for_tts(text)
    if not clean:
        raise ValueError("nothing to speak after cleaning text")

    k = await _get_kokoro()
    voice, lang = _pick_voice_lang(clean)
    log.debug("tts: voice=%s lang=%s chars=%d", voice, lang, len(clean))

    def _create() -> tuple[Any, int]:
        return k.create(clean, voice=voice, speed=TTS_SPEED, lang=lang)

    samples, sample_rate = await asyncio.to_thread(_create)

    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.close()

    def _write_wav() -> None:
        import soundfile as sf
        sf.write(wav.name, samples, sample_rate)

    await asyncio.to_thread(_write_wav)

    ogg = wav.name.rsplit(".", 1)[0] + ".ogg"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", wav.name,
        "-c:a", "libopus", "-b:a", "64k",
        ogg,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    try:
        os.unlink(wav.name)
    except OSError:
        pass
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ogg conversion failed: {stderr.decode(errors='replace')}")
    return ogg
