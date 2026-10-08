# tools/base.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Tool Base Class & Result Type
#
# WHY THIS EXISTS:
#   Every tool Nida can use (shell, notes, volume, web search, etc.) needs a
#   consistent interface so the act_node can dispatch to any of them without
#   knowing their internals.
#
#   BaseTool defines the contract:
#     - name         : unique identifier the LLM uses in TOOL: lines
#     - description  : injected into the system prompt so the LLM knows
#                      when to pick this tool
#     - args_schema  : dict of arg_name → description (for prompt generation)
#     - run(args)    : does the work, returns a ToolResult
#
#   ToolResult is the universal return type — replaces ExecutionResult.
#   It's tool-agnostic: success/output/error works for shell commands,
#   note-taking, volume changes, anything.
#
# HOW TO ADD A NEW TOOL:
#   1. Create a new file in tools/ (e.g. tools/notes.py)
#   2. Subclass BaseTool, implement run()
#   3. Register it in main.py:  registry.register(NotesTool())
#   4. Update the system prompt with the new tool's examples
#   That's it. No node/edge changes needed.
# ─────────────────────────────────────────────────────────────────────────────

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """
    Universal result from any tool execution.

    Fields:
        success  : True if the tool completed without error
        output   : human-readable output (will be spoken by TTS if short enough)
        error    : error message if success is False
        blocked  : True if a safety check prevented execution
    """
    success: bool
    output: str = ""
    error: str = ""
    blocked: bool = False
    state_updates: dict = field(default_factory=dict)


class BaseTool(ABC):
    """
    Abstract base class for all Nida tools.

    Subclasses must define:
        name         — unique string identifier (e.g. "shell", "notes")
        description  — what this tool does (shown to the LLM in the prompt)
        args_schema  — dict mapping arg names to descriptions

    And implement:
        run(args)    — execute the tool with a dict of arguments
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier. The LLM writes TOOL: <name> {...}."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description. Injected into the system prompt."""
        ...

    @property
    @abstractmethod
    def args_schema(self) -> dict[str, str]:
        """
        Maps argument names to their descriptions.
        Example: {"command": "The shell command to execute"}
        Used to generate the AVAILABLE TOOLS block in the system prompt.
        """
        ...

    @abstractmethod
    def run(self, args: dict) -> ToolResult:
        """
        Execute the tool with the given arguments.

        Parameters
        ----------
        args : dict
            Parsed from the LLM's JSON output.
            Example: {"command": "dir"}

        Returns
        -------
        ToolResult with success, output, error, blocked fields.
        """
        ...
