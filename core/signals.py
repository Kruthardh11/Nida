# core/signals.py
# ─────────────────────────────────────────────────────────────────────────────
# Global signals for hotkeys and UI events.
# We use this instead of NidaState because LangGraph creates copies of its
# state object at each step, making it impossible to mutate state mid-execution
# from a separate thread.
# ─────────────────────────────────────────────────────────────────────────────

class AppSignals:
    sleep_requested: bool = False
    wake_requested: bool = False

signals = AppSignals()
