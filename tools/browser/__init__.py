# tools/browser/__init__.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Browser Controller Tool (Package Entry Point)
#
# This file is the direct continuation of the original tools/browser.py.
# It exports BrowserTool and BrowserSession so all existing imports
# (`from tools.browser import BrowserTool`) continue to work unchanged.
#
# Tier 3 (Media Control) is injected here via MediaController.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import subprocess
import time
import ctypes
import requests
import win32gui
import win32con
from playwright.sync_api import sync_playwright, Browser, BrowserContext

from tools.base import BaseTool, ToolResult
from tools.browser.media_controller import MediaController
from tools.browser.hotstar import HotstarController
from tools.browser.page_reader import PageReader
from tools.browser.autofiller import AutofillerController
from config.settings import HOTSTAR_PHONE

logger = logging.getLogger("nida.browser")


def force_restore_brave():
    """
    Uses Win32 thread-input attachment to reliably bring Brave to the
    foreground — works even when Brave is minimised or behind other windows.
    Standard SetForegroundWindow() fails from background processes;
    AttachThreadInput() unlocks it.
    """
    user32  = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    def callback(hwnd, extra):
        title = win32gui.GetWindowText(hwnd)
        if "Brave" in title or "Chrome" in title:
            # Un-minimise first
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            # Attach our thread to the foreground window's thread,
            # then force-set foreground, then detach.
            fg_hwnd   = user32.GetForegroundWindow()
            our_tid   = kernel32.GetCurrentThreadId()
            fg_tid    = user32.GetWindowThreadProcessId(fg_hwnd, None)
            if fg_tid and fg_tid != our_tid:
                user32.AttachThreadInput(our_tid, fg_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            if fg_tid and fg_tid != our_tid:
                user32.AttachThreadInput(our_tid, fg_tid, False)
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception as e:
        logger.debug(f"force_restore_brave: {e}")


CDP_URL = "http://localhost:9222"


def launch_brave_with_cdp() -> str | None:
    """
    Checks if Brave's CDP port is already open.
    If not, kills existing Brave instances and relaunches with
    --remote-debugging-port=9222 so Playwright can attach.
    Returns an error string if it ultimately cannot start Brave, or None on success.
    """
    # Quick check — is CDP already up?
    try:
        r = requests.get(f"{CDP_URL}/json/version", timeout=1)
        if r.status_code == 200:
            return None   # already running correctly
    except Exception:
        pass

    # Kill any existing Brave instances (they won't have the flag)
    try:
        subprocess.run(["taskkill", "/F", "/IM", "brave.exe"], capture_output=True)
        time.sleep(1.5)   # wait for process to fully die
    except Exception:
        pass

    # Common Brave installation paths on Windows
    brave_paths = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    brave_exe = next((p for p in brave_paths if __import__('os').path.exists(p)), None)
    if not brave_exe:
        return "Could not find Brave installed. Please open Brave manually with the debug flag."

    try:
        subprocess.Popen([
            brave_exe,
            f"--remote-debugging-port=9222",
            "--no-first-run",
        ])
        logger.info("Launched Brave with --remote-debugging-port=9222")
        # Give it 3 seconds to spin up
        for _ in range(10):
            time.sleep(0.5)
            try:
                r = requests.get(f"{CDP_URL}/json/version", timeout=1)
                if r.status_code == 200:
                    return None
            except Exception:
                continue
        return "Brave launched but CDP port didn't become ready in time. Try again."
    except Exception as e:
        return f"Failed to launch Brave: {e}"



class BrowserSession:
    """
    Singleton manager for the Playwright CDP connection.
    Ensures we only spin up the bridge once, and gracefully detects
    if Brave is not running with the debug flag.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BrowserSession, cls).__new__(cls)
            cls._instance._init_session()
        return cls._instance

    def _init_session(self):
        self.p = None
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.connected = False
        self.recent_page = None   # tracks the last page Nida touched

    def connect(self) -> str | None:
        """
        Attempts to connect to the active browser via CDP.
        Returns an error string if it fails, or None if successful.
        """
        if self.connected:
            try:
                if self.browser and self.browser.is_connected():
                    return None
            except Exception:
                pass
            # Connection died, force cleanup
            self.shutdown()

        try:
            r = requests.get(f"{CDP_URL}/json/version", timeout=1)
            r.raise_for_status()
        except requests.exceptions.RequestException:
            return (
                "Brave is not running with the CDP debugging port open. "
                "Close all Brave instances and relaunch with `--remote-debugging-port=9222`."
            )

        try:
            self.p = sync_playwright().start()
            self.browser = self.p.chromium.connect_over_cdp(CDP_URL, timeout=30000)
            self.context = self.browser.contexts[0]
            self.connected = True
            logger.info("Successfully connected to Brave via CDP.")
            return None
        except Exception as e:
            logger.error(f"Playwright CDP Connection Error: {e}")
            if self.p:
                self.p.stop()
                self.p = None
            return f"Failed to attach Playwright to Brave: {str(e)}"

    def shutdown(self):
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
            
        try:
            if self.p:
                self.p.stop()
        except Exception:
            pass
            
        self.connected = False

    def active_page(self):
        """
        Returns the page the user is currently looking at.
        Playwright auto-spoofs visibilityState/hasFocus in CDP, making all tabs
        look "active". Instead, we query the actual OS window titles. Brave sets
        its main window title to match the active tab's title.
        """
        pages = self.context.pages if self.context else []
        if not pages:
            return None

        import win32gui

        active_titles = []
        def enum_windows(hwnd, lparam):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if " - Brave" in title or " - Google Chrome" in title:
                    # Strip the browser suffix to get the raw tab title
                    clean = title.replace(" - Brave", "").replace(" - Google Chrome", "").strip()
                    active_titles.append(clean)

        win32gui.EnumWindows(enum_windows, None)

        if active_titles:
            # Look for exact or partial match of the page title in the active OS window
            for page in pages:
                try:
                    pt = page.title().strip()
                    if any(pt in at or at in pt for at in active_titles if pt):
                        return page
                except Exception:
                    continue

        # Fallback to the last opened tab if OS title matching fails
        return pages[-1]


class BrowserTool(BaseTool):
    """
    Orchestrates the active browser natively via CDP.
    Tier 1: navigate / close_tab
    Tier 2: search  (Google, YouTube auto-play, generic DOM heuristic)
    Tier 3: media   (play, pause, seek, volume, fullscreen, pip…)
    """

    def __init__(self):
        self.session = BrowserSession()
        super().__init__()

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control the active browser (Brave/Chrome): navigate tabs, search the web, "
            "or control media playback (play, pause, seek, volume, fullscreen)."
        )

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action":  "status | navigate | search | close_tab | media | hotstar_login | hotstar_search | launch_brave | read | read_control | google_result | autofill | google_login",
            "target":  "URL, search query, or site domain",
            "engine":  "(search only) google | youtube | youtube_play | <domain.com>",
            "command": "(media/read_control) pause | play | seek_forward | mute | pip | summarise | skip | stop ...",
            "seconds": "(seek only) integer number of seconds",
            "level":   "(volume_set only) float 0.0–1.0",
            "index":   "(google_result only) integer index of the search result to click (1 = first, 2 = second...)",
        }

    def run(self, args: dict) -> ToolResult:
        action  = args.get("action", "").strip().lower().rstrip("}\"'/")
        target  = args.get("target", "").strip()
        engine  = args.get("engine", "google").strip().lower()
        command = args.get("command", "").strip().lower()
        index   = int(args.get("index", 1))

        # Handle flat aliases that LLMs often hallucinate instead of nested commands
        if action == "summarise" or action == "summarize":
            action = "read_control"
            command = "summarise"

        # launch_brave is special — can run before CDP is up
        if action == "launch_brave":
            err = launch_brave_with_cdp()
            if err:
                return ToolResult(success=False, error=err)
            # Reset the singleton so it reconnects
            BrowserSession._instance = None
            return ToolResult(success=True, output="Brave is up and the debugging port is open. Ready.")

        err = self.session.connect()
        if err:
            # Try to auto-launch Brave before giving up
            launch_err = launch_brave_with_cdp()
            if launch_err:
                return ToolResult(success=False, error=launch_err)
            BrowserSession._instance = None
            self.session = BrowserSession()
            err = self.session.connect()
            if err:
                return ToolResult(success=False, error=err)

        if action == "status":
            n = len(self.session.context.pages)
            return ToolResult(success=True, output=f"Connected. {n} tab{'s' if n != 1 else ''} open.")

        elif action == "navigate":
            return self._navigate(target)

        elif action == "search":
            return self._search(target, engine)

        elif action == "close_tab":
            return self._close_tab(target)

        elif action in ["media", "media_control", "playback"]:
            return self._media(command, args)

        elif action == "hotstar_login":
            return self._hotstar_login()

        elif action == "hotstar_search":
            return self._hotstar_search(target)

        elif action == "read":
            return self._read()
            
        elif action == "read_control":
            return self._read_control(command)
            
        elif action == "google_result":
            return self._google_result(index)
            
        elif action == "autofill":
            return self._autofill()

        elif action == "google_login":
            return self._google_login(target)

        return ToolResult(
            success=False,
            error=f"Unknown browser action: '{action}'."
        )

    # ── Tier 1: Navigation ──────────────────────────────────────────────────

    def _navigate(self, target: str) -> ToolResult:
        import urllib.parse
        if not target:
            return ToolResult(success=False, error="No target URL provided.")

        force_restore_brave()

        url = target.lower()
        if not url.startswith("http") and not url.startswith("file://"):
            if "." not in url and not url.startswith("localhost"):
                url = f"{url}.com"
            url = f"https://{url}"

        parsed_target = urllib.parse.urlparse(url)
        target_domain = parsed_target.netloc.replace("www.", "")
        target_path   = parsed_target.path.strip("/")

        for page in self.session.context.pages:
            try:
                parsed_page = urllib.parse.urlparse(page.url.lower())
                page_domain = parsed_page.netloc.replace("www.", "")
                page_path   = parsed_page.path.strip("/")

                if target_domain == page_domain or page_domain.endswith(f".{target_domain}"):
                    if not target_path or target_path in page_path:
                        page.bring_to_front()
                        self.session.recent_page = page
                        logger.info(f"Switched to existing tab: {page.url}")
                        return ToolResult(success=True, output=f"Brought existing {target} tab to the front.")
            except Exception:
                pass

        try:
            page = self.session.context.new_page()
            page.goto(url)
            page.bring_to_front()
            self.session.recent_page = page
            logger.info(f"Opened new tab: {url}")
            return ToolResult(success=True, output=f"Opened {target} in a new tab.")
        except Exception as e:
            return ToolResult(success=False, error=f"Navigation failed: {str(e)}")

    def _close_tab(self, target: str = "") -> ToolResult:
        """Closes the active visible tab, or a specific tab if target is provided."""
        try:
            pages = self.session.context.pages
            if not pages:
                return ToolResult(success=False, error="No tabs are open.")

            if target:
                clean_target = target.replace("www.", "").lower()
                pages = [p for p in pages if clean_target in p.url.lower()]
                if not pages:
                    return ToolResult(success=False, error=f"No open tab matching '{target}'.")

            front_page = None

            # 1. Nida's last-touched page
            recent = self.session.recent_page
            if recent and recent in pages:
                try:
                    if recent.evaluate("() => document.visibilityState === 'visible'"):
                        front_page = recent
                except Exception:
                    pass

            # 2. Visible tab, newest first
            if not front_page:
                for page in reversed(pages):
                    try:
                        if page.evaluate("() => document.visibilityState === 'visible'"):
                            front_page = page
                            break
                    except Exception:
                        continue

            # 3. Absolute newest
            if not front_page:
                front_page = pages[-1]

            front_page.close()
            return ToolResult(success=True, output=f"Closed the {target or 'active'} tab.")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to close tab: {str(e)}")

    # ── Tier 2: Search ──────────────────────────────────────────────────────

    def _search(self, query: str, engine: str) -> ToolResult:
        import urllib.parse
        if not query:
            return ToolResult(success=False, error="No search query provided.")

        force_restore_brave()

        try:
            page = self.session.context.new_page()
            self.session.recent_page = page

            if engine in ["google", "youtube", "youtube_play"]:
                query_escaped = urllib.parse.quote_plus(query)

                if engine == "google":
                    page.goto(f"https://www.google.com/search?q={query_escaped}")
                    page.bring_to_front()
                    logger.info(f"Fast Google search: '{query}'")
                    return ToolResult(success=True, output=f"Searching Google for {query}.")

                elif engine.startswith("youtube"):
                    page.goto(f"https://www.youtube.com/results?search_query={query_escaped}")
                    page.bring_to_front()

                    if engine == "youtube_play":
                        try:
                            # YouTube uses various wrappers for search results depending on AB tests (ytd-video-renderer, ytd-rich-grid-media, etc)
                            selector = "ytd-video-renderer a#video-title, ytd-rich-grid-media a#video-title-link, a#thumbnail[href^='/watch']"
                            page.wait_for_selector(selector, timeout=8000)
                            
                            # Real user pointer emulation ensures proper client-side routing
                            loc = page.locator(selector).first
                            loc.click(force=True)
                            
                            logger.info(f"Auto-playing first YT result: '{query}'")
                            return ToolResult(success=True, output=f"Playing {query} on YouTube.")
                        except Exception as click_err:
                            logger.warning(f"YT auto-click failed: {click_err}")
                            return ToolResult(success=True, output=f"Searched YouTube for {query}.")

                    return ToolResult(success=True, output=f"Searching YouTube for {query}.")

            # Generic DOM heuristic for any other site
            domain = engine if "." in engine else f"{engine}.com"
            url = f"https://www.{domain}"
            page.goto(url)
            page.bring_to_front()

            search_sel = "input[type='search'], input[name*='search' i], input[id*='search' i], input[placeholder*='search' i]"
            try:
                page.wait_for_selector(search_sel, timeout=5000)
                box = page.locator(search_sel).first
                box.fill(query)
                box.press("Enter")
                logger.info(f"DOM-heuristic search on {domain}: '{query}'")
                return ToolResult(success=True, output=f"Searching {domain} for {query}.")
            except Exception:
                return ToolResult(success=False, error=f"Could not find a search box on {domain}.")

        except Exception as e:
            return ToolResult(success=False, error=f"Search failed: {str(e)}")

    # ── Tier 3: Media Control ────────────────────────────────────────────────

    def _media(self, command: str, args: dict) -> ToolResult:
        """
        Delegates to MediaController, targeting the active page.
        Works even when Brave is minimised — CDP doesn't need window focus.
        """
        page = self.session.active_page()
        if page is None:
            return ToolResult(success=False, error="No browser tabs are open.")

        if not command:
            return ToolResult(success=False, error="No media command provided.")

        mc = MediaController(page)
        result = mc.dispatch(command, args)

        if result["ok"]:
            return ToolResult(success=True, output=result["feedback"])
        else:
            return ToolResult(
                success=False,
                output=result["feedback"],   # speak the friendly message
                error=result.get("error", "")
            )

    # ── Hotstar ──────────────────────────────────────────────────────────────

    def _hotstar_login(self) -> ToolResult:
        """
        Navigates to Hotstar, enters HOTSTAR_PHONE, triggers OTP,
        then announces that the user should complete OTP manually.
        """
        # Ensure we're on (or navigate to) Hotstar
        page = self.session.active_page()
        if page is None or "hotstar" not in page.url.lower():
            page = self.session.context.new_page()
            page.goto("https://www.hotstar.com")
            page.bring_to_front()
            self.session.recent_page = page

        force_restore_brave()
        hc = HotstarController(page)
        result = hc.login_flow(HOTSTAR_PHONE)

        if result["ok"]:
            return ToolResult(success=True, output=result["feedback"])
        return ToolResult(success=False, output=result["feedback"], error=result.get("error", ""))

    def _hotstar_search(self, query: str) -> ToolResult:
        """
        Searches Hotstar and auto-plays the first result.
        Navigates to Hotstar first if not already there.
        """
        if not query:
            return ToolResult(success=False, error="No search query provided for Hotstar.")

        page = self.session.active_page()
        if page is None or "hotstar" not in page.url.lower():
            page = self.session.context.new_page()
            page.goto("https://www.hotstar.com")
            page.bring_to_front()
            self.session.recent_page = page

        force_restore_brave()
        hc = HotstarController(page)
        result = hc.search_and_play(query)

        if result["ok"]:
            return ToolResult(success=True, output=result["feedback"])
        return ToolResult(success=False, output=result["feedback"], error=result.get("error", ""))


    # ── Tier 4: Page Reader & Extraction ─────────────────────────────────────

    def _read(self) -> ToolResult:
        page = self.session.active_page()
        if not page:
            return ToolResult(success=False, error="No active page found to read.")
        reader = PageReader(page)
        res = reader.execute("read", {})
        return ToolResult(success=res["ok"], output=res.get("feedback", res.get("error", "")))

    def _read_control(self, command: str) -> ToolResult:
        page = self.session.active_page()
        if not page:
            return ToolResult(success=False, error="No active page found.")
            
        reader = PageReader(page)
        # Map our internal command naming to the new execute() dispatch schema
        action_map = {
            "pause": "read_pause",
            "resume": "read_resume",
            "stop": "read_stop",
            "skip": "read_skip",
            "repeat": "read_repeat",
            "summarise": "summarise",
            "author": "author",
            "date": "date",
            "reading_time": "reading_time"
        }
        mapped = action_map.get(command)
        if not mapped:
            return ToolResult(success=False, error=f"Unknown read control: {command}")
            
        res = reader.execute(mapped, {"style": "bullets"})
        return ToolResult(success=res["ok"], output=res.get("feedback", res.get("error", "")))

    def _google_result(self, index: int) -> ToolResult:
        page = self.session.active_page()
        if not page:
            return ToolResult(success=False, error="No active page found.")
        reader = PageReader(page)
        res = reader.execute("google_result", {"index": index})
        return ToolResult(success=res["ok"], output=res.get("feedback", res.get("error", "")))

    def _autofill(self) -> ToolResult:
        page = self.session.active_page()
        if not page:
            return ToolResult(success=False, error="No active page found.")
            
        af = AutofillerController(page)
        res = af.fill_form()
        return ToolResult(success=res["ok"], output=res.get("feedback", res.get("error", "")))

    def _google_login(self, target: str) -> ToolResult:
        """
        Navigates to the target (if provided and we are not already there),
        looks for a Google login button, clicks it, catches the popup,
        and selects the first Google account.
        """
        # 1. Navigation
        if target:
            # We use the existing _navigate helper to go to the site if we aren't there
            nav_result = self._navigate(target)
            if not nav_result.success:
                return nav_result

        page = self.session.active_page()
        if not page:
            return ToolResult(success=False, error="No active page found.")

        force_restore_brave()

        # 2. Look for Google SSO Button
        google_button_selectors = [
            "button:has-text('Continue with Google')",
            "button:has-text('Sign in with Google')",
            "button:has-text('Log in with Google')",
            "button:has-text('Login with Google')",
            "a:has-text('Continue with Google')",
            "a:has-text('Sign in with Google')",
            "a:has-text('Log in with Google')",
            "a:has-text('Login with Google')",
            "[aria-label*='Google' i]",
            "[data-test*='google' i]",
            ".google-login",
            "#googleLogin",
            "img[alt*='Google' i]",  # clicking the google icon often triggers it
            "div:has-text('Continue with Google')",
            "span:has-text('Continue with Google')"
        ]

        button_found = False
        try:
            # First try without waiting, to see if it's already there
            for sel in google_button_selectors:
                if page.locator(sel).count() > 0:
                    btn = page.locator(sel).first
                    if btn.is_visible():
                        logger.info(f"Found Google login button using selector: {sel}")
                        
                        # Sometimes popups are blocked or open new tabs
                        try:
                            with self.session.context.expect_page(timeout=5000) as popup_info:
                                btn.click(force=True)
                            popup = popup_info.value
                            button_found = True
                            break
                        except Exception as e:
                            logger.debug(f"No popup caught, maybe it redirected in-place? {e}")
                            # If no popup, assume it redirected in the same tab. We'll handle that next.
                            popup = page
                            button_found = True
                            break
        except Exception as e:
            logger.debug(f"Click failed: {e}")
            
        if not button_found:
            return ToolResult(success=False, error="Could not find a Google login button on this page.")

        # 3. Handle the Google Accounts Selection
        try:
            logger.info("Waiting for Google accounts selection to load...")
            popup.wait_for_load_state("networkidle", timeout=10000)
            
            # The standard Google account chooser item selector
            account_selectors = [
                ".vxx8jf",            # standard google account list item
                "[data-email]",       # sometimes used
                "ul li div[role='link']", 
                "ul li",
                "div:has-text('@gmail.com')",
                "div[data-identifier]" # modern oauth screen
            ]
            
            account_clicked = False
            for sel in account_selectors:
                if popup.locator(sel).count() > 0:
                    acc = popup.locator(sel).first
                    acc.click()
                    account_clicked = True
                    logger.info("Clicked first Google account.")
                    break
                    
            if not account_clicked:
                return ToolResult(success=False, output="Google popup opened, but I couldn't automatically select your account. Please click your account manually.", error="Google account selection failed.")
                
            return ToolResult(success=True, output="Successfully clicked the Google login button and selected your account.")
            
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to interact with Google accounts popup: {e}")

