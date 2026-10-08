"""
nida/gmail_handler.py
──────────────────────────────────────────────────────────────
The single entry point to plug into Nida's main intent loop for Gmail.
"""

from tools.gmail import send_email, search_emails
from tools.gmail_intent import parse_gmail_command

GMAIL_TRIGGERS = [
    "email", "gmail", "inbox", "send mail", "read mail", "check mail"
]

def is_gmail_command(voice_text: str) -> bool:
    """Quick check before calling the LLM parser."""
    text = voice_text.lower()
    return any(trigger in text for trigger in GMAIL_TRIGGERS)

def handle_gmail_command(voice_text: str) -> str:
    """
    Full pipeline: voice text → parse → execute → voice response string.
    """
    parsed = parse_gmail_command(voice_text)

    # ── Parse failed ──
    if parsed.get("parse_error") and parsed["intent"] == "UNKNOWN":
        return parsed.get(
            "clarification_needed",
            "I didn't understand that Gmail command. Could you rephrase?"
        )

    # ── Clarification needed ──
    if parsed["intent"] == "UNKNOWN":
        return parsed.get(
            "clarification_needed",
            "I didn't catch what you want to do with Gmail."
        )

    # ── SEND ──────────────────────────────────────────────
    if parsed["intent"] == "SEND":
        to = parsed.get("to")
        subject = parsed.get("subject") or "Message from Nida"
        message = parsed.get("message")

        if not to:
            return "Who should I send the email to?"
        if not message:
            return f"What should I say to {to}?"

        try:
            send_email(to_name=to, subject=subject, body=message)
            return f"Email sent to {to} with subject '{subject}'."
        except ValueError as e:
            if "not found" in str(e).lower():
                return f"I couldn't find {to} in your contacts. Would you like to add them?"
            return f"Failed to send the email. {str(e)}"
        except Exception as e:
            return f"Failed to send the email. {str(e)}"

    # ── READ / SEARCH ──────────────────────────────────────
    if parsed["intent"] in ["READ", "SEARCH"]:
        query = parsed.get("query", "")
        limit = parsed.get("limit", 5)
        unread_only = parsed.get("unread_only", False)

        if parsed["intent"] == "READ" and unread_only and "is:unread" not in query:
            query = f"is:unread {query}".strip()

        try:
            emails = search_emails(query=query, max_results=limit)
            if not emails:
                return "I couldn't find any matching emails."

            response_parts = [f"You have {len(emails)} matching emails."]
            for i, em in enumerate(emails, 1):
                from_str = em.get('from', 'Unknown Sender').split('<')[0].strip()
                subject = em.get('subject', 'No Subject')
                if i == 1:
                    response_parts.append(f"First, from {from_str}: subject '{subject}'.")
                elif i == 2:
                    response_parts.append(f"Second, from {from_str}: subject '{subject}'.")
                elif i == 3:
                    response_parts.append(f"Third, from {from_str}: subject '{subject}'.")
                else:
                    response_parts.append(f"Number {i}, from {from_str}: subject '{subject}'.")

            return " ".join(response_parts)
        except Exception as e:
            return f"Failed to read emails. {str(e)}"

    return "I'm not sure what you want to do with Gmail. Try saying 'Send email to [name]' or 'Read my emails'."
