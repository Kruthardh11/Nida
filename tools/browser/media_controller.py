# tools/browser/media_controller.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Tier 3 Media Controller
#
# Injects JavaScript over CDP into the active browser tab to control any
# HTML5 video element — YouTube, Netflix, Twitch, local files, anything.
#
# Design decisions:
#   - All JS is evaluated synchronously via page.evaluate() — total round-trip
#     is under 300ms even on cold first-call because we're not doing network I/O.
#   - We do NOT use document.querySelector('video') blindly. The _get_video_js()
#     helper implements a 3-priority strategy so we always target the right video
#     in pages with multiple <video> tags (ads, thumbnails, etc).
#   - Volume actions affect ONLY the video element, not system volume.
#   - All feedback strings are written as natural speech for TTS.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger("nida.media")


# ── Video selector strategy ──────────────────────────────────────────────────
# Returned as a JS string fragment — embed this inside any evaluate() call.
# Priority:
#   0. Explicit overrides for hard platforms (YouTube)
#   1. A video that is currently playing (paused === false)
#   2. The video with the longest duration (main content, not ads)
#   3. The video with the largest visible area (offsetWidth * offsetHeight)
#   4. Throws a descriptive error so the caller can surface it cleanly.
_VIDEO_SELECTOR_JS = """
(function _findVideo() {
    // Priority 0: Explicit YouTube main video override
    const ytVideo = document.querySelector('.html5-main-video');
    if (ytVideo) return ytVideo;

    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');

    // Priority 1: currently playing
    const playing = all.find(v => !v.paused && !v.ended && v.readyState > 2);
    if (playing) return playing;

    // Priority 2: longest duration (filters out 0-duration ad placeholders)
    const withDuration = all.filter(v => v.duration > 0 && !isNaN(v.duration));
    if (withDuration.length) {
        return withDuration.reduce((a, b) => a.duration > b.duration ? a : b);
    }

    // Priority 3: largest visible area
    const visible = all.filter(v => v.offsetWidth > 0 && v.offsetHeight > 0);
    if (visible.length) {
        return visible.reduce((a, b) =>
            (a.offsetWidth * a.offsetHeight) > (b.offsetWidth * b.offsetHeight) ? a : b
        );
    }

    // Fallback: Just return the first video found
    return all[0];
})()
"""


class MediaController:
    """
    Controls HTML5 video elements in the active browser page via CDP JS injection.

    All public methods return a dict with keys:
        ok       bool    — True on success
        feedback str     — TTS-ready human speech string
        error    str     — (only on failure) machine-readable cause
    """

    def __init__(self, page: "Page"):
        self._page = page

    def _find_video_frame(self):
        """
        Hotstar, Disney+, and similar services embed their video player
        inside a child iframe. Standard page.evaluate() only runs in the
        top-level document and will throw 'No video element found'.

        This method scans every frame on the page (BFS) and returns the
        first frame that contains at least one <video> element.
        Falls back to the top-level page if none of the frames have video.
        """
        # Try top-level document first (YouTube, etc.)
        try:
            count = self._page.evaluate("document.querySelectorAll('video').length")
            if count > 0:
                return self._page
        except Exception:
            pass

        # Walk all frames (iframes) — Hotstar embeds player in a child frame
        for frame in self._page.frames:
            if frame == self._page.main_frame:
                continue  # already checked above
            try:
                count = frame.evaluate("document.querySelectorAll('video').length")
                if count > 0:
                    logger.debug(f"Found video in sub-frame: {frame.url[:60]}")
                    return frame
            except Exception:
                continue

        # Default back to page — let the JS throw its own descriptive error
        return self._page

    def _eval(self, js: str):
        """
        Runs JS in the correct frame context (auto-detects iframe for Hotstar).
        Raises on evaluate() error — caller must catch.
        """
        frame = self._find_video_frame()
        if hasattr(frame, 'evaluate'):
            return frame.evaluate(js)
        return self._page.evaluate(js)

    def _video_js(self, expression: str) -> str:
        """
        Wraps an expression that uses `video` variable with the selector strategy.
        Usage: self._video_js("video.pause()")
        """
        return f"""
(function() {{
    const video = {_VIDEO_SELECTOR_JS};
    {expression}
}})()
"""

    def _ok(self, feedback: str) -> dict:
        return {"ok": True, "feedback": feedback}

    def _fail(self, feedback: str, error: str = "") -> dict:
        return {"ok": False, "feedback": feedback, "error": error or feedback}

    def _run(self, expression: str, feedback: str) -> dict:
        """
        Convenience: run a JS expression that doesn't return a value,
        return ok with the provided feedback string.
        """
        try:
            self._eval(self._video_js(expression))
            return self._ok(feedback)
        except Exception as e:
            err = str(e)
            logger.error(f"MediaController JS error: {err}")
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Something went wrong with the media control.", err)



    # ── PLAYBACK ─────────────────────────────────────────────────────────────

    def pause(self) -> dict:
        return self._run("video.pause();", "Paused.")

    def play(self) -> dict:
        return self._run("video.play();", "Playing.")

    def restart(self) -> dict:
        return self._run("video.currentTime = 0; video.play();", "Restarting from the beginning.")

    # ── SEEK ─────────────────────────────────────────────────────────────────

    def seek_forward(self, seconds: int = 10) -> dict:
        js = f"video.currentTime = Math.min(video.duration, video.currentTime + {seconds});"
        return self._run(js, f"Skipped forward {seconds} seconds.")

    def seek_backward(self, seconds: int = 10) -> dict:
        js = f"video.currentTime = Math.max(0, video.currentTime - {seconds});"
        return self._run(js, f"Skipped back {seconds} seconds.")

    def seek_to(self, seconds: int = 0) -> dict:
        js = f"video.currentTime = Math.max(0, Math.min(video.duration, {seconds}));"
        return self._run(js, f"Jumped to {seconds} seconds.")

    # ── NAVIGATION ───────────────────────────────────────────────────────────

    def next(self) -> dict:
        """Tries the YouTube next-button. Falls back to jumping to near-end of video."""
        js = """
(function() {
    const btn = document.querySelector('.ytp-next-button');
    if (btn) { btn.click(); return 'next_button'; }
    const video = """ + _VIDEO_SELECTOR_JS + """;
    video.currentTime = video.duration - 0.1;
    return 'seek_end';
})()
"""
        try:
            result = self._eval(js)
            if result == "next_button":
                return self._ok("Going to the next video.")
            return self._ok("Jumped to the end of the video.")
        except Exception as e:
            err = str(e)
            logger.error(f"next() error: {err}")
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't navigate to the next video.", err)

    def previous(self) -> dict:
        """Navigates back in browser history — works for YouTube playlists, etc."""
        try:
            self._eval("history.back();")
            return self._ok("Going back.")
        except Exception as e:
            return self._fail("Couldn't go back.", str(e))

    # ── AUDIO ────────────────────────────────────────────────────────────────

    def volume_up(self) -> dict:
        js = """
(function() {
    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');
    const video = all.find(v => !v.paused) || all.reduce((a,b) => a.duration>b.duration?a:b, all[0]);
    if (video.volume >= 1.0) return -1;
    video.volume = Math.min(1.0, parseFloat((video.volume + 0.1).toFixed(2)));
    return Math.round(video.volume * 100);
})()
"""
        try:
            result = self._eval(js)
            if result == -1:
                return self._fail("Already at full volume.")
            return self._ok(f"Volume is now {result} percent.")
        except Exception as e:
            err = str(e)
            logger.error(f"volume_up() error: {err}")
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't adjust the volume.", err)

    def volume_down(self) -> dict:
        js = """
(function() {
    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');
    const video = all.find(v => !v.paused) || all.reduce((a,b) => a.duration>b.duration?a:b, all[0]);
    if (video.volume <= 0.0) return -1;
    video.volume = Math.max(0.0, parseFloat((video.volume - 0.1).toFixed(2)));
    return Math.round(video.volume * 100);
})()
"""
        try:
            result = self._eval(js)
            if result == -1:
                return self._fail("Volume is already at zero.")
            return self._ok(f"Volume is now {result} percent.")
        except Exception as e:
            err = str(e)
            logger.error(f"volume_down() error: {err}")
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't adjust the volume.", err)

    def volume_set(self, level: float = 0.5) -> dict:
        level = max(0.0, min(1.0, float(level)))
        js = f"""
(function() {{
    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');
    const video = all.find(v => !v.paused) || all[0];
    video.volume = {level:.2f};
    return Math.round(video.volume * 100);
}})()
"""
        try:
            result = self._eval(js)
            return self._ok(f"Volume set to {result} percent.")
        except Exception as e:
            err = str(e)
            logger.error(f"volume_set() error: {err}")
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't set the volume.", err)

    def mute(self) -> dict:
        return self._run("video.muted = true;", "Muted.")

    def unmute(self) -> dict:
        return self._run("video.muted = false;", "Unmuted.")

    def get_volume(self) -> dict:
        js = """
(function() {
    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');
    const video = all.find(v => !v.paused) || all[0];
    return { volume: Math.round(video.volume * 100), muted: video.muted };
})()
"""
        try:
            result = self._eval(js)
            muted_str = ", and it's muted" if result["muted"] else ""
            return self._ok(f"Volume is at {result['volume']} percent{muted_str}.")
        except Exception as e:
            err = str(e)
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't read the volume.", err)

    # ── DISPLAY ──────────────────────────────────────────────────────────────

    def fullscreen(self) -> dict:
        js = """
(function() {
    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');
    const video = all.find(v => !v.paused) || all[0];
    video.requestFullscreen();
})()
"""
        try:
            self._eval(js)
            return self._ok("Entering fullscreen.")
        except Exception as e:
            err = str(e)
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't enter fullscreen.", err)

    def exit_fullscreen(self) -> dict:
        try:
            self._eval("document.exitFullscreen();")
            return self._ok("Exiting fullscreen.")
        except Exception as e:
            return self._fail("Couldn't exit fullscreen.", str(e))

    def theatre_mode(self) -> dict:
        """YouTube-specific — silently ignores if button not found."""
        js = """
(function() {
    const btn = document.querySelector('.ytp-size-button');
    if (btn) { btn.click(); return true; }
    return false;
})()
"""
        try:
            found = self._eval(js)
            if found:
                return self._ok("Toggled theatre mode.")
            return self._fail("Theatre mode button wasn't found on this page.")
        except Exception as e:
            return self._fail("Couldn't toggle theatre mode.", str(e))

    def pip(self) -> dict:
        """Picture-in-Picture — works on Chrome/Brave with any HTML5 video."""
        js = """
(function() {
    const all = Array.from(document.querySelectorAll('video'));
    if (!all.length) throw new Error('No video element found on this page');
    const video = all.find(v => !v.paused) || all[0];
    video.requestPictureInPicture();
})()
"""
        try:
            self._eval(js)
            return self._ok("Picture in picture mode activated.")
        except Exception as e:
            err = str(e)
            if "No video element" in err:
                return self._fail("I don't see a video playing on this page.", err)
            return self._fail("Couldn't activate picture in picture.", err)

    # ── Router ───────────────────────────────────────────────────────────────

    def dispatch(self, action: str, args: dict) -> dict:
        """
        Central dispatcher. Maps action string → method call.
        Called by BrowserTool.run() when action == 'media'.
        """
        seconds = int(args.get("seconds", 10))
        level   = float(args.get("level", 0.5))

        dispatch_map = {
            # Playback
            "pause":          self.pause,
            "play":           self.play,
            "restart":        self.restart,
            # Seek
            "seek_forward":   lambda: self.seek_forward(seconds),
            "seek_backward":  lambda: self.seek_backward(seconds),
            "seek_to":        lambda: self.seek_to(seconds),
            # Navigation
            "next":           self.next,
            "previous":       self.previous,
            # Audio
            "volume_up":      self.volume_up,
            "volume_down":    self.volume_down,
            "volume_set":     lambda: self.volume_set(level),
            "mute":           self.mute,
            "unmute":         self.unmute,
            "get_volume":     self.get_volume,
            # Display
            "fullscreen":     self.fullscreen,
            "exit_fullscreen":self.exit_fullscreen,
            "theatre_mode":   self.theatre_mode,
            "pip":            self.pip,
        }

        handler = dispatch_map.get(action)
        if not handler:
            return self._fail(
                f"I don't know how to do '{action}' on a video.",
                f"Unknown media action: {action}"
            )

        return handler()
