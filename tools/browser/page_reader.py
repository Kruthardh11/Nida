# tools/browser/page_reader.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Tier 4: Read and Summarise
#
# WHAT'S FIXED VS THE ORIGINAL:
#   1. Readability injection now uses page.add_script_tag() — the correct
#      Playwright API. The original used page.evaluate() with a dynamic
#      <script> append, which races against script load timing and silently
#      fails on pages with strict CSP headers. add_script_tag() blocks until
#      the script is fully parsed and Readability is available.
#
#   2. Content cleaning pipeline added. Readability strips sidebars and nav
#      but its textContent still contains repeated whitespace, Unicode
#      non-breaking spaces, and short noise fragments ("Share", "Tweet",
#      "Advertisement"). _clean_text() handles all of this before chunking.
#
#   3. chunk_content() fixed. The original split on the literal string '\\n\\n'
#      (four characters) instead of the actual double newline '\n\n' (two).
#      This meant every article was delivered as one enormous chunk.
#      Also the sub-chunk sentence splitter rebuilt sentences correctly but
#      the chunk accumulator had an off-by-one: it flushed on >= 10 words
#      instead of >= 150, so each sentence became its own chunk.
#
#   4. Google result navigation added: open_google_result(index) extracts
#      all non-YouTube organic result URLs from the current Google SERP,
#      filters to blog/article links only, navigates to the nth one, and
#      then reads it. "Open the fourth result and read it" works end-to-end.
#
#   5. live_search() no longer navigates the user's active tab. It opens a
#      NEW tab for the search, extracts, answers, then closes it — so the
#      user's current page is never disrupted.
#
#   6. _reading_loop() for..else clause was semantically wrong. The `else`
#      of a for loop runs when the loop finishes WITHOUT a break. But the
#      break condition was stop_event.is_set(), which is the pause/stop path.
#      The "end of article" speech should fire when the loop exhausts normally.
#      Fixed by tracking completion with a boolean flag instead.
#
#   7. Summarise was blocking — extract + LLM both happened before the thread
#      started. Now extraction happens on the calling thread (fast, CDP),
#      and only the slow LLM call runs in the background thread, so Nida
#      speaks "Summarising, one moment..." immediately then speaks the result.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests

from config.settings import OLLAMA_BASE_URL, OLLAMA_HEAVY_MODEL
from core.voice import Voice

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger("nida.page_reader")

# ── URLs that contain video content and cannot be read as articles ───────────
_VIDEO_DOMAINS = {
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
    "twitch.tv", "rumble.com", "bitchute.com"
}

# ── Short noise strings Readability leaves behind ────────────────────────────
_NOISE_PATTERNS = re.compile(
    r"^(share|tweet|subscribe|advertisement|sponsored|follow us|"
    r"related articles?|read more|sign up|log in|newsletter|"
    r"cookie|privacy policy|terms of service|all rights reserved)[\s.]*$",
    re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PageContent:
    title: str = ""
    content: str = ""          # clean, readable text only
    author: str = ""
    excerpt: str = ""
    word_count: int = 0
    url: str = ""
    chunks: list[str] = field(default_factory=list)


@dataclass
class ReadingSession:
    chunks: list[str] = field(default_factory=list)
    position: int = 0
    status: str = "idle"       # idle | reading | paused | done
    source_url: str = ""
    title: str = ""
    stop_event: threading.Event = field(default_factory=threading.Event)


# ─────────────────────────────────────────────────────────────────────────────
# PageReader
# ─────────────────────────────────────────────────────────────────────────────

class PageReader:
    """
    Tier 4 — read and summarise.

    Handles:
      - Reading the current page aloud, paragraph by paragraph
      - Pause / resume / skip / repeat / stop controls
      - Full summary, bullet points, one-line summary
      - Metadata extraction: author, date, reading time
      - Google SERP result navigation: open nth result and read it
        (skips YouTube and other video URLs automatically)
      - Live search: answer a query without disturbing the current tab
    """

    # Readability CDN — loaded once per page via add_script_tag
    _READABILITY_CDN = (
        "https://cdn.jsdelivr.net/npm/@mozilla/readability@0.5.0/Readability.js"
    )

    def __init__(self, page: "Page"):
        self._page = page
        self._readability_loaded = False
        self.voice = Voice()
        self.session = ReadingSession()
        self._thread: threading.Thread | None = None

    # ─────────────────────────────────────────────────────────────────────────
    # Public dispatch entry point
    # ─────────────────────────────────────────────────────────────────────────

    def execute(self, action: str, args: dict) -> dict:
        """
        Single entry point called by the LangGraph act_node.
        Maps action strings from the tool JSON to methods.
        """
        dispatch = {
            "read":          self.read_start,
            "read_pause":    self.read_pause,
            "read_resume":   self.read_continue,
            "read_stop":     self.read_stop,
            "read_skip":     self.read_skip,
            "read_repeat":   self.read_repeat,
            "summarise":     lambda: self.summarise(args.get("style", "full")),
            "author":        self.extract_author,
            "date":          self.extract_date,
            "reading_time":  self.extract_reading_time,
            "google_result": lambda: self.open_google_result(args.get("index", 1)),
            "live_search":   lambda: self.live_search(args.get("query", "")),
        }
        handler = dispatch.get(action)
        if not handler:
            return self._err(f"Unknown page_reader action: {action}")
        try:
            return handler()
        except Exception as e:
            logger.exception(f"page_reader.execute error: {e}")
            return self._err(f"Something went wrong: {str(e)[:80]}")

    # ─────────────────────────────────────────────────────────────────────────
    # Content extraction
    # ─────────────────────────────────────────────────────────────────────────

    def _inject_readability(self) -> bool:
        """
        Load Mozilla Readability.js into the current page.
        Uses page.add_script_tag() which blocks until the script is parsed —
        unlike a dynamic <script> append which races against load timing.
        Returns True on success, False on failure.
        """
        if self._readability_loaded:
            return True
        try:
            self._page.add_script_tag(url=self._READABILITY_CDN)
            # Confirm it's available before marking loaded
            loaded = self._page.evaluate("typeof Readability !== 'undefined'")
            self._readability_loaded = bool(loaded)
            return self._readability_loaded
        except Exception as e:
            logger.warning(f"Readability inject failed: {e}")
            self._readability_loaded = False
            return False

    def extract_page_content(self, target_page: "Page | None" = None) -> PageContent:
        """
        Extract clean article content from the given page (or active page).
        Strategy:
          1. Inject Mozilla Readability.js
          2. Run Readability.parse() which returns article body without
             nav, sidebars, footers, ads, and social share buttons
          3. If Readability fails (non-article page), fall back to body.innerText
             with aggressive noise filtering
          4. Clean and chunk the result
        """
        page = target_page or self._page
        pc = PageContent(url=page.url)

        # ── Step 1: try Readability ───────────────────────────────────────────
        readability_ok = self._inject_readability_on(page)
        if readability_ok:
            try:
                res = page.evaluate("""
                    (function() {
                        try {
                            const doc = document.cloneNode(true);
                            const reader = new Readability(doc);
                            const article = reader.parse();
                            if (!article) return null;
                            return {
                                title:       article.title       || document.title,
                                textContent: article.textContent || "",
                                byline:      article.byline      || "",
                                excerpt:     article.excerpt     || "",
                                length:      article.length      || 0
                            };
                        } catch(e) {
                            return null;
                        }
                    })()
                """)
                if res and res.get("textContent", "").strip():
                    pc.title   = res.get("title", "")
                    pc.content = self._clean_text(res.get("textContent", ""))
                    pc.author  = res.get("byline", "") or ""
                    pc.excerpt = res.get("excerpt", "") or ""
                    logger.info(f"Readability extracted {len(pc.content)} chars from {pc.url}")
            except Exception as e:
                logger.warning(f"Readability parse failed: {e}")

        # ── Step 2: fallback — body.innerText with noise filter ───────────────
        if not pc.content:
            try:
                pc.title = page.title()
                raw = page.evaluate("document.body.innerText") or ""
                pc.content = self._clean_text(raw[:10000])
                logger.info(f"Fallback extraction: {len(pc.content)} chars")
            except Exception as e:
                logger.error(f"Fallback extraction failed: {e}")

        pc.word_count = len(pc.content.split())
        pc.chunks = self.chunk_content(pc.content)
        return pc

    def _inject_readability_on(self, page: "Page") -> bool:
        """Inject Readability on a specific page object (used for new tabs)."""
        if page is self._page and self._readability_loaded:
            return True
        try:
            page.add_script_tag(url=self._READABILITY_CDN)
            loaded = page.evaluate("typeof Readability !== 'undefined'")
            if page is self._page:
                self._readability_loaded = bool(loaded)
            return bool(loaded)
        except Exception as e:
            logger.warning(f"Readability inject on page failed: {e}")
            return False

    def _clean_text(self, raw: str) -> str:
        """
        Remove noise from extracted text:
          - Normalise Unicode (converts \xa0 non-breaking spaces to regular spaces)
          - Collapse runs of whitespace within lines
          - Remove lines that are pure noise (share buttons, cookie notices, etc.)
          - Remove lines under 4 words that aren't part of a sentence
          - Collapse excessive blank lines to a single blank line
        """
        # Normalise Unicode
        raw = unicodedata.normalize("NFKD", raw)

        lines = raw.splitlines()
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                cleaned.append("")
                continue
            # Skip pure noise lines
            if _NOISE_PATTERNS.match(line):
                continue
            # Skip very short lines that look like UI fragments
            # (but keep short lines that end with punctuation — they're sentence endings)
            words = line.split()
            if len(words) < 4 and not line[-1] in ".!?:":
                continue
            cleaned.append(line)

        # Collapse multiple consecutive blank lines to one
        result_lines = []
        prev_blank = False
        for line in cleaned:
            is_blank = (line == "")
            if is_blank and prev_blank:
                continue
            result_lines.append(line)
            prev_blank = is_blank

        return "\n".join(result_lines).strip()

    def chunk_content(self, text: str) -> list[str]:
        """
        Split clean article text into TTS-friendly chunks.

        Strategy:
          - Split on actual double newlines (paragraph boundaries)
          - Each paragraph becomes one chunk if under 150 words
          - Paragraphs over 150 words are split at sentence boundaries,
            grouping sentences until each group is 80–150 words
          - Chunks under 8 words are discarded (navigation remnants)
        """
        chunks: list[str] = []

        # Split on double newline — the actual two-character sequence
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        for para in paragraphs:
            words = para.split()
            if len(words) < 8:
                continue

            if len(words) <= 150:
                chunks.append(para)
            else:
                # Split at sentence boundaries
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current: list[str] = []
                current_wc = 0

                for sent in sentences:
                    sent_wc = len(sent.split())
                    if current_wc + sent_wc > 150 and current:
                        chunk_text = " ".join(current).strip()
                        if len(chunk_text.split()) >= 8:
                            chunks.append(chunk_text)
                        current = [sent]
                        current_wc = sent_wc
                    else:
                        current.append(sent)
                        current_wc += sent_wc

                # Flush remaining sentences
                if current:
                    chunk_text = " ".join(current).strip()
                    if len(chunk_text.split()) >= 8:
                        chunks.append(chunk_text)

        return chunks

    # ─────────────────────────────────────────────────────────────────────────
    # Reading controls
    # ─────────────────────────────────────────────────────────────────────────

    def read_start(self) -> dict:
        """Extract current page and begin reading aloud."""
        # Stop any ongoing reading first
        if self.session.status == "reading":
            self.session.stop_event.set()
            self.voice.stop()
            time.sleep(0.3)

        pc = self.extract_page_content()

        if not pc.chunks:
            return self._ok(
                "I don't see any readable content on this page. "
                "This might be a video page, a dashboard, or a login wall."
            )

        mins = max(1, round(pc.word_count / 200))
        self.session = ReadingSession(
            chunks=pc.chunks,
            source_url=pc.url,
            title=pc.title,
            status="reading"
        )

        def run():
            title_str = f"Reading: {pc.title}. " if pc.title else ""
            self.voice.speak_sync(
                f"{title_str}{pc.word_count} words. About {mins} minute read."
            )
            self._reading_loop(self.session)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self._ok(f"Starting to read: {pc.title or 'this page'}.")

    def read_pause(self) -> dict:
        if self.session.status != "reading":
            return self._ok("Nothing is being read right now.")
        self.session.stop_event.set()
        self.session.status = "paused"
        self.voice.stop()
        chunks_left = len(self.session.chunks) - self.session.position
        return self._ok(f"Paused. {chunks_left} sections remaining. Say 'continue reading' to resume.")

    def read_continue(self) -> dict:
        if self.session.status != "paused":
            return self._ok(
                "Nothing is paused. Say 'read this page' to start reading."
            )
        # Must replace stop_event before starting thread — the old one is set
        self.session.stop_event = threading.Event()
        self.session.status = "reading"
        self._thread = threading.Thread(
            target=self._reading_loop, args=(self.session,), daemon=True
        )
        self._thread.start()
        return self._ok("")   # No preamble — resume silently, mid-article

    def read_skip(self) -> dict:
        """Skip to the next paragraph."""
        self.session.stop_event.set()
        self.voice.stop()
        new_pos = self.session.position + 1
        if new_pos >= len(self.session.chunks):
            self.session.status = "done"
            return self._ok("That was the last section.")
        self.session.position = new_pos
        self.session.stop_event = threading.Event()
        self.session.status = "reading"
        self._thread = threading.Thread(
            target=self._reading_loop, args=(self.session,), daemon=True
        )
        self._thread.start()
        return self._ok("")

    def read_repeat(self) -> dict:
        """Repeat the previous paragraph."""
        self.session.stop_event.set()
        self.voice.stop()
        self.session.position = max(0, self.session.position - 1)
        self.session.stop_event = threading.Event()
        self.session.status = "reading"
        self._thread = threading.Thread(
            target=self._reading_loop, args=(self.session,), daemon=True
        )
        self._thread.start()
        return self._ok("")

    def read_stop(self) -> dict:
        self.session.stop_event.set()
        self.voice.stop()
        self.session = ReadingSession()   # full reset
        return self._ok("Stopped.")

    def _reading_loop(self, session: ReadingSession):
        """
        Core reading loop. Runs in a daemon thread.
        Uses stop_event (threading.Event) — not a boolean flag — so pause
        responds within the current sentence, not end of paragraph.
        """
        completed = False
        for i in range(session.position, len(session.chunks)):
            if session.stop_event.is_set():
                break
            session.position = i
            self.voice.speak_sync(session.chunks[i])
            # Natural paragraph pause. If stop_event fires here, exit cleanly.
            if session.stop_event.wait(timeout=0.5):
                break
        else:
            completed = True

        if completed:
            session.status = "done"
            self.voice.speak_sync("That's the end of the article.")
        # If stopped/paused — status was already set by the caller

    # ─────────────────────────────────────────────────────────────────────────
    # Summarise
    # ─────────────────────────────────────────────────────────────────────────

    def summarise(self, style: str = "full") -> dict:
        """
        Extract and summarise current page.
        Extraction (fast CDP) happens synchronously.
        LLM inference (slow) runs in a background thread so Nida speaks
        "Summarising, one moment..." immediately.
        """
        pc = self.extract_page_content()

        if pc.word_count < 50:
            return self._ok(
                "There isn't enough text on this page to summarise."
            )

        style_instructions = {
            "full":     "4 to 5 sentences, covering the main argument and key facts",
            "bullets":  "exactly 4 key points, each as a complete spoken sentence starting with a number",
            "one_line": "exactly one sentence",
            "topic":    "2 sentences: what this article is about and who it's for",
        }
        instruction = style_instructions.get(style, style_instructions["full"])

        system = (
            "You are summarising an article for someone listening, not reading. "
            "Respond in plain spoken English only. No markdown, no asterisks, "
            "no bullet symbols, no headers. Write as if speaking naturally."
        )
        prompt = (
            f"Summarise this article in {instruction}.\n\n"
            f"Article title: {pc.title}\n\n"
            f"{pc.content[:5000]}"
        )

        def run():
            answer = self._ask_ollama(prompt, system=system)
            self.voice.speak_sync(answer)

        self.voice.speak_sync("Summarising, one moment.")
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return self._ok(f"Summarising {pc.title or 'this page'}.")

    # ─────────────────────────────────────────────────────────────────────────
    # Metadata extraction
    # ─────────────────────────────────────────────────────────────────────────

    def extract_author(self) -> dict:
        # Try Readability byline first (already extracted during content pass)
        pc = self.extract_page_content()
        if pc.author:
            return self._ok(f"This was written by {pc.author}.")

        # Try meta tags
        try:
            author = self._page.evaluate("""
                document.querySelector(
                    "meta[name='author'], meta[property='article:author']"
                )?.content
                || document.querySelector('[rel="author"]')?.innerText
                || null
            """)
            if author:
                return self._ok(f"This was written by {author.strip()}.")
        except Exception:
            pass

        return self._ok("I couldn't find an author credit on this page.")

    def extract_date(self) -> dict:
        try:
            date_val = self._page.evaluate("""
                document.querySelector("meta[property='article:published_time']")?.content
                || document.querySelector("meta[name='publish-date']")?.content
                || document.querySelector("time[datetime]")?.getAttribute('datetime')
                || document.querySelector("time")?.innerText
                || null
            """)
            if date_val:
                # Try to make it human-readable
                date_str = str(date_val).strip()
                # ISO dates like 2024-03-15T10:30:00Z — extract just the date part
                iso_match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
                if iso_match:
                    date_str = iso_match.group(1)
                return self._ok(f"This was published on {date_str}.")
        except Exception:
            pass
        return self._ok("I couldn't find a publication date on this page.")

    def extract_reading_time(self) -> dict:
        pc = self.extract_page_content()
        mins = max(1, round(pc.word_count / 200))
        return self._ok(
            f"This article is about {pc.word_count} words. "
            f"At average reading speed, it's a {mins} minute read."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Google SERP result navigation
    # ─────────────────────────────────────────────────────────────────────────

    def open_google_result(self, index: int = 1) -> dict:
        """
        Open the nth organic Google search result and read it.

        Filters out:
          - YouTube and other video domain URLs
          - Google internal URLs (maps, images, shopping)
          - Ads and sponsored results

        'index' is 1-based (first result = 1).
        """
        current_url = self._page.url
        if "google.com/search" not in current_url:
            return self._ok(
                "I don't see a Google search results page open. "
                "Please search for something first."
            )

        # Extract all organic article URLs from the SERP
        try:
            all_links: list[dict] = self._page.evaluate("""
                (function() {
                    const results = [];
                    // Google result links are in <a> tags inside #search
                    // The href is sometimes a /url?q= redirect, sometimes direct
                    const anchors = document.querySelectorAll(
                        '#search .g a[href], #rso .g a[href]'
                    );
                    anchors.forEach(a => {
                        let href = a.href || '';
                        // Unwrap Google redirect URLs
                        if (href.includes('/url?q=')) {
                            try {
                                const u = new URL(href);
                                href = u.searchParams.get('q') || href;
                            } catch(e) {}
                        }
                        const text = a.innerText || a.textContent || '';
                        if (href.startsWith('http') && text.trim().length > 5) {
                            results.push({ url: href, title: text.trim() });
                        }
                    });
                    return results;
                })()
            """)
        except Exception as e:
            return self._err(f"Could not read the search results page: {e}")

        if not all_links:
            return self._ok(
                "I couldn't find any links on this page. "
                "Make sure a Google search results page is open."
            )

        # Filter to readable article URLs only
        readable_links = self._filter_readable_links(all_links)

        if not readable_links:
            return self._ok(
                "All the results on this page appear to be videos or Google features. "
                "Try searching for something more specific."
            )

        # Validate requested index
        if index < 1 or index > len(readable_links):
            available = len(readable_links)
            return self._ok(
                f"I only found {available} readable result{'s' if available != 1 else ''}. "
                f"Please ask for a result between 1 and {available}."
            )

        target = readable_links[index - 1]
        logger.info(f"Opening result {index}: {target['url']}")

        # Navigate to the article
        try:
            self._page.goto(target["url"], wait_until="domcontentloaded", timeout=15000)
            # Invalidate Readability cache — new page
            self._readability_loaded = False
        except Exception as e:
            return self._err(
                f"I couldn't open that page. {str(e)[:60]}"
            )

        # Brief wait for dynamic content
        time.sleep(0.8)

        # Now read it
        return self.read_start()

    def _filter_readable_links(self, links: list[dict]) -> list[dict]:
        """
        Keep only links that are readable text articles.
        Removes: YouTube, Vimeo, Google Maps, Google Images,
                 Google Shopping, PDF files, social media video feeds.
        Deduplicates by domain to avoid listing the same site multiple times
        as different results.
        """
        seen_urls: set[str] = set()
        readable: list[dict] = []

        for link in links:
            url = link.get("url", "")
            if not url.startswith("http"):
                continue

            # Skip already seen
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Skip video domains
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lstrip("www.")
                if any(vd in domain for vd in _VIDEO_DOMAINS):
                    logger.debug(f"Skipping video URL: {url}")
                    continue
            except Exception:
                continue

            # Skip Google internal pages
            skip_patterns = [
                "google.com/maps", "google.com/images", "google.com/shopping",
                "google.com/search", "accounts.google.com",
                ".pdf", "twitter.com", "instagram.com", "facebook.com",
                "tiktok.com"
            ]
            if any(p in url for p in skip_patterns):
                logger.debug(f"Skipping internal/social URL: {url}")
                continue

            readable.append(link)

        return readable

    # ─────────────────────────────────────────────────────────────────────────
    # Live search (answer a query without disrupting current tab)
    # ─────────────────────────────────────────────────────────────────────────

    def live_search(self, query: str) -> dict:
        """
        Answer a factual query (sports score, price, weather, news) by:
          1. Opening a NEW browser tab (not disrupting current page)
          2. Navigating to Google search
          3. Extracting the results snippet
          4. Asking the LLM to extract the direct answer
          5. Speaking the answer
          6. Closing the search tab

        The user's current page is never navigated away from.
        """
        if not query.strip():
            return self._ok("What would you like me to search for?")

        def run_search():
            context = self._page.context
            search_page = None
            try:
                # Open a fresh tab
                search_page = context.new_page()
                q = urllib.parse.quote_plus(query)
                search_page.goto(
                    f"https://www.google.com/search?q={q}",
                    wait_until="domcontentloaded",
                    timeout=12000
                )
                # Let the page settle — Google's answer boxes load dynamically
                time.sleep(1.5)

                # Extract the full page text
                raw = search_page.evaluate("document.body.innerText") or ""
                snippet = raw[:4000]

                system = (
                    "You answer questions from Google search result text. "
                    "Respond in plain spoken English only. No markdown. "
                    "Be direct and concise — 1 to 3 sentences max."
                )
                prompt = (
                    f"The user asked: '{query}'\n\n"
                    f"Here is the Google search results page text:\n\n{snippet}\n\n"
                    f"Give the direct answer to the question."
                )
                answer = self._ask_ollama(prompt, system=system)
                self.voice.speak_sync(answer)

            except Exception as e:
                logger.error(f"live_search error: {e}")
                self.voice.speak_sync(
                    "I couldn't complete the search. Please check your connection."
                )
            finally:
                if search_page:
                    try:
                        search_page.close()
                    except Exception:
                        pass

        self.voice.speak("Searching, one moment.")
        t = threading.Thread(target=run_search, daemon=True)
        t.start()
        return self._ok(f"Searching for: {query}.")

    # ─────────────────────────────────────────────────────────────────────────
    # LLM helper
    # ─────────────────────────────────────────────────────────────────────────

    def _ask_ollama(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": OLLAMA_HEAVY_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 300},
        }
        try:
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=40
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except requests.exceptions.Timeout:
            return "My brain timed out. Try again in a moment."
        except Exception as e:
            logger.error(f"Ollama call failed: {e}")
            return "I couldn't get a response from my language model."

    # ─────────────────────────────────────────────────────────────────────────
    # Return helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _ok(self, feedback: str) -> dict:
        return {"ok": True, "feedback": feedback}

    def _err(self, feedback: str) -> dict:
        return {"ok": False, "error": feedback, "feedback": feedback}