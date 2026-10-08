# tools/brightness.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Brightness Control Tool
#
# Controls display brightness via PowerShell WMI — no Python dependencies.
#
# HOW IT WORKS:
#   Windows exposes monitor brightness through the WMI class
#   WmiMonitorBrightness (read) and WmiMonitorBrightnessMethods (write)
#   in the root/WMI namespace. We call these via PowerShell subprocess.
#
# LIMITATIONS:
#   - Works on most laptops (integrated displays)
#   - May NOT work on desktops or external monitors (no WMI support)
#   - Tool returns a clear error message when the hardware doesn't support it
#
# ACTIONS:
#   set    — set brightness to an exact percentage (0-100)
#   get    — return current brightness percentage
#   up     — increase by 10%
#   down   — decrease by 10%
# ─────────────────────────────────────────────────────────────────────────────

import logging
import subprocess

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.brightness")

STEP_SIZE = 10  # brightness up/down step (%)


def _run_ps(script: str) -> tuple[bool, str, str]:
    """
    Run a PowerShell script and return (success, stdout, stderr).
    Uses -NoProfile for speed and -Command for inline scripts.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (
            result.returncode == 0,
            result.stdout.strip(),
            result.stderr.strip(),
        )
    except subprocess.TimeoutExpired:
        return False, "", "PowerShell timed out."
    except Exception as e:
        return False, "", str(e)


class BrightnessTool(BaseTool):
    """
    Controls display brightness via PowerShell WMI.
    No Python dependencies — pure subprocess calls.
    """

    @property
    def name(self) -> str:
        return "brightness"

    @property
    def description(self) -> str:
        return "Control screen brightness: set, get, up, down"

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action": "set, get, up, or down",
            "level": "(optional) brightness percentage 0-100, used with 'set'"
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").strip().lower()

        if action == "set":
            return self._set_brightness(args)
        elif action == "get":
            return self._get_brightness()
        elif action == "up":
            return self._step_brightness(STEP_SIZE)
        elif action == "down":
            return self._step_brightness(-STEP_SIZE)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown brightness action: '{action}'. Use set, get, up, or down."
            )

    def _get_brightness(self) -> ToolResult:
        """Get the current brightness level."""
        script = (
            "(Get-CimInstance -Namespace root/WMI "
            "-ClassName WmiMonitorBrightness).CurrentBrightness"
        )
        ok, stdout, stderr = _run_ps(script)

        if not ok or not stdout:
            logger.error(f"Brightness get failed: {stderr}")
            return ToolResult(
                success=False,
                error="Could not read brightness. Your display may not support WMI brightness control."
            )

        try:
            level = int(stdout)
        except ValueError:
            return ToolResult(success=False, error=f"Unexpected brightness value: '{stdout}'")

        logger.info(f"Brightness: {level}%")
        return ToolResult(success=True, output=f"Brightness is at {level} percent.")

    def _set_brightness(self, args: dict) -> ToolResult:
        """Set brightness to an exact percentage (0-100)."""
        level = args.get("level")
        if level is None:
            return ToolResult(success=False, error="Missing 'level' argument for brightness set.")

        try:
            level = int(level)
        except (ValueError, TypeError):
            return ToolResult(success=False, error=f"Invalid brightness level: '{level}'. Use 0-100.")

        level = max(0, min(100, level))

        script = (
            f"$m = Get-CimInstance -Namespace root/WMI "
            f"-ClassName WmiMonitorBrightnessMethods; "
            f"$m | Invoke-CimMethod -MethodName WmiSetBrightness "
            f"-Arguments @{{Brightness={level}; Timeout=0}}"
        )
        ok, stdout, stderr = _run_ps(script)

        if not ok:
            logger.error(f"Brightness set failed: {stderr}")
            return ToolResult(
                success=False,
                error="Could not set brightness. Your display may not support WMI brightness control."
            )

        logger.info(f"Brightness set to {level}%")
        return ToolResult(success=True, output=f"Brightness set to {level} percent.")

    def _step_brightness(self, step: int) -> ToolResult:
        """Step brightness up or down by STEP_SIZE."""
        # First get current level
        script = (
            "(Get-CimInstance -Namespace root/WMI "
            "-ClassName WmiMonitorBrightness).CurrentBrightness"
        )
        ok, stdout, stderr = _run_ps(script)

        if not ok or not stdout:
            return ToolResult(
                success=False,
                error="Could not read current brightness."
            )

        try:
            current = int(stdout)
        except ValueError:
            return ToolResult(success=False, error=f"Unexpected brightness value: '{stdout}'")

        new_level = max(0, min(100, current + step))

        # Now set the new level
        set_script = (
            f"$m = Get-CimInstance -Namespace root/WMI "
            f"-ClassName WmiMonitorBrightnessMethods; "
            f"$m | Invoke-CimMethod -MethodName WmiSetBrightness "
            f"-Arguments @{{Brightness={new_level}; Timeout=0}}"
        )
        ok, stdout, stderr = _run_ps(set_script)

        if not ok:
            return ToolResult(success=False, error="Could not set brightness.")

        direction = "up" if step > 0 else "down"
        logger.info(f"Brightness {direction} to {new_level}%")
        return ToolResult(success=True, output=f"Brightness {direction} to {new_level} percent.")
