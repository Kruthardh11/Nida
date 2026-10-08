# core/listener.py
# ─────────────────────────────────────────────
# Nida — Audio Listener + Whisper Transcriber
# ─────────────────────────────────────────────

import io
import time
import struct
import logging
import threading
import numpy as np

import pyaudio
from faster_whisper import WhisperModel

from config.settings import (
    WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE, WHISPER_LANGUAGE,
    AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, AUDIO_CHUNK_SIZE,
    SILENCE_THRESHOLD, SILENCE_DURATION, MAX_RECORD_SECONDS
)

logger = logging.getLogger("nida.listener")


class Listener:
    """
    Handles microphone capture and speech-to-text transcription.

    Flow:
      1. wait_for_speech()  — blocks until RMS crosses SILENCE_THRESHOLD
      2. record_until_silence() — captures audio until user stops speaking
      3. transcribe()       — runs faster-whisper on the captured bytes
    """

    def __init__(self):
        logger.info(f"Loading Whisper model: {WHISPER_MODEL} [{WHISPER_DEVICE}/{WHISPER_COMPUTE}]")
        self.model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE
        )
        self.audio = pyaudio.PyAudio()
        self._interrupt_event = threading.Event()
        logger.info("Listener ready.")

    # ── Public API ─────────────────────────────────────────────────────────

    def listen(self) -> str:
        """
        Full pipeline: wait → record → transcribe.
        Returns the transcribed string, stripped and lowercased.
        """
        print("\n🎙️  Listening... (speak now)")
        frames = self._record()
        if not frames:
            return ""
        print("🔄  Transcribing...")
        text = self._transcribe(frames)
        logger.info(f"Heard: '{text}'")
        return text

    def cleanup(self):
        self.audio.terminate()

    def interrupt(self):
        """Immediately aborts any running recording loop."""
        self._interrupt_event.set()
        logger.debug("Listener interrupted.")

    def pause(self):
        """Signal the listener it should not open new streams (sleep mode)."""
        self._paused = True
        logger.info("Listener paused.")

    def resume(self):
        """Signal the listener it can open streams again (wake mode)."""
        self._paused = False
        logger.info("Listener resumed.")

    def listen_for_wakeword(self, wake_phrases: list[str]) -> bool:
        """
        Lightweight wake-word detector used while Nida is sleeping.
        Keeps a raw RMS loop running — only fires Whisper when sustained
        speech is detected. Returns True if a wake phrase was spoken.
        CPU cost: ~0% when silent, brief Whisper spike on detected speech.
        """
        stream = self._open_stream()
        frames = []
        silent_chunks = 0
        max_silent = int(1.0 * AUDIO_SAMPLE_RATE / AUDIO_CHUNK_SIZE)  # 1s silence
        speaking = False

        try:
            # Poll for up to MAX_RECORD_SECONDS — then give up and return False
            max_chunks = int(MAX_RECORD_SECONDS * AUDIO_SAMPLE_RATE / AUDIO_CHUNK_SIZE)
            for _ in range(max_chunks):
                data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
                rms = self._rms(data)

                if not speaking:
                    if rms > SILENCE_THRESHOLD:
                        speaking = True
                        frames.append(data)
                else:
                    frames.append(data)
                    if rms < SILENCE_THRESHOLD:
                        silent_chunks += 1
                        if silent_chunks >= max_silent:
                            break
                    else:
                        silent_chunks = 0
        finally:
            stream.stop_stream()
            stream.close()

        if not frames:
            return False

        # Only transcribe if we caught something — keep it fast
        text = self._transcribe(frames)
        logger.debug(f"Wake-word check heard: '{text}'")
        return any(phrase in text for phrase in wake_phrases)

    # ── Private ────────────────────────────────────────────────────────────

    def _open_stream(self):
        return self.audio.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_SAMPLE_RATE,
            input=True,
            frames_per_buffer=AUDIO_CHUNK_SIZE
        )

    def _rms(self, data: bytes) -> float:
        """Root Mean Square of audio chunk — proxy for volume."""
        count = len(data) // 2
        shorts = struct.unpack(f"{count}h", data)
        if count == 0:
            return 0.0
        sum_sq = sum(s * s for s in shorts)
        return (sum_sq / count) ** 0.5

    def _record(self) -> list[bytes]:
        """
        Waits for speech to start, then records until silence.
        Returns list of raw audio byte chunks.
        """
        stream = self._open_stream()
        frames = []
        silent_chunks = 0
        max_silent = int(SILENCE_DURATION * AUDIO_SAMPLE_RATE / AUDIO_CHUNK_SIZE)
        max_chunks = int(MAX_RECORD_SECONDS * AUDIO_SAMPLE_RATE / AUDIO_CHUNK_SIZE)
        speaking = False

        try:
            for _ in range(max_chunks):
                if self._interrupt_event.is_set():
                    self._interrupt_event.clear()
                    logger.debug("Recording interrupted mid-loop.")
                    return []

                data = stream.read(AUDIO_CHUNK_SIZE, exception_on_overflow=False)
                rms = self._rms(data)

                if not speaking:
                    if rms > SILENCE_THRESHOLD:
                        speaking = True
                        frames.append(data)
                else:
                    frames.append(data)
                    if rms < SILENCE_THRESHOLD:
                        silent_chunks += 1
                        if silent_chunks >= max_silent:
                            break
                    else:
                        silent_chunks = 0
        finally:
            stream.stop_stream()
            stream.close()

        return frames if speaking else []

    def _transcribe(self, frames: list[bytes]) -> str:
        """Convert raw PCM frames → text via faster-whisper."""
        raw = b"".join(frames)
        # faster-whisper wants float32 numpy array, normalised to [-1, 1]
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        segments, _ = self.model.transcribe(
            audio_np,
            language=WHISPER_LANGUAGE,
            beam_size=5,
            vad_filter=True,           # built-in voice activity detection
            vad_parameters=dict(min_silence_duration_ms=500),
            initial_prompt="assistant, command, desktop, Discord, Spotify, Brave, Chrome, VS Code, volume, brightness, system, roadmap, checklist."
        )
        return " ".join(seg.text for seg in segments).strip().lower()
