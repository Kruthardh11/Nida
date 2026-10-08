# tools/registry.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Tool Registry
#
# WHY A REGISTRY:
#   The act_node needs to dispatch TOOL: <name> to the correct tool class.
#   A registry is the simplest way: it's a dict of name → BaseTool instance.
#
#   Adding a tool = registry.register(MyTool()).
#   Removing a tool = don't register it.
#   No if/elif chains, no node changes, no edge changes.
#
# PROMPT GENERATION:
#   The registry also generates the AVAILABLE TOOLS block for the system
#   prompt so the LLM always knows exactly which tools exist and what
#   arguments they accept. This is auto-generated — you never hand-write
#   tool descriptions in the prompt.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from typing import Optional

from tools.base import BaseTool

logger = logging.getLogger("nida.registry")


class ToolRegistry:
    """
    Central registry of all available tools.

    Usage:
        registry = ToolRegistry()
        registry.register(ShellTool())
        registry.register(NotesTool())

        tool = registry.get("shell")
        result = tool.run({"command": "dir"})
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool. Overwrites if name already exists."""
        if tool.name in self._tools:
            logger.warning(f"Overwriting existing tool: '{tool.name}'")
        self._tools[tool.name] = tool
        logger.info(f"Tool registered: '{tool.name}' — {tool.description}")

    def get(self, name: str) -> Optional[BaseTool]:
        """Look up a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Returns a list of registered tool names."""
        return list(self._tools.keys())

    def tool_prompt_block(self) -> str:
        """
        Generates the AVAILABLE TOOLS section for the LLM system prompt.

        Output format:
            AVAILABLE TOOLS:
              shell — Run a shell command on the user's Windows computer.
                Args: {"command": "<cmd.exe command>"}
              notes — Save a quick note or reminder.
                Args: {"text": "<note content>"}
        """
        if not self._tools:
            return ""

        lines = ["AVAILABLE TOOLS:"]
        for tool in self._tools.values():
            lines.append(f"  {tool.name} — {tool.description}")
            # Format args schema as a JSON-like hint
            args_hint = ", ".join(
                f'"{k}": "<{v}>"' for k, v in tool.args_schema.items()
            )
            lines.append(f"    Args: {{{args_hint}}}")

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
