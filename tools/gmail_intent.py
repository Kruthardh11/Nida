"""
nida/gmail_intent.py
──────────────────────────────────────────────────────────────
Parses natural voice commands into structured Gmail actions
using Qwen 2.5 7B (your local model via Ollama or llama.cpp).

This sits between Nida's STT output and the gmail.py module.

Supported intents:
    SEND    → send_email(to_name, subject, body)
    READ    → search_emails(query)
    SEARCH  → search_emails(query)
    UNKNOWN → ask Nida to clarify
"""

import json
import re
import requests

# ── Model config (adjust to your Qwen setup) ──────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"   # or your exact tag

SYSTEM_PROMPT = """You are a command parser for a Gmail voice assistant called Nida.
The user will give you a raw voice command. Extract the intent and parameters.

Return ONLY valid JSON — no explanation, no markdown fences.

Schema:
{
  "intent": "SEND" | "READ" | "SEARCH" | "UNKNOWN",
  "to": "<contact name, only for SEND>",
  "subject": "<email subject, only for SEND>",
  "message": "<message body, only for SEND>",
  "query": "<gmail search query, for READ or SEARCH>",
  "limit": <integer 1-20, only for READ/SEARCH, default 5>,
  "unread_only": <true|false, only for READ>,
  "clarification_needed": "<question to ask user if UNKNOWN>"
}

Rules:
- For SEND: extract who to send to, the subject, and the exact message body. If no subject is given, leave it empty or guess a short one.
- For READ: detect phrases like "read", "check my emails", "any emails from". Formulate a Gmail search query if a person is mentioned (e.g. "from:mom").
- For SEARCH: detect phrases like "search emails about", "find email".
- Preserve the message body exactly as the user said it — do not paraphrase.
- Names should be title-cased as spoken.
- If ambiguous, set intent to UNKNOWN and ask a clarifying question.
"""

def _call_qwen(voice_input: str) -> str:
    """Call local Qwen model via Ollama and return raw text response."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{SYSTEM_PROMPT}\n\nVoice command: {voice_input}",
        "stream": False,
        "options": {"temperature": 0.1, "top_p": 0.9},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("response", "")

def _extract_json(raw: str) -> dict:
    """Robustly extract JSON from model response."""
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No JSON found in model response: {raw!r}")

def parse_gmail_command(voice_input: str) -> dict:
    """
    Parse a raw voice string into a structured Gmail command.
    """
    result = {
        "intent": "UNKNOWN",
        "to": None,
        "subject": None,
        "message": None,
        "query": None,
        "limit": 5,
        "unread_only": False,
        "clarification_needed": None,
        "raw_input": voice_input,
        "parse_error": None,
    }

    try:
        raw = _call_qwen(voice_input)
        parsed = _extract_json(raw)
        result.update({k: v for k, v in parsed.items() if k in result})
    except requests.RequestException as e:
        result["parse_error"] = f"Model unreachable: {e}"
        result["intent"] = "UNKNOWN"
        result["clarification_needed"] = (
            "I couldn't reach my language model. Please try again."
        )
    except (json.JSONDecodeError, ValueError) as e:
        result["parse_error"] = f"JSON parse failed: {e}"
        # Fallback: simple regex for common send pattern
        result.update(_regex_fallback(voice_input))

    return result

def _regex_fallback(text: str) -> dict:
    """
    Simple regex fallback if LLM is unavailable.
    """
    send_pattern = re.compile(
        r"(?:send|email)\s+(?:an\s+)?(?:email\s+)?(?:to\s+)?([A-Za-z ]+?)\s+(?:saying|that|subject|with subject)?\s+(.+)",
        re.IGNORECASE,
    )
    match = send_pattern.search(text)
    if match:
        return {
            "intent": "SEND",
            "to": match.group(1).strip().title(),
            "subject": "Message from Nida",
            "message": match.group(2).strip(),
        }

    read_pattern = re.compile(
        r"(?:read|check|any|show)\s+(?:my\s+)?(?:email|emails?|gmail)",
        re.IGNORECASE,
    )
    if read_pattern.search(text):
        return {"intent": "READ", "limit": 5, "unread_only": False}

    return {
        "intent": "UNKNOWN",
        "clarification_needed": (
            "I didn't catch that. You can say things like "
            "'Send email to Ayesha with subject meeting and body I'll be late' "
            "or 'Read my latest emails'."
        ),
    }
