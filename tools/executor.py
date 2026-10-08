# tools/executor.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Shell Tool  (formerly "Executor")
#
# WHAT CHANGED IN PHASE 2:
#   The old Executor class is now ShellTool, a BaseTool subclass.
#   The act_node no longer calls executor.run(command) directly — it calls
#   registry.get("shell").run({"command": "..."}).
#
#   Internally, NOTHING changed. The GUI detection, safety checks, CLI/GUI
#   routing, timeout handling — all identical. The only difference is:
#     - run() now takes a dict {"command": "..."} instead of a bare string
#     - run() returns ToolResult instead of ExecutionResult
#     - The class has name/description/args_schema for the registry
#
# ExecutionResult is kept as an internal type for the _run_gui/_run_cli
# methods. ShellTool.run() converts it to ToolResult before returning.
# ─────────────────────────────────────────────────────────────────────────────

import subprocess
import platform
import logging
from dataclasses import dataclass

from config.settings import DANGEROUS_KEYWORDS
from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.executor")

OS = platform.system()          # "Windows" | "Linux" | "Darwin"

# ── GUI apps: fire-and-forget, never wait for exit ────────────────────────────
# subprocess.run() WAITS for the process to finish before returning.
# GUI apps (notepad, chrome, code, etc.) never "finish" — they stay open.
# So run() times out after 15s and reports failure, even though the app opened.
#
# Fix: detect GUI commands and use Popen() instead (fire-and-forget).
# The process launches, we return success immediately without waiting.
GUI_APPS = {
    # Windows
    "notepad", "notepad.exe",
    "mspaint", "mspaint.exe",
    "calc", "calc.exe",
    "explorer", "explorer.exe",
    "taskmgr", "taskmgr.exe",
    "code", "code.exe",                 # VS Code
    "chrome", "chrome.exe",
    "msedge", "msedge.exe",
    "firefox", "firefox.exe",
    "winword", "excel", "powerpnt",     # Office
    "start",                            # Windows 'start' command always launches async
    # Linux / macOS
    "xdg-open", "open",
    "gedit", "mousepad", "kate",
    "nautilus", "thunar",
}


def _is_gui_command(command: str) -> bool:
    """
    Returns True if the command launches a GUI app that will never exit.
    Checks the first token of the command against the known GUI app list,
    and also treats any 'start <something>' as a GUI launch on Windows.
    """
    cmd_lower = command.strip().lower()
    first_token = cmd_lower.split()[0] if cmd_lower else ""

    # 'start chrome', 'start notepad', etc.
    if first_token == "start":
        return True

    return first_token in GUI_APPS


@dataclass
class ExecutionResult:
    """Internal result type for _run_gui / _run_cli. Converted to ToolResult by ShellTool.run()."""
    success: bool
    stdout: str
    stderr: str
    command: str
    blocked: bool = False       # True if safety check blocked execution


class ShellTool(BaseTool):
    """
    Runs shell commands on the user's computer.

    Two execution modes:
      - GUI apps  → Popen() fire-and-forget (never waits, returns immediately)
      - CLI tools → run() with 15s timeout, captures stdout/stderr

    Safety:
      - Checks against DANGEROUS_KEYWORDS before executing
      - Prompts user confirmation for risky commands
      - Caps stdout at 500 chars (TTS doesn't need full output)
    """

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "Run a shell command on the user's Windows computer"

    @property
    def args_schema(self) -> dict[str, str]:
        return {"command": "cmd.exe shell command to execute"}

    def run(self, args: dict) -> ToolResult:
        """
        Execute a shell command.

        Args dict must contain:
            command: str — the shell command to run

        Returns ToolResult with output/error.
        """
        command = args.get("command", "")
        if not command:
            return ToolResult(success=False, error="No command provided.")

        logger.info(f"Executing: {command}")

        # ── Safety gate ────────────────────────────────────────────────────
        danger = self._check_dangerous(command)
        if danger:
            confirmed = self._confirm(command, danger)
            if not confirmed:
                logger.warning(f"User blocked dangerous command: {command}")
                return ToolResult(
                    success=False,
                    error="Blocked by user.",
                    blocked=True
                )

        # ── Route: GUI app vs CLI tool ─────────────────────────────────────
        if _is_gui_command(command):
            result = self._run_gui(command)
        else:
            result = self._run_cli(command)

        # ── Convert ExecutionResult → ToolResult ───────────────────────────
        return ToolResult(
            success=result.success,
            output=result.stdout,
            error=result.stderr,
            blocked=result.blocked,
        )

    # ── Execution modes ────────────────────────────────────────────────────

    def _run_gui(self, command: str) -> ExecutionResult:
        """
        Fire-and-forget for GUI applications.
        Uses Popen() which launches the process and returns immediately.
        We don't wait, we don't capture output — GUI apps don't produce useful stdout.
        """
        try:
            if OS == "Windows":
                subprocess.Popen(
                    command,
                    shell=True,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                subprocess.Popen(
                    command,
                    shell=True,
                    executable="/bin/bash",
                    start_new_session=True
                )
            logger.info(f"GUI app launched: {command}")
            return ExecutionResult(True, "", "", command)

        except Exception as e:
            logger.error(f"GUI launch error: {e}")
            return ExecutionResult(False, "", str(e), command)

    def _run_cli(self, command: str) -> ExecutionResult:
        """
        Blocking execution for CLI tools that produce output.
        Waits up to 15 seconds and captures stdout/stderr.

        Windows note: CREATE_NO_WINDOW + capture_output + shell=True conflicts
        for builtins like echo, dir, set. Using explicit ["cmd", "/c", command]
        gives reliable stdout capture without that flag.
        """
        try:
            if OS == "Windows":
                result = subprocess.run(
                    ["cmd", "/c", command],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            else:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    executable="/bin/bash"
                )

            stdout = result.stdout.strip()[:500]
            stderr = result.stderr.strip()[:200]

            if result.returncode == 0:
                logger.info(f"Success. stdout: {stdout[:80]}")
                return ExecutionResult(True, stdout, stderr, command)
            else:
                logger.warning(f"Returned code {result.returncode}. stderr: {stderr}")
                return ExecutionResult(False, stdout, stderr, command)

        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {command}")
            return ExecutionResult(False, "", "Command timed out.", command)

        except Exception as e:
            logger.error(f"Execution error: {e}")
            return ExecutionResult(False, "", str(e), command)

    # ── Private ────────────────────────────────────────────────────────────

    def _check_dangerous(self, command: str) -> str | None:
        """Returns the matched dangerous keyword, or None if safe."""
        lower = command.lower()
        for keyword in DANGEROUS_KEYWORDS:
            if keyword.lower() in lower:
                return keyword
        return None

    def _confirm(self, command: str, reason: str) -> bool:
        """CLI confirmation prompt for dangerous commands."""
        print(f"\n⚠️  DANGEROUS COMMAND DETECTED")
        print(f"   Matched keyword : '{reason}'")
        print(f"   Full command     : {command}")
        answer = input("   Allow? (yes/no): ").strip().lower()
        return answer in ("yes", "y")
