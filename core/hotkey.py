# core/hotkey.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Global Hotkey Listener  (Ctrl+Alt+N)
#
# WHY A HOTKEY:
#   When Nida is sleeping, the mic is off. The user needs a way to wake her
#   that doesn't require speaking (maybe the room is loud, or TTS isn't
#   running). A global hotkey is the fastest non-voice trigger.
#
# HOW IT WORKS:
#   pynput's GlobalHotkeys runs in a background thread. When Ctrl+Alt+N is
#   pressed, it calls a callback that sets wake_requested=True on the shared
#   NidaState. The listen_node polls this flag on its next iteration.
#
# WHY NOT KEYBOARD INTERRUPT / SIGNAL:
#   Signals are process-wide and interfere with PyAudio's C callbacks.
#   pynput operates at the OS keyboard hook level — no interference.
#
# DEPENDENCY:
#   pip install pynput
# ─────────────────────────────────────────────────────────────────────────────

import logging
import threading
from typing import Callable

logger = logging.getLogger("nida.hotkey")

try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    logger.warning("pynput not installed — Ctrl+Alt+N hotkey disabled. pip install pynput")


class HotkeyListener:
    """
    Runs a background thread that listens for:
      Ctrl+Alt+N  — wake Nida
      Ctrl+Alt+S  — put Nida to sleep
    """

    WAKE_COMBO  = "<ctrl>+<alt>+n"
    SLEEP_COMBO = "<ctrl>+<alt>+s"

    def __init__(self, on_wake: Callable[[], None], on_sleep: Callable[[], None] = None):
        self._on_wake   = on_wake
        self._on_sleep  = on_sleep
        self._listener  = None
        self._thread    = None

    def start(self):
        if not PYNPUT_AVAILABLE:
            logger.warning("Hotkey disabled (pynput missing).")
            return

        hotkeys = {
            self.WAKE_COMBO: lambda: (
                logger.info(f"Hotkey {self.WAKE_COMBO} — requesting wake."),
                self._on_wake()
            ),
        }
        if self._on_sleep:
            hotkeys[self.SLEEP_COMBO] = lambda: (
                logger.info(f"Hotkey {self.SLEEP_COMBO} — requesting sleep."),
                self._on_sleep()
            )

        self._listener = keyboard.GlobalHotKeys(hotkeys)
        self._thread = threading.Thread(target=self._listener.start, daemon=True)
        self._thread.start()
        logger.info(f"Hotkeys registered: wake={self.WAKE_COMBO}, sleep={self.SLEEP_COMBO}")

    def stop(self):
        if self._listener:
            self._listener.stop()
            logger.info("Hotkey listener stopped.")
