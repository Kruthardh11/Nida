# core/voice.py
# ─────────────────────────────────────────────
# Nida — Piper TTS Voice Output
# ─────────────────────────────────────────────

import io
import logging
import platform
import subprocess
import tempfile
import threading
from pathlib import Path

from config.settings import (
    PIPER_EXECUTABLE, PIPER_VOICE_MODEL,
    PIPER_VOICE_CONFIG, PIPER_SAMPLE_RATE
)

logger = logging.getLogger("nida.voice")
OS = platform.system()


class Voice:
    """
    Text-to-speech via Piper (ONNX runtime).

    Two modes:
    - speak()       : async (non-blocking) — fires and forgets
    - speak_sync()  : blocking — waits until audio finishes

    Falls back to print() if Piper is not installed.
    """

    def __init__(self):
        self._piper_available = self._check_piper()
        self._current_proc: subprocess.Popen | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def speak(self, text: str):
        """Non-blocking TTS. Runs in a background thread."""
        if not text:
            return
        t = threading.Thread(target=self._say, args=(text,), daemon=True)
        t.start()

    def speak_sync(self, text: str):
        """Blocking TTS. Returns when audio is done."""
        if not text:
            return
        self._say(text)

    def stop(self):
        """Kill any currently playing audio."""
        if self._current_proc and self._current_proc.poll() is None:
            self._current_proc.terminate()

    # ── Private ────────────────────────────────────────────────────────────

    def _say(self, text: str):
        # Print the exact text with formatting intact
        print(f"🔊  Nida: {text}")

        if not self._piper_available:
            return                          # fallback already printed

        # Fast strip of common markdown (asterisks, underscores, hashes, backticks)
        # so Piper doesn't say "asterisk" out loud.
        import re
        spoken_text = re.sub(r'[*_#`]', '', text)

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name

            # Piper: echo text | piper --model <m> --output_file <wav>
            cmd = [
                PIPER_EXECUTABLE,
                "--model",       PIPER_VOICE_MODEL,
                "--config",      PIPER_VOICE_CONFIG,
                "--output_file", wav_path,
            ]

            piper_proc = subprocess.run(
                cmd,
                input=spoken_text,
                text=True,
                capture_output=True,
                timeout=10
            )

            if piper_proc.returncode != 0:
                logger.error(f"Piper error: {piper_proc.stderr}")
                return

            # Play the wav file
            self._play_wav(wav_path)

        except subprocess.TimeoutExpired:
            logger.error("Piper TTS timed out")
        except FileNotFoundError:
            logger.error(
                f"Piper not found at '{PIPER_EXECUTABLE}'. "
                "Download from https://github.com/rhasspy/piper/releases"
            )
        except Exception as e:
            logger.error(f"Voice error: {e}")
        finally:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _play_wav(self, wav_path: str):
        """Cross-platform WAV playback."""
        if OS == "Windows":
            cmd = ["powershell", "-c", f"(New-Object Media.SoundPlayer '{wav_path}').PlaySync()"]
        elif OS == "Darwin":
            cmd = ["afplay", wav_path]
        else:
            # Linux — try multiple players
            for player in ["aplay", "paplay", "ffplay -nodisp -autoexit"]:
                if subprocess.run(["which", player.split()[0]],
                                  capture_output=True).returncode == 0:
                    cmd = player.split() + [wav_path]
                    break
            else:
                logger.warning("No audio player found. Install aplay or paplay.")
                return

        self._current_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
        self._current_proc.wait()

    def _check_piper(self) -> bool:
        result = subprocess.run(
            [PIPER_EXECUTABLE, "--help"],
            capture_output=True
        )
        available = result.returncode == 0
        if available:
            logger.info("Piper TTS found.")
        else:
            logger.warning(
                "Piper not found — voice output will be text-only. "
                "Download: https://github.com/rhasspy/piper/releases"
            )
        return available
