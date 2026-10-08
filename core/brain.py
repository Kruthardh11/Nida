# core/brain.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Ollama LLM Interface
#
# WHAT CHANGED IN PHASE 2:
#   The old protocol was:
#     ACTION: <shell command>
#     ANSWER: <spoken text>
#
#   The new protocol is:
#     TOOL: <tool_name> {"arg": "value"}
#     ANSWER: <spoken text>
#
#   _parse() now extracts tool_name and tool_args from the TOOL: line.
#   NidaResponse carries tool_name and tool_args instead of a bare string.
#
#   Backward-compatible fallbacks handle:
#     - ACTION: <command>          → re-routed to TOOL: shell {"command": "..."}
#     - TOOL: shell <bare command> → lenient parse, wraps as {"command": "..."}
#     - Raw shell commands         → detected by _looks_like_command(), wrapped
# ─────────────────────────────────────────────────────────────────────────────

import json
import re
import logging
import requests
from dataclasses import dataclass, field
from typing import Literal

from config.settings import (
    OLLAMA_BASE_URL,
    OLLAMA_TIMEOUT, OLLAMA_TEMPERATURE,
    OLLAMA_SYSTEM_PROMPT
)

logger = logging.getLogger("nida.brain")


@dataclass
class NidaResponse:
    """
    Parsed LLM response.

    type = "tool"   → dispatch to a registered tool
    type = "answer" → speak text back to user
    type = "error"  → something went wrong

    When type == "tool":
        tools      = list of parsed tool dicts: [{"name": "shell", "args": {...}}]
        content    = "" (unused for tool responses)

    When type == "answer":
        content    = the text to speak
        tools      = [] (unused)
    """
    type: Literal["tool", "answer", "error"]
    content: str = ""               # answer text OR error message
    tools: list[dict] = field(default_factory=list) # [{"name": "...", "args": {...}}]
    raw: str = ""                   # raw LLM output for debugging


# ── Fallback detection ────────────────────────────────────────────────────────
# Used when the LLM skips the TOOL:/ANSWER: prefix entirely.
# If the raw text looks like a shell command, we wrap it as a shell tool call.

_COMMAND_SIGNALS = [
    "\\",
    "%",
    ".exe",
    "echo ", "dir ", "mkdir ", "start ", "tasklist", "taskkill",
    "cd ", "copy ", "move ", "del ", "type ",
    "npm ", "git ", "pip ", "python ", "node ",
    "&&", "||", " | ", " > ", " >> ",
]

def _looks_like_command(text: str) -> bool:
    return any(sig in text for sig in _COMMAND_SIGNALS)


class Brain:
    """
    Thin wrapper around the Ollama /api/chat endpoint.
    Parses the TOOL: / ANSWER: protocol defined in the system prompt.
    """

    def __init__(self):
        self._check_ollama()
        logger.info(f"Brain connected to Ollama")

    # ── Public API ────────────────────────────────────────────────────────────

    # Summary Buffer Memory constants:
    #   SUMMARY_TRIGGER : messages to accumulate before compressing old ones.
    #   RECENCY_WINDOW  : most recent messages always kept verbatim.
    SUMMARY_TRIGGER = 10   # = 5 back-and-forth exchanges
    RECENCY_WINDOW  = 4    # = 2 most recent exchanges, kept raw

    def think(
        self,
        user_text: str,
        history: list[dict] | None = None,
        summary: str = "",
        persona: str = "assistant",
    ) -> NidaResponse:
        """
        Send user utterance to Ollama, return a structured NidaResponse.

        Parameters
        ----------
        user_text : str
            The raw transcribed utterance from the user.
        history : list[dict] | None
            Recent verbatim turns (recency window only).
        summary : str
            Compressed summary of older turns (empty for first few turns).
        """
        logger.info(f"Sending to LLM: '{user_text}' (persona: {persona})")
        raw = self._call_ollama(user_text, history or [], summary, persona)
        response = self._parse(raw)
        logger.info(
            f"LLM response → type={response.type}, "
            f"tools_count={len(response.tools)}, "
            f"tools={', '.join([t['name'] for t in response.tools]) if response.tools else 'None'}"
        )
        return response

    def summarize_history(self, history: list[dict], existing_summary: str) -> str:
        """
        Compress history turns into a brief paragraph, folding in any
        existing summary so older context is never permanently lost.

        Called by respond_node when len(history) > SUMMARY_TRIGGER.
        Returns the new consolidated summary string.
        """
        from config.settings import OLLAMA_HEAVY_MODEL
        
        prior_ctx = (
            f"Previous summary:\n{existing_summary}\n\n"
            if existing_summary else ""
        )
        turns_text = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}"
            for m in history
        )
        prompt = (
            f"{prior_ctx}"
            f"New conversation turns to fold in:\n{turns_text}\n\n"
            "Write a concise 2-3 sentence summary preserving key facts, "
            "decisions, topics, and user preferences. "
            "No pleasantries. Be precise and factual."
        )
        payload = {
            "model": OLLAMA_HEAVY_MODEL,
            "messages": [
                {"role": "system", "content": "You are a concise conversation summariser."},
                {"role": "user",   "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 120},
        }
        try:
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            new_summary = r.json()["message"]["content"].strip()
            logger.info(
                f"History summarised "
                f"({len(history)} msgs \u2192 {len(new_summary)} chars) via {OLLAMA_HEAVY_MODEL}."
            )
            return new_summary
        except Exception as e:
            logger.warning(f"Summarisation failed: {e}. Keeping old summary.")
            return existing_summary

    def health_check(self) -> bool:
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    # ── Private ────────────────────────────────────────────────────────────

    def _check_ollama(self):
        from config.settings import OLLAMA_FAST_MODEL, OLLAMA_HEAVY_MODEL
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            logger.info(f"Ollama running. Available models: {models}")
            
            for required_model in [OLLAMA_FAST_MODEL, OLLAMA_HEAVY_MODEL]:
                if not any(required_model in m for m in models):
                    logger.warning(
                        f"Model '{required_model}' not found in Ollama. "
                        f"Run: ollama pull {required_model}"
                    )
        except requests.exceptions.Timeout:
            raise RuntimeError(
                "Ollama is running but took too long to respond. "
                "It might be overloaded or waking up. Please try again."
            )
        except requests.exceptions.RequestException:
            raise RuntimeError(
                "Ollama is not running. Start it with: ollama serve"
            )

    def _call_ollama(
        self, 
        user_text: str, 
        history: list[dict], 
        summary: str = "", 
        persona: str = "assistant"
    ) -> str:
        """
        Build the full message array and route to the correct LLM.
        """
        from config.settings import (
            OLLAMA_SYSTEM_PROMPT, INSTRUCTOR_PROMPT,
            OLLAMA_FAST_MODEL, OLLAMA_HEAVY_MODEL
        )

        # Dynamic Routing Logic: Default to Fast 3B. 
        # Switch to Heavy 7B if teaching, OR if the user is asking for complex browser automation.
        # Simple actions (pause, volume, close tab) drop through to the fast 3B model.
        heavy_keywords = [
            "search", "google", "youtube", "open", "browser", "amazon", 
            "hotstar", "watch", "summarize", "summarise", "read this", 
            "read the page", "start reading", "whatsapp", "message",
            "fitness", "calories", "protein", "workout", "routine", "rate my day"
        ]
        
        if persona in ["instructor", "trainer"] or any(k in user_text.lower() for k in heavy_keywords):
            target_model = OLLAMA_HEAVY_MODEL
        else:
            target_model = OLLAMA_FAST_MODEL

        messages: list[dict] = [{"role": "system", "content": OLLAMA_SYSTEM_PROMPT}]

        # Inject persona override if not standard assistant
        if persona == "instructor":
            messages.append({"role": "system", "content": INSTRUCTOR_PROMPT})
        elif persona == "trainer":
            from config.settings import TRAINER_PROMPT
            messages.append({"role": "system", "content": TRAINER_PROMPT})

        # Inject compressed summary of older context if available
        if summary:
            messages.append({
                "role": "system",
                "content": (
                    f"[Summary of earlier conversation]\n{summary}\n"
                    "[Most recent exchanges follow verbatim below.]"
                )
            })

        # Append the recent verbatim turns (recency window)
        messages.extend(history)

        # The current user turn
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": target_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": OLLAMA_TEMPERATURE,
                "num_predict": 150
            },
        }

        try:
            r = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=OLLAMA_TIMEOUT
            )
            r.raise_for_status()
            logger.debug(f"Routed query through {target_model}")
            return r.json()["message"]["content"].strip()

        except requests.exceptions.Timeout:
            logger.error("Ollama timed out")
            return "ANSWER: Sorry, my brain timed out. Try again."
        except requests.exceptions.ConnectionError:
            logger.error("Lost connection to Ollama")
            return "ANSWER: I can't reach my brain right now. Is Ollama running?"
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return f"ANSWER: Something went wrong: {str(e)[:80]}"

    # ── Parser ─────────────────────────────────────────────────────────────

    def _parse(self, raw: str) -> NidaResponse:
        """
        Parse the TOOL: / ANSWER: protocol.
        Extracts ALL tools if multiple are present.
        """
        text = raw.strip()

        # ── 1. Multiple Tool Extraction ─────────────────────────────────────
        # Split everything using 'TOOL:' as the delimiter.
        # This safely catches multi-line JSON because _try_parse_json only consumes
        # up to the closing brace, ignoring any trailing chatter.
        parts = re.split(r'(?i)TOOL:', text)
        
        extracted_tools = []
        for part in parts[1:]:  # skip [0] because it's text before the first TOOL:
            tool_dict = self._parse_tool_line(part.strip())
            if tool_dict:
                extracted_tools.append(tool_dict)
                
        if extracted_tools:
            return NidaResponse(type="tool", tools=extracted_tools, raw=raw)

        # ── 2. ANSWER: protocol ───────────────────────────────────────────
        if text.upper().startswith("ANSWER:"):
            answer = text[7:].strip()
            return NidaResponse(type="answer", content=answer, raw=raw)

        # ── 3. ACTION: backward compatibility ─────────────────────────────
        if text.upper().startswith("ACTION:"):
            command = text[7:].strip()
            return NidaResponse(type="tool", tools=[{"name": "shell", "args": {"command": command}}], raw=raw)

        # ── 4. Fallback: bare command ───────────────────────────────────────
        if _looks_like_command(text):
            return NidaResponse(type="tool", tools=[{"name": "shell", "args": {"command": text}}], raw=raw)

        # ── 5. Fallback: treat as answer ────────────────────────────────────
        logger.warning(f"LLM skipped prefix, treating as ANSWER: '{text}'")
        return NidaResponse(type="answer", content=text, raw=raw)

    def _parse_tool_line(self, remainder: str) -> dict | None:
        """
        Parse everything after a "TOOL:" declaration (e.g. `shell {"cmd": "dir"}`).
        Returns {"name": tool_name, "args": args} or None if parsing fails.
        """
        parts = remainder.split(None, 1)
        if not parts:
            return None

        tool_name = parts[0].lower()
        args_str = parts[1].strip() if len(parts) > 1 else ""

        # Try strict JSON
        if args_str.startswith("{"):
            args = self._try_parse_json(args_str)
            if args is not None:
                return {"name": tool_name, "args": args}

            # Regex recovery for unescaped inner quotes
            match = re.search(r'"(\w+)"\s*:\s*"([^"]+)"', args_str, re.DOTALL)
            if match:
                return {"name": tool_name, "args": {match.group(1): match.group(2)}}

        # Lenient fallback (bare strings)
        if tool_name == "shell":
            return {"name": "shell", "args": {"command": args_str}}
        
        return {"name": tool_name, "args": {"input": args_str}}

    def _try_parse_json(self, text: str) -> dict | None:
        """
        Attempt to parse a JSON object from the start of text.
        Handles trailing garbage after the closing brace (LLMs sometimes
        append explanations after the JSON).

        Returns the parsed dict, or None if parsing fails.
        """
        # LLMs often output raw newlines inside JSON strings (e.g., when taking notes),
        # which breaks standard JSON parsers. Escaping them fixes 99% of cases.
        cleaned_text = text.replace('\n', '\\n')

        # Try the full string first
        try:
            return json.loads(cleaned_text)
        except json.JSONDecodeError:
            pass

        # Try to extract just the JSON object (find matching closing brace)
        brace_depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    # Found the complete JSON object
                    try:
                        return json.loads(text[:i + 1])
                    except json.JSONDecodeError:
                        return None

        return None
