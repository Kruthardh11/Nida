# tools/browser/hotstar.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Hotstar-Specific Controller
#
# Handles:
#   - login_flow  : Navigate to Hotstar, enter phone number, announce OTP wait
#   - search_play : Search Hotstar and click the first content card
#
# Selector strategy:
#   Hotstar uses React with dynamic classnames. We use attribute/role/aria
#   selectors which are far more stable than generated class hashes.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import logging
import time
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger("nida.hotstar")

# Stable selectors for Hotstar's UI (tested as of 2025)
# These use data-testid / aria / input type — resistant to class-name churn
_SEL_LOGIN_BUTTON     = "[data-testid='login'], a[href*='login'], button:has-text('Log in'), button:has-text('Login'), button:has-text('Sign in')"
_SEL_PHONE_INPUT      = "input[type='tel'], input[placeholder*='phone' i], input[placeholder*='mobile' i], input[name*='phone' i]"
_SEL_CONTINUE_BUTTON  = "button:has-text('Continue'), button:has-text('Get OTP'), button[type='submit']"
_SEL_FIRST_CARD       = (
    # Hotstar content cards in order of reliability
    "[data-testid='title-card'] a, "
    "a[href*='/movies/'], "
    "a[href*='/shows/'], "
    "a[href*='/sports/'], "
    "[class*='card'] a[href*='/watch/'], "
    "[class*='item'] a[href*='/watch/']"
)


class HotstarController:
    """
    Controls Hotstar-specific flows in the active browser page.
    All methods return a dict: {ok: bool, feedback: str, error?: str}
    """

    def __init__(self, page: "Page"):
        self._page = page

    def _ok(self, feedback: str) -> dict:
        return {"ok": True, "feedback": feedback}

    def _fail(self, feedback: str, error: str = "") -> dict:
        return {"ok": False, "feedback": feedback, "error": error or feedback}

    def _handle_profile(self) -> bool:
        """Checks if the 'Kruthardh' profile is visible and clicks it."""
        try:
            loc = self._page.locator("text='Kruthardh'")
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                logger.info("Clicked 'Kruthardh' profile")
                time.sleep(2)
                return True
        except Exception:
            pass
        return False

    # ── Login flow ───────────────────────────────────────────────────────────

    def login_flow(self, phone: str) -> dict:
        """
        Navigates to Hotstar. If logged in, selects Kruthardh.
        Otherwise clicks Log In, enters the phone number, clicks Continue,
        then Nida announces she's waiting for the user to complete OTP manually.
        """
        try:
            # 1. Make sure we're on hotstar
            current = self._page.url.lower()
            if "hotstar" not in current:
                self._page.goto("https://www.hotstar.com")
                self._page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(2)

            if self._handle_profile():
                return self._ok("You were already logged in, so I selected the Kruthardh profile.")

            # 2. Click the Login button
            try:
                self._page.wait_for_selector(_SEL_LOGIN_BUTTON, timeout=5000)
                self._page.locator(_SEL_LOGIN_BUTTON).first.click()
                logger.info("Clicked Hotstar login button")
            except Exception:
                # Maybe already on login page
                logger.info("Login button not found — may already be on login page")

            # 3. Enter phone number
            self._page.wait_for_selector(_SEL_PHONE_INPUT, timeout=8000)
            phone_box = self._page.locator(_SEL_PHONE_INPUT).first
            phone_box.click()
            phone_box.fill("")          # clear any existing content
            phone_box.type(phone, delay=80)   # type naturally, not fill() — avoids JS events missing
            logger.info(f"Entered phone number: {phone[:4]}****")

            # 4. Click Continue / Get OTP
            try:
                self._page.wait_for_selector(_SEL_CONTINUE_BUTTON, timeout=4000)
                self._page.locator(_SEL_CONTINUE_BUTTON).first.click()
                logger.info("Clicked Continue / Get OTP")
            except Exception:
                # Try pressing Enter as fallback
                phone_box.press("Enter")

            return self._ok(
                "I've entered your number and requested the OTP. "
                "Please check your phone and enter the code to complete login."
            )

        except Exception as e:
            logger.error(f"Hotstar login flow error: {e}")
            return self._fail(
                "I ran into a problem during the Hotstar login. "
                "You may need to log in manually.",
                str(e)
            )

    # ── Search and auto-play first result ────────────────────────────────────

    def search_and_play(self, query: str) -> dict:
        """
        Navigates directly to the Hotstar search URL and clicks the first card.
        """
        try:
            query_escaped = urllib.parse.quote_plus(query)
            search_url = f"https://www.hotstar.com/in/explore?search_query={query_escaped}"
            self._page.goto(search_url)
            self._page.wait_for_load_state("domcontentloaded", timeout=10000)
            time.sleep(2)

            # Might have been redirected to profile selection page
            self._handle_profile()

            # If we were redirected and clicked profile, we may no longer be on the search page
            if "search_query" not in self._page.url.lower():
                self._page.goto(search_url)
                self._page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(2)

            # Wait for results and click the first card
            try:
                self._page.wait_for_selector(_SEL_FIRST_CARD, timeout=7000)
                self._page.locator(_SEL_FIRST_CARD).first.click()
                logger.info(f"Clicked first Hotstar result for '{query}'")
                return self._ok(f"Playing {query} on Hotstar.")

            except Exception as click_err:
                logger.warning(f"Could not auto-click first Hotstar result: {click_err}")
                return self._ok(
                    f"Searched Hotstar for {query}. "
                    "I found results but couldn't auto-open the first one — "
                    "click it to start watching."
                )

        except Exception as e:
            logger.error(f"Hotstar search_and_play error: {e}")
            return self._fail(
                "I had trouble searching on Hotstar. "
                "Make sure you're logged in and the page is loaded.",
                str(e)
            )
