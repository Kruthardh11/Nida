# tools/system_control.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — System Control Tool
#
# Catch-all for system-level actions: lock screen, screenshot, battery
# status, and Do Not Disturb toggling. All implemented via subprocess
# (PowerShell/rundll32) — no Python dependencies.
#
# ACTIONS:
#   lock       — lock the screen immediately
#   screenshot — capture full screen, save to screenshots/ folder
#   battery    — report battery level and charging status
#   dnd_on     — suppress Windows notification toasts
#   dnd_off    — re-enable Windows notification toasts
# ─────────────────────────────────────────────────────────────────────────────

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.system")

# Screenshots saved here (relative to project root)
SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"


def _run_ps(script: str, timeout: int = 10) -> tuple[bool, str, str]:
    """Run a PowerShell script and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
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


class SystemTool(BaseTool):
    """
    System control actions: lock, screenshot, battery, DND.
    All implemented via subprocess — no extra Python dependencies.
    """

    @property
    def name(self) -> str:
        return "system"

    @property
    def description(self) -> str:
        return "System actions: lock screen, screenshot, battery status, do not disturb"

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action": "lock, screenshot, battery, dnd_on, dnd_off, or hardware"
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").strip().lower()

        if action == "lock":
            return self._lock_screen()
        elif action == "screenshot":
            return self._take_screenshot()
        elif action == "battery":
            return self._get_battery()
        elif action == "hardware":
            return self._get_hardware_status()
        elif action == "dnd_on":
            return self._set_dnd(True)
        elif action == "dnd_off":
            return self._set_dnd(False)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown system action: '{action}'. "
                      f"Use lock, screenshot, battery, hardware, dnd_on, or dnd_off."
            )

    # ── Hardware Diagnostics ────────────────────────────────────────────────

    def _get_hardware_status(self) -> ToolResult:
        """
        Retrieves CPU / RAM utilization securely via psutil and attempts 
        an nvidia-smi GPU thermal probe if applicable.
        """
        import psutil
        
        cpu_pct = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        
        status = f"CPU load is at {round(cpu_pct)} percent. RAM utilization is at {round(ram.percent)} percent."
        
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                gpu_temp = int(r.stdout.strip())
                status += f" The GPU is currently running at {gpu_temp} degrees Celsius."
        except Exception:
            # Not an Nvidia system or don't have driver tools accessible
            pass
            
        logger.info(f"Hardware Pulse: {status}")
        return ToolResult(success=True, output=status)

    # ── Lock Screen ─────────────────────────────────────────────────────────

    def _lock_screen(self) -> ToolResult:
        """Lock the workstation immediately."""
        try:
            subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                timeout=5,
            )
            logger.info("Screen locked")
            return ToolResult(success=True, output="Screen locked.")
        except Exception as e:
            logger.error(f"Lock failed: {e}")
            return ToolResult(success=False, error=f"Could not lock screen: {e}")

    # ── Screenshot ──────────────────────────────────────────────────────────

    def _take_screenshot(self) -> ToolResult:
        """
        Capture a screenshot of all displays and save to screenshots/ folder.
        Uses .NET System.Windows.Forms via PowerShell — no Pillow needed.
        """
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"nida_screenshot_{timestamp}.png"
        filepath = SCREENSHOT_DIR / filename

        # PowerShell script that uses .NET to capture the screen
        # Works without any external tools
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save('{filepath}')
$graphics.Dispose()
$bitmap.Dispose()
Write-Output 'OK'
"""
        ok, stdout, stderr = _run_ps(script, timeout=15)

        if ok and filepath.exists():
            logger.info(f"Screenshot saved: {filepath}")
            return ToolResult(
                success=True,
                output="Screenshot taken and saved."
            )
        else:
            logger.error(f"Screenshot failed: {stderr}")
            return ToolResult(success=False, error="Could not take screenshot.")

    # ── Battery Status ──────────────────────────────────────────────────────

    def _get_battery(self) -> ToolResult:
        """
        Get battery level and charging status.
        Uses WMI Win32_Battery class via PowerShell.
        """
        script = (
            "$b = Get-CimInstance -ClassName Win32_Battery; "
            "if ($b) { "
            "  $pct = $b.EstimatedChargeRemaining; "
            "  $status = switch($b.BatteryStatus) { "
            "    1 {'discharging'} 2 {'plugged in'} 3 {'fully charged'} "
            "    4 {'low'} 5 {'critical'} default {'unknown'} "
            "  }; "
            "  Write-Output \"$pct|$status\" "
            "} else { Write-Output 'NO_BATTERY' }"
        )
        ok, stdout, stderr = _run_ps(script)

        if not ok:
            logger.error(f"Battery check failed: {stderr}")
            return ToolResult(success=False, error="Could not check battery status.")

        if stdout == "NO_BATTERY":
            return ToolResult(success=True, output="No battery detected. This might be a desktop.")

        try:
            parts = stdout.split("|")
            pct = int(parts[0])
            status = parts[1] if len(parts) > 1 else "unknown"
        except (ValueError, IndexError):
            return ToolResult(success=False, error=f"Unexpected battery data: '{stdout}'")

        output = f"Battery is at {pct} percent, {status}."
        logger.info(f"Battery: {pct}%, {status}")
        return ToolResult(success=True, output=output)

    # ── Do Not Disturb ──────────────────────────────────────────────────────

    def _set_dnd(self, enable: bool) -> ToolResult:
        """
        Toggle Windows notification toasts on/off.

        Sets the ToastEnabled registry value:
          0 = notifications suppressed (DND on)
          1 = notifications enabled (DND off)

        Registry key:
          HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications
        """
        value = 0 if enable else 1
        reg_path = (
            r"HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications"
        )

        script = (
            f"$path = '{reg_path}'; "
            f"if (!(Test-Path $path)) {{ New-Item -Path $path -Force | Out-Null }}; "
            f"Set-ItemProperty -Path $path -Name 'ToastEnabled' -Value {value} "
            f"-Type DWord -Force"
        )
        ok, stdout, stderr = _run_ps(script)

        if not ok:
            logger.error(f"DND toggle failed: {stderr}")
            return ToolResult(success=False, error="Could not toggle Do Not Disturb.")

        state = "on" if enable else "off"
        action = "Notifications silenced" if enable else "Notifications re-enabled"
        logger.info(f"DND {state}")
        return ToolResult(success=True, output=f"Do not disturb {state}. {action}.")
