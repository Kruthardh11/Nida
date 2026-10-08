# tools/game.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Game Mode Tool
#
# ACTIONS:
#   analyze  — Kill hogs, check GPU temp, apply optimizations, list games.
#              Returns a spoken response telling the user what was found,
#              so the LLM can relay it and ask which game to play.
#   launch   — Launch a game by name from the games/ folder & escalate its
#              process priority to High.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import subprocess
import time
import re
from pathlib import Path

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.game")

# ── Constants ──────────────────────────────────────────────────────────────
GAMES_DIR = Path(__file__).parent.parent / "games"

# High Performance plan GUID (built into Windows, no need to create it)
HIGH_PERF_GUID   = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED_GUID    = "381b4222-f694-41f0-9685-ff5bb260df2e"

# Background apps to kill when game mode activates
HOGS = ["chrome", "msedge", "discord"]

# GPU temp thresholds for spoken feedback
GPU_WARN_TEMP  = 75   # "Moderate"
GPU_HOT_TEMP   = 85   # "Hot – use a cooling pad!"


# ── Helpers ────────────────────────────────────────────────────────────────

def _run_ps(script: str, timeout: int = 10) -> tuple[bool, str, str]:
    """Run a PowerShell script and return (success, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "PowerShell timed out."
    except Exception as e:
        return False, "", str(e)


def _kill_hogs() -> list[str]:
    """Silently kill known resource-hog processes. Returns names of what was killed."""
    killed = []
    for proc in HOGS:
        r = subprocess.run(
            ["taskkill", "/F", "/IM", f"{proc}.exe"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            killed.append(proc)
            logger.info(f"Killed: {proc}.exe")
    return killed


def _set_high_perf():
    """Switch to High Performance power plan via powercfg."""
    subprocess.run(
        ["powercfg", "-SETACTIVE", HIGH_PERF_GUID],
        capture_output=True
    )
    logger.info("Power plan → High Performance")


def _restore_balanced():
    """Restore Balanced power plan."""
    subprocess.run(
        ["powercfg", "-SETACTIVE", BALANCED_GUID],
        capture_output=True
    )
    logger.info("Power plan → Balanced")


def _set_windows_game_mode(enable: bool):
    """Toggle Windows Game Mode via registry (HKCU - no admin needed)."""
    value = 1 if enable else 0
    script = (
        "$p = 'HKCU:\\Software\\Microsoft\\GameBar'; "
        "if (!(Test-Path $p)) { New-Item -Path $p -Force | Out-Null }; "
        f"Set-ItemProperty -Path $p -Name 'AllowAutoGameMode' -Value {value} -Type DWord -Force"
    )
    _run_ps(script)
    logger.info(f"Windows Game Mode → {'ON' if enable else 'OFF'}")


def _set_dnd(enable: bool):
    """Toggle Do Not Disturb via registry (ToastEnabled)."""
    value = 0 if enable else 1
    reg_path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications"
    script = (
        f"$p = '{reg_path}'; "
        f"if (!(Test-Path $p)) {{ New-Item -Path $p -Force | Out-Null }}; "
        f"Set-ItemProperty -Path $p -Name 'ToastEnabled' -Value {value} -Type DWord -Force"
    )
    _run_ps(script)
    logger.info(f"DND → {'ON' if enable else 'OFF'}")


def _get_gpu_temp() -> int | None:
    """Read current GPU temperature via nvidia-smi. Returns °C or None."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return int(r.stdout.strip())
    except Exception:
        pass
    return None


def _scan_games() -> list[Path]:
    """
    List all .lnk and .url files in the games/ folder.
    Returns a list of Path objects sorted alphabetically.
    """
    GAMES_DIR.mkdir(exist_ok=True)
    return sorted(
        p for p in GAMES_DIR.iterdir()
        if p.suffix.lower() in (".lnk", ".url", ".exe")
    )


def _game_display_name(path: Path) -> str:
    """Return a clean human-readable name from a shortcut filename."""
    return path.stem.replace("_", " ").replace("-", " ").strip()


def _find_game_path(game_name: str) -> Path | None:
    """
    Match a game name (from LLM) to a file in the games/ folder.
    Uses robust word-based matching to handle missing spaces (e.g., GhostOfTsushima)
    and extra words (e.g., EA SPORTS FC 24 vs EA FC 24).
    """
    query_words = re.findall(r'[a-z0-9]+', game_name.lower())
    if not query_words:
        return None

    for p in _scan_games():
        file_name_lower = p.stem.lower()
        
        # 1. First try simple substring matching (ignoring all spaces/symbols)
        query_clean = "".join(query_words)
        file_clean = re.sub(r'[^a-z0-9]', '', file_name_lower)
        if query_clean in file_clean or file_clean in query_clean:
            return p
            
        # 2. Fallback: check if every word from the query is found somewhere in the filename
        # This helps match "ea fc 24" to "easportsfc24"
        if all(word in file_name_lower for word in query_words):
            return p

    return None


def _launch_shortcut(path: Path) -> tuple[bool, str]:
    """
    Launch a .lnk/.url shortcut or .exe via Windows shell.
    Returns (success, error_msg).
    """
    try:
        # Use 'start' to respect the shortcut's icon/UAC settings
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             f"Start-Process -FilePath '{path}' -ErrorAction Stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, ""
    except Exception as e:
        return False, str(e)


def _escalate_priority(process_name: str):
    """
    Escalate a running process to High priority.
    Waits up to 15 seconds for the process to appear.
    process_name is the stem of the exe (no .exe extension).
    """
    script = (
        f"$deadline = (Get-Date).AddSeconds(15); "
        f"while ((Get-Date) -lt $deadline) {{ "
        f"  $p = Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue; "
        f"  if ($p) {{ $p | ForEach-Object {{ $_.PriorityClass = 'High' }}; Write-Output 'OK'; break }}; "
        f"  Start-Sleep -Milliseconds 500 }}"
    )
    ok, stdout, _ = _run_ps(script, timeout=20)
    if ok and "OK" in stdout:
        logger.info(f"Priority escalated: {process_name} → High")
    else:
        logger.warning(f"Could not escalate priority for '{process_name}' (process may not have appeared yet)")


# ── Tool Class ─────────────────────────────────────────────────────────────

class GameTool(BaseTool):
    """
    Activates Game Mode: kills hogs, tweaks power settings, checks GPU temp,
    lists available games, and launches the chosen one at High priority.
    """

    @property
    def name(self) -> str:
        return "game_tool"

    @property
    def description(self) -> str:
        return "Activate game mode: optimize PC and launch a game. Actions: analyze, launch"

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action": "analyze (optimize PC & list games) or launch (start a specific game)",
            "game":   "(optional) game name, required when action is 'launch'",
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").strip().lower()

        if action == "analyze":
            return self._analyze()
        elif action == "launch":
            return self._launch(args.get("game", "").strip())
        else:
            return ToolResult(
                success=False,
                error=f"Unknown game_tool action: '{action}'. Use analyze or launch."
            )

    # ── Analyze ─────────────────────────────────────────────────────────────

    def _analyze(self) -> ToolResult:
        parts = []

        # 1. Kill hogs
        killed = _kill_hogs()
        if killed:
            names = " and ".join(k.capitalize() for k in killed)
            parts.append(f"{names} closed")

        # 2. Apply system optimizations
        _set_high_perf()
        _set_windows_game_mode(True)
        _set_dnd(True)
        parts.append("PC optimized for gaming")

        # 3. GPU thermal check
        temp = _get_gpu_temp()
        if temp is not None:
            if temp >= GPU_HOT_TEMP:
                parts.append(f"Warning: GPU is running hot at {temp} degrees — please use a cooling pad")
            elif temp >= GPU_WARN_TEMP:
                parts.append(f"GPU at {temp} degrees, getting warm — monitor your temps")
            else:
                parts.append(f"GPU is cool at {temp} degrees")
        else:
            parts.append("GPU temperature check unavailable")

        # 4. Scan for games
        game_paths = _scan_games()
        if not game_paths:
            msg = (
                ". ".join(parts) + ". "
                "No games found in the games folder. "
                "Tell the user to add their game shortcuts to the games folder using shell:appsfolder."
            )
            logger.info("Game mode activated. No games in games/ folder.")
            return ToolResult(success=True, output=msg)

        game_names = [_game_display_name(p) for p in game_paths]
        game_list  = " and ".join(game_names) if len(game_names) <= 2 else \
                     ", ".join(game_names[:-1]) + f" and {game_names[-1]}"

        msg = (
            ". ".join(parts) + f". "
            f"I found {len(game_names)} game{'s' if len(game_names) > 1 else ''}: {game_list}. "
            f"Which one do you want to play?"
        )
        logger.info(f"Game mode activated. Found games: {game_names}")
        return ToolResult(success=True, output=msg)

    # ── Launch ──────────────────────────────────────────────────────────────

    def _launch(self, game_name: str) -> ToolResult:
        if not game_name:
            return ToolResult(
                success=False,
                error="No game name provided. Specify the game to launch."
            )

        path = _find_game_path(game_name)
        if path is None:
            # Fallback to App Launcher to dynamically launch games not in the games/ folder
            from tools.app_launcher import AppLauncherTool
            app_tool = AppLauncherTool()
            app_res = app_tool.run({"app_name": game_name})
            
            if app_res.success:
                logger.info(f"AppLauncher successfully launched: {game_name}")
                # Best-effort priority escalation for dynamically found apps
                self._try_escalate_priority(game_name.split()[0])
                return ToolResult(success=True, output=f"Launching {game_name}. Enjoy your game!")
            else:
                # List available games for the error
                available = [_game_display_name(p) for p in _scan_games()]
                hint = f"Available local games: {', '.join(available)}." if available else "No local games found."
                return ToolResult(
                    success=False,
                    error=f"Could not find '{game_name}' locally or in system apps. {hint}"
                )

        ok, err = _launch_shortcut(path)
        if not ok:
            logger.error(f"Failed to launch {path}: {err}")
            return ToolResult(success=False, error=f"Could not launch {game_name}: {err}")

        display = _game_display_name(path)
        logger.info(f"Launching: {display} ({path.name})")

        # Escalate priority in the background (game may take a few seconds to appear)
        self._try_escalate_priority(path.stem.split()[0])

        return ToolResult(
            success=True,
            output=f"Launching {display}. Enjoy your game!"
        )

    def _try_escalate_priority(self, process_name: str):
        """Helper to safely spawn a background thread for priority escalation."""
        try:
            import threading
            t = threading.Thread(
                target=_escalate_priority,
                args=(process_name,),
                daemon=True,
            )
            t.start()
        except Exception as e:
            logger.warning(f"Priority escalation thread failed to start: {e}")
