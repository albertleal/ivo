"""Voice transcription — pluggable interface.

Ships with a `NullTranscriber` (returns a friendly stub message). Real
transcribers (Whisper, OpenAI Audio, Gemini, etc.) plug in by subclassing
`Transcriber`. Wiring them up is intentionally out of scope for the scaffold.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Transcriber(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: Path) -> str:
        """Return the text transcript of the audio file."""


class NullTranscriber(Transcriber):
    """No-op transcriber used by default."""

    async def transcribe(self, audio_path: Path) -> str:
        return (
            "[voice transcription is not configured — install a transcriber "
            "plugin and wire it in `bot/transcribe.py`]"
        )
