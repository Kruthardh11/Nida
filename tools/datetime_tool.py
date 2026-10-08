# tools/datetime_tool.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Date & Time Tool
#
# WHY A DEDICATED TOOL:
#   Shell commands like `echo %TIME%` are fragile — different locales format
#   them differently, and LLMs often misroute time queries to ANSWER: instead
#   of using the shell. A dedicated Python tool avoids all that.
#
#   This also demonstrates how easy it is to add a non-shell tool now that
#   the registry system is in place.
# ─────────────────────────────────────────────────────────────────────────────

import logging
from datetime import datetime

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.datetime")


class DateTimeTool(BaseTool):
    """
    Returns the current date, time, or both.
    Pure Python — no shell commands, no locale issues.
    """

    @property
    def name(self) -> str:
        return "datetime"

    @property
    def description(self) -> str:
        return "Get the current date and/or time"

    @property
    def args_schema(self) -> dict[str, str]:
        return {"query": "what to get: 'time', 'date', or 'both'"}

    def run(self, args: dict) -> ToolResult:
        query = args.get("query", "both").strip().lower()
        now = datetime.now()

        if query == "time":
            result = now.strftime("It's %I:%M %p")
        elif query == "date":
            result = now.strftime("Today is %A, %B %d, %Y")
        else:
            result = now.strftime("It's %I:%M %p on %A, %B %d, %Y")

        logger.info(f"DateTimeTool: query='{query}' -> '{result}'")
        return ToolResult(success=True, output=result)
