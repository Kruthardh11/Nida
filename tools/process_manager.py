import logging
import psutil
import win32gui
import win32process
import win32api
import win32con

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.process_manager")

class ProcessManagerTool(BaseTool):
    """
    Manages running processes on the system.
    Can list active UI windows, forcefully kill processes, and bring specific windows to focus.
    """

    @property
    def name(self) -> str:
        return "process_manager"

    @property
    def description(self) -> str:
        return "Manage system processes. Actions: list_ui (what is open on screen?), kill (close an app), focus (bring window to front)."

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action": "'list_ui', 'kill', or 'focus'",
            "target": "The process name (e.g. 'brave', 'code', 'discord') if action is kill or focus. Optional for list_ui."
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").strip().lower()
        target = args.get("target", "").strip().lower()

        if action == "list_ui":
            return self._list_ui()
        elif action == "kill":
            if not target:
                return ToolResult(success=False, error="Must provide 'target' for kill action.")
            return self._kill_process(target)
        elif action == "focus":
            if not target:
                return ToolResult(success=False, error="Must provide 'target' for focus action.")
            return self._focus_window(target)
        else:
            return ToolResult(success=False, error=f"Unknown process manager action: '{action}'")

    def _get_ui_windows(self) -> list[dict]:
        """
        Enumerate all top-level windows and map them to their Process Names.
        Returns a list of dicts: {"hwnd": int, "title": str, "process": str, "pid": int}
        """
        windows = []

        def enum_handler(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return

                # Filter out basic OS cruft
                if title == "Program Manager" or title == "Settings":
                    return

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    proc = psutil.Process(pid)
                    proc_name = proc.name().lower()
                    # Ignore pure background tasks that somehow have a window
                    if proc_name not in ["explorer.exe", "searchapp.exe", "textinputhost.exe"]:
                        windows.append({
                            "hwnd": hwnd,
                            "title": title,
                            "process": proc_name,
                            "pid": pid
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        win32gui.EnumWindows(enum_handler, None)
        return windows

    def _list_ui(self) -> ToolResult:
        windows = self._get_ui_windows()
        if not windows:
            return ToolResult(success=True, output="No visible UI applications are running.")
            
        unique_procs = {}
        for w in windows:
            # Clean up discord.exe to Discord, for example
            p = w["process"].replace(".exe", "").capitalize()
            if p not in unique_procs:
                unique_procs[p] = []
            unique_procs[p].append(w["title"])
            
        summary = "Active UI Applications:\n"
        for proc, titles in unique_procs.items():
            count = len(titles)
            summary += f"- {proc} ({count} window{'s' if count > 1 else ''} open)\n"
            
        return ToolResult(success=True, output=summary.strip())

    def _kill_process(self, target: str) -> ToolResult:
        """
        Kill any process whose name includes the target string.
        """
        # Ensure we don't accidentally kill critical things if target is too short
        if len(target) < 3:
            return ToolResult(success=False, error="Target name too short. Be more specific.")

        killed = 0
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name'].lower()
                if target in name:
                    # Do not kill fundamental Windows processes
                    safe_list = ["explorer.exe", "svchost.exe", "system", "cmd.exe", "powershell.exe"]
                    if name in safe_list:
                        continue
                        
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
                
        if killed > 0:
            logger.info(f"Killed {killed} processes matching '{target}'")
            return ToolResult(success=True, output=f"Forcefully closed {killed} processes matching '{target}'.")
        else:
            return ToolResult(success=False, error=f"Could not find any process matching '{target}' to kill.")

    def _focus_window(self, target: str) -> ToolResult:
        """
        Bring the first window matching the target process name or title to the foreground.
        """
        windows = self._get_ui_windows()
        
        target_hwnd = None
        target_title = ""
        
        # 1. Try exact or partial process name match (e.g. "brave" -> "brave.exe")
        for w in windows:
            if target in w["process"]:
                target_hwnd = w["hwnd"]
                target_title = w["title"]
                break
                
        # 2. If no process match, try partial title match
        if not target_hwnd:
            for w in windows:
                if target in w["title"].lower():
                    target_hwnd = w["hwnd"]
                    target_title = w["title"]
                    break
                    
        if not target_hwnd:
            return ToolResult(success=False, error=f"Could not find any open window for '{target}'.")
            
        try:
            # Wake up screen/UI queue if necessary using Alt key trick
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(target_hwnd)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            
            # Unminimize if minimized
            if win32gui.IsIconic(target_hwnd):
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                
            return ToolResult(success=True, output=f"Focused window: {target_title}")
        except Exception as e:
            return ToolResult(success=False, error=f"Found window but failed to focus: {e}")
