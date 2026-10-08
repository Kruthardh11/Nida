import logging
import os
import winreg
from pathlib import Path

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.app_launcher")

class AppLauncherTool(BaseTool):
    """
    Intelligently launches desktop applications.
    Resolves app names securely by leveraging native OS shortcut resolution.
    """

    @property
    def name(self) -> str:
        return "app_launcher"

    @property
    def description(self) -> str:
        return "Launch an application securely (e.g. 'discord', 'whatsapp', 'spotify')."

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "app_name": "The generic name of the application to launch"
        }

    def run(self, args: dict) -> ToolResult:
        app_name = args.get("app_name", "").strip().lower()
        if not app_name:
            return ToolResult(success=False, error="No app_name provided.")

        logger.info(f"Attempting to launch app: {app_name}")

        # 1. Start Menu Shortcuts (Handles Discord, Spotify, VSCode perfectly by preserving arguments)
        shortcut_path = self._find_in_start_menu(app_name)
        if shortcut_path and self._launch_native(shortcut_path):
            return ToolResult(success=True, output=f"Successfully launched {app_name} from shortcut.")

        # 2. UWP Protocol Fallback (Handles WhatsApp, Settings, Microsoft Store)
        # We wrap in try/except because invalid URIs throw a FileNotFoundError in os.startfile
        if self._launch_native(f"{app_name}:"):
            return ToolResult(success=True, output=f"Successfully launched {app_name} via Windows Protocol.")

        # 3. Last Resort: Registry App Paths (Handles legacy executables without shortcuts)
        registry_path = self._find_in_registry(app_name)
        if registry_path and self._launch_native(registry_path):
            return ToolResult(success=True, output=f"Successfully launched {app_name} from registry.")

        # 4. Ultimate Fallback for direct EXEs
        if app_name.endswith(".exe") and self._launch_native(app_name):
             return ToolResult(success=True, output=f"Successfully executed {app_name}.")

        return ToolResult(success=False, error=f"Could not find or launch application: '{app_name}'")

    def _launch_native(self, target: str) -> bool:
        """Use the native Windows shell to gracefully open ANY file, shortcut, or URI."""
        try:
            os.startfile(target)
            return True
        except Exception:
            return False

    def _find_in_start_menu(self, app_name: str) -> str | None:
        """Scan common Start Menu locations for .lnk files matching the app name."""
        locations = [
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs")
        ]
        
        matches = []
        for loc in locations:
            start_menu_path = Path(loc)
            if not start_menu_path.exists():
                continue
                
            for root, _, files in os.walk(start_menu_path):
                for file in files:
                    if file.lower().endswith(".lnk"):
                        if app_name in file.lower():
                            matches.append(os.path.join(root, file))
                            
        # Sort by shortest filename length (most exact match first)
        matches.sort(key=lambda p: len(os.path.basename(p)))
        
        if not matches:
            return None
            
        # We do NOT extract targetpath because doing so strips arguments (e.g., Discord's --processStart)
        # Returning the direct path to the .lnk ensures all metadata is executed flawlessly.
        return matches[0]

    def _find_in_registry(self, app_name: str) -> str | None:
        search_key = app_name if app_name.endswith(".exe") else f"{app_name}.exe"
        hives = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        
        for hive in hives:
            try:
                with winreg.OpenKey(hive, path) as app_paths:
                    try:
                        with winreg.OpenKey(app_paths, search_key) as app_key:
                            file_path, _ = winreg.QueryValueEx(app_key, "")
                            return file_path
                    except FileNotFoundError:
                        pass
            except WindowsError:
                continue
                
        return None
