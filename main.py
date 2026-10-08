# main.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Entry Point  (Phase 2)
#
# What changed from Phase 1:
#   - Executor is gone from main.py. Replaced by ToolRegistry + ShellTool.
#   - build_graph() receives registry instead of executor.
#   - Adding a new tool = register it here + update the system prompt.
#
# HOW graph.invoke() WORKS:
#   invoke() runs the graph from entry_point ("listen") until it hits END
#   or raises an exception. Since our graph is a loop (respond → listen),
#   it will run indefinitely until after_listen returns "END".
#
#   We call invoke() with the SAME state object on each restart so state
#   (turn_count, last_interaction_time, etc.) persists across iterations.
# ─────────────────────────────────────────────────────────────────────────────

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import LOG_LEVEL, LOG_FILE, LOGS_DIR
from core.listener  import Listener
from core.brain     import Brain
from core.voice     import Voice
from core.hotkey    import HotkeyListener
from tools.registry import ToolRegistry
from tools.executor import ShellTool
from tools.datetime_tool import DateTimeTool
from tools.volume import VolumeTool
from tools.brightness import BrightnessTool
from tools.system_control import SystemTool
from tools.game import GameTool
from tools.search import SearchTool
from tools.notes import NotesTool
from graph.builder  import build_graph
from graph.state    import NidaState


def setup_logging():
    LOGS_DIR.mkdir(exist_ok=True)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8")
        ]
    )


def main():
    setup_logging()
    logger = logging.getLogger("nida.main")
    logger.info("Starting Nida Phase 2...")

    # ── Initialise components ─────────────────────────────────────────────────
    try:
        listener = Listener()
        brain    = Brain()
        voice    = Voice()
    except RuntimeError as e:
        print(f"\n❌  Setup error: {e}\n")
        sys.exit(1)

    # ── Build tool registry ───────────────────────────────────────────────────
    #
    # Every tool Nida can use is registered here. To add a new tool:
    #   1. Create a BaseTool subclass (e.g. tools/notes.py → NotesTool)
    #   2. Register it: registry.register(NotesTool())
    #   3. Update OLLAMA_SYSTEM_PROMPT with examples for the new tool
    #
    registry = ToolRegistry()
    registry.register(ShellTool())
    registry.register(DateTimeTool())
    registry.register(VolumeTool())
    registry.register(BrightnessTool())
    registry.register(SystemTool())
    registry.register(GameTool())
    registry.register(SearchTool())
    registry.register(NotesTool())
    from tools.mode import ModeTool
    from tools.app_launcher import AppLauncherTool
    from tools.process_manager import ProcessManagerTool
    from tools.browser import BrowserTool
    from tools.whatsapp import WhatsAppTool
    from tools.fitness import FitnessTool
    from tools.gmail import GmailTool
    from tools.google_calendar import CalendarTool
    
    registry.register(ModeTool())
    registry.register(AppLauncherTool())
    registry.register(ProcessManagerTool())
    registry.register(BrowserTool())
    registry.register(WhatsAppTool())
    registry.register(FitnessTool())
    registry.register(GmailTool())
    registry.register(CalendarTool())
    logger.info(f"Tools registered: {registry.list_tools()}")

    # ── Build the LangGraph ───────────────────────────────────────────────────
    graph = build_graph(listener, brain, voice, registry)

    # ── Shared state ──────────────────────────────────────────────────────────
    # One NidaState instance lives for the entire session.
    # The graph reads from it and returns partial dicts; we merge manually
    # since we're running in a persistent loop (not one-shot invoke).
    state = NidaState()

    # ── Hotkey ─────────────────────────────────────────────────────────────────────
    def request_wake():
        from core.signals import signals
        signals.wake_requested = True

    def request_sleep():
        from core.signals import signals
        signals.sleep_requested = True
        listener.interrupt()

    hotkey = HotkeyListener(on_wake=request_wake, on_sleep=request_sleep)
    hotkey.start()

    # ── Banner ────────────────────────────────────────────────────────────────
    voice.speak_sync("Nida is online. How can I help?")
    print("\n" + "═" * 60)
    print("  NIDA  |  Ctrl+Alt+N: wake  |  Ctrl+Alt+S: sleep  |  'exit' to quit")
    print("═" * 60 + "\n")

    # ── PyWebView API ─────────────────────────────────────────────────────────
    class NidaApi:
        def get_status(self):
            return {
                "mode": state.mode,
                "transcript": getattr(state, "user_text", ""),
                "response": getattr(state, "response_text", "")
            }

        def sleep_nida(self):
            request_sleep()

        def wake_nida(self):
            request_wake()

    # ── Main loop (Background Thread) ─────────────────────────────────────────
    def run_nida_loop():
        try:
            result = graph.invoke(state)
            voice.speak_sync("Goodbye.")
            logger.info(f"Session ended. Turns: {result.get('turn_count', 0)}")
        except Exception as e:
            logger.exception(f"Unhandled error in graph: {e}")
            voice.speak_sync("Something went wrong. Shutting down.")
        finally:
            hotkey.stop()
            listener.cleanup()
            print("Nida stopped.")
            import os
            os._exit(0)  # Ensure everything closes when Nida exits

    import threading
    nida_thread = threading.Thread(target=run_nida_loop, daemon=True)
    nida_thread.start()

    # ── Start UI (Main Thread) ────────────────────────────────────────────────
    import webview
    import os
    
    html_path = os.path.join(os.path.dirname(__file__), "web", "index.html")
    api = NidaApi()
    
    window = webview.create_window(
        'Nida Core', 
        url=f'file:///{html_path}', 
        js_api=api,
        width=350,
        height=550,
        resizable=False,
        frameless=True,
        easy_drag=True
    )
    
    webview.start(debug=False)

if __name__ == "__main__":
    main()
