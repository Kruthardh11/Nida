# tools/volume.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Volume Control Tool
#
# Controls system audio via the Windows Core Audio API (pycaw).
#
# WHY PYCAW INSTEAD OF SHELL:
#   Shell-based approaches (nircmd, PowerShell) are fragile, slow, and
#   require external tools. pycaw talks directly to the Windows audio
#   engine — instant, precise, and no external dependencies beyond the
#   tiny pycaw+comtypes packages.
#
# ACTIONS:
#   set    — set volume to an exact percentage (0-100)
#   get    — return current volume percentage
#   up     — increase by 5%
#   down   — decrease by 5%
#   mute   — mute system audio
#   unmute — unmute system audio
# ─────────────────────────────────────────────────────────────────────────────

import logging

from pycaw.pycaw import AudioUtilities

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.volume")

# Volume up/down step size (as a fraction of 0-1)
STEP_SIZE = 0.05  # 5%


def _get_volume_interface():
    """
    Get the IAudioEndpointVolume interface for the default speaker.
    pycaw 20251023+ returns an AudioDevice wrapper with an
    EndpointVolume property that gives us the COM interface directly.
    """
    device = AudioUtilities.GetSpeakers()
    return device.EndpointVolume


class VolumeTool(BaseTool):
    """
    Controls the system volume via Windows Core Audio API.

    Uses pycaw's scalar API (0.0 to 1.0) which maps directly to the
    Windows volume slider position. No dB conversion needed.
    """

    @property
    def name(self) -> str:
        return "volume"

    @property
    def description(self) -> str:
        return "Control system volume: set, get, mute, unmute, up, down"

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action": "set, get, mute, unmute, up, or down",
            "level": "(optional) volume percentage 0-100, used with 'set'"
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").strip().lower()

        try:
            vol = _get_volume_interface()
        except Exception as e:
            logger.error(f"Failed to access audio device: {e}")
            return ToolResult(success=False, error="Could not access audio device.")

        try:
            if action == "set":
                return self._set_volume(vol, args)
            elif action == "get":
                return self._get_volume(vol)
            elif action == "mute":
                return self._set_mute(vol, True)
            elif action == "unmute":
                return self._set_mute(vol, False)
            elif action == "up":
                return self._step_volume(vol, args, direction=1)
            elif action == "down":
                return self._step_volume(vol, args, direction=-1)
            else:
                return ToolResult(
                    success=False,
                    error=f"Unknown volume action: '{action}'. Use set, get, mute, unmute, up, or down."
                )
        except Exception as e:
            logger.error(f"Volume control error: {e}")
            return ToolResult(success=False, error=str(e))

    def _set_volume(self, vol, args: dict) -> ToolResult:
        """Set volume to an exact percentage (0-100)."""
        level = args.get("level")
        if level is None:
            return ToolResult(success=False, error="Missing 'level' argument for volume set.")

        try:
            level = int(level)
        except (ValueError, TypeError):
            return ToolResult(success=False, error=f"Invalid volume level: '{level}'. Use 0-100.")

        level = max(0, min(100, level))
        vol.SetMasterVolumeLevelScalar(level / 100.0, None)

        logger.info(f"Volume set to {level}%")
        return ToolResult(success=True, output=f"Volume set to {level} percent.")

    def _get_volume(self, vol) -> ToolResult:
        """Get current volume as a percentage."""
        current = vol.GetMasterVolumeLevelScalar()
        pct = round(current * 100)

        is_muted = vol.GetMute()
        status = f"Volume is at {pct} percent"
        if is_muted:
            status += " and muted"
        status += "."

        logger.info(f"Volume: {pct}%, muted={is_muted}")
        return ToolResult(success=True, output=status)

    def _set_mute(self, vol, mute: bool) -> ToolResult:
        """Mute or unmute system audio."""
        vol.SetMute(1 if mute else 0, None)
        state = "muted" if mute else "unmuted"
        logger.info(f"Audio {state}")
        return ToolResult(success=True, output=f"Audio {state}.")

    def _step_volume(self, vol, args: dict, direction: int) -> ToolResult:
        """
        Step volume up or down.
        Uses 'level' from args as the step amount (in %).
        Falls back to STEP_SIZE (5%) if 'level' not provided.
        """
        # If the LLM passes a level, use it as the step amount
        level = args.get("level")
        if level is not None:
            try:
                step = int(level) / 100.0
            except (ValueError, TypeError):
                step = STEP_SIZE
        else:
            step = STEP_SIZE

        step = step * direction  # apply direction (+ or -)

        current = vol.GetMasterVolumeLevelScalar()
        new_level = max(0.0, min(1.0, current + step))
        vol.SetMasterVolumeLevelScalar(new_level, None)

        display_pct = round(new_level * 100)
        dir_word = "up" if direction > 0 else "down"
        logger.info(f"Volume {dir_word} to {display_pct}%")
        return ToolResult(success=True, output=f"Volume {dir_word} to {display_pct} percent.")

