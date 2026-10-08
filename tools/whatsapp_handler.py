"""
nida/whatsapp_handler.py
──────────────────────────────────────────────────────────────
The single entry point to plug into Nida's main intent loop.

In your main Nida loop, after STT gives you `voice_text`, call:

    from nida.whatsapp_handler import handle_whatsapp_command

    if is_whatsapp_command(voice_text):
        response = handle_whatsapp_command(voice_text)
        nida_speak(response)  # your TTS function

Nida will speak the returned string back to the user.
"""

from nida.whatsapp import send_whatsapp, read_whatsapp, format_for_voice
from nida.whatsapp_intent import parse_whatsapp_command


# ── Trigger detection ─────────────────────────────────────

WHATSAPP_TRIGGERS = [
    "whatsapp", "whats app", "wa ", " wa ",
    "send message to", "message to",
    "read my messages", "check messages",
    "any messages from",
]


def is_whatsapp_command(voice_text: str) -> bool:
    """Quick check before calling the LLM parser."""
    text = voice_text.lower()
    return any(trigger in text for trigger in WHATSAPP_TRIGGERS)


# ── Main handler ──────────────────────────────────────────

def handle_whatsapp_command(voice_text: str) -> str:
    """
    Full pipeline: voice text → parse → execute → voice response string.

    Args:
        voice_text: Raw transcript from Nida's STT

    Returns:
        A natural-language string for Nida to speak aloud.
    """
    parsed = parse_whatsapp_command(voice_text)

    # ── Parse failed ──
    if parsed.get("parse_error") and parsed["intent"] == "UNKNOWN":
        return parsed.get(
            "clarification_needed",
            "I didn't understand that WhatsApp command. Could you rephrase?"
        )

    # ── Clarification needed ──
    if parsed["intent"] == "UNKNOWN":
        return parsed.get(
            "clarification_needed",
            "I didn't catch who you want to message or what you want to say."
        )

    # ── SEND ──────────────────────────────────────────────
    if parsed["intent"] == "SEND":
        to = parsed.get("to")
        message = parsed.get("message")

        if not to:
            return "Who should I send the WhatsApp to?"
        if not message:
            return f"What should I say to {to}?"

        # Confirm before sending (optional — remove if you want fire-and-forget)
        # For now Nida sends immediately and reports back.
        result = send_whatsapp(to_name=to, message=message)

        if result["success"]:
            return (
                f"Done! WhatsApp sent to {result['contact']}. "
                f"Message: '{message}'"
            )
        else:
            error = result.get("error", "unknown error")
            # Make common errors user-friendly
            if "not found" in error.lower():
                return (
                    f"I couldn't find {to} in your contacts. "
                    "Would you like to add them?"
                )
            if "twilio error" in error.lower():
                return (
                    "There was a problem sending the message. "
                    "Please check your Twilio credentials."
                )
            return f"Failed to send the WhatsApp. {error}"

    # ── READ ──────────────────────────────────────────────
    if parsed["intent"] == "READ":
        from_name   = parsed.get("from_name")
        limit       = parsed.get("limit", 5)
        unread_only = parsed.get("unread_only", False)

        messages = read_whatsapp(
            from_name=from_name,
            limit=limit,
            unread_only=unread_only,
        )

        return format_for_voice(messages)

    return "I'm not sure what you want to do with WhatsApp. Try saying 'Send WhatsApp to [name]' or 'Read my messages'."
