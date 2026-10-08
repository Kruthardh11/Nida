# tools/mode.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Mode / Persona Switcher Tool
#
# Allows Nida to switch between different predefined personas (e.g., assistant
# vs instructor) by updating the global state.
# ─────────────────────────────────────────────────────────────────────────────

import logging
from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.mode")

class ModeTool(BaseTool):
    """
    Switches Nida's persona/mode. Used when the user wants to enter different
    modes like 'Learn Mode' (instructor) or regular 'Assistant'.
    """

    @property
    def name(self) -> str:
        return "mode_tool"

    @property
    def description(self) -> str:
        return "Switches your persona between 'assistant', 'instructor', and 'trainer'."

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "target": "The persona to switch to: 'assistant', 'instructor', or 'trainer'"
        }

    def run(self, args: dict) -> ToolResult:
        target = args.get("target", "").strip().lower()
        
        if target not in ["assistant", "instructor", "trainer"]:
            return ToolResult(
                success=False, 
                error=f"Invalid target persona: '{target}'. Must be assistant, instructor, or trainer."
            )

        logger.info(f"Switching persona to: {target}")
        
        return ToolResult(
            success=True,
            output=f"Successfully switched to {target} mode.",
            state_updates={"persona": target}
        )
