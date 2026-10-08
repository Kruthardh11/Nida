import json
import base64
import os
from pathlib import Path
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from tools.base import BaseTool, ToolResult

# Assuming get_credentials is in root auth.py, but it's cleaner to re-implement or import
# I will import it from auth.py which is at the root.
import sys
sys.path.append(str(Path(__file__).parent.parent))
from auth import get_credentials

CONTACTS_PATH = Path("contacts.json")

def _load_contacts() -> dict:
    if not CONTACTS_PATH.exists():
        return {}
    with open(CONTACTS_PATH) as f:
        return json.load(f)

def _resolve_email(name: str) -> str:
    contacts = _load_contacts()
    name_lower = name.strip().lower()

    if not contacts:
        raise ValueError("Your contacts file is empty.")

    # Exact match first
    for contact_name, info in contacts.items():
        if contact_name.lower() == name_lower:
            email = info.get("email", "")
            if not email:
                raise ValueError(f"Contact '{contact_name}' has no email address.")
            return email

    # Partial / substring match
    matches = [
        (cn, info) for cn, info in contacts.items()
        if name_lower in cn.lower() or cn.lower() in name_lower
    ]
    if len(matches) == 1:
        contact_name, info = matches[0]
        email = info.get("email", "")
        if not email:
            raise ValueError(f"Contact '{contact_name}' has no email address.")
        return email

    if len(matches) > 1:
        names = ", ".join(cn for cn, _ in matches)
        raise ValueError(f"Multiple contacts match '{name}': {names}.")

    raise ValueError(f"I couldn't find a contact named {name}.")

def _get_gmail_service():
    creds = get_credentials()
    if not creds or not creds.valid:
        raise ValueError("Valid Google credentials not found. Run auth.py first.")
    return build("gmail", "v1", credentials=creds)

def list_emails(max_results=5, query="", label=""):
    """Returns list of {id, threadId}"""
    service = _get_gmail_service()
    kwargs = {"userId": "me", "maxResults": max_results}
    if query:
        kwargs["q"] = query
    if label:
        kwargs["labelIds"] = [label]

    results = service.users().messages().list(**kwargs).execute()
    messages = results.get("messages", [])
    return messages

def get_email(message_id):
    """Returns {from, subject, date, body_text, snippet}"""
    service = _get_gmail_service()
    msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()
    
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    
    email_data = {
        "id": msg.get("id"),
        "snippet": msg.get("snippet", ""),
        "from": "Unknown",
        "subject": "No Subject",
        "date": "Unknown"
    }
    
    for header in headers:
        name = header.get("name", "").lower()
        if name == "from":
            email_data["from"] = header.get("value")
        elif name == "subject":
            email_data["subject"] = header.get("value")
        elif name == "date":
            email_data["date"] = header.get("value")

    # Extract body
    parts = payload.get("parts", [])
    body_data = None
    mime_type = "text/plain"
    
    if not parts:
        body_data = payload.get("body", {}).get("data")
        mime_type = payload.get("mimeType", "text/plain")
    else:
        for part in parts:
            if part.get("mimeType") == "text/plain":
                body_data = part.get("body", {}).get("data")
                mime_type = "text/plain"
                break
            elif part.get("mimeType") == "text/html":
                body_data = part.get("body", {}).get("data")
                mime_type = "text/html"

    body_text = ""
    if body_data:
        decoded_bytes = base64.urlsafe_b64decode(body_data)
        decoded_text = decoded_bytes.decode("utf-8", errors="ignore")
        if "html" in mime_type.lower():
            soup = BeautifulSoup(decoded_text, "html.parser")
            body_text = soup.get_text(separator="\n", strip=True)
        else:
            body_text = decoded_text

    email_data["body_text"] = body_text
    return email_data

def send_email(to_name, subject, body):
    email_address = _resolve_email(to_name)
    service = _get_gmail_service()
    
    msg = MIMEText(body)
    msg['to'] = email_address
    msg['subject'] = subject
    
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    message = service.users().messages().send(userId='me', body={'raw': raw}).execute()
    return message

def search_emails(query, max_results=5):
    messages = list_emails(max_results=max_results, query=query)
    emails = []
    for m in messages:
        try:
            emails.append(get_email(m['id']))
        except Exception:
            pass
    return emails

class GmailTool(BaseTool):
    """
    Nida Gmail Integration - Sends, reads, and searches emails via Gmail API.
    """
    
    @property
    def name(self) -> str:
        return "gmail"

    @property
    def description(self) -> str:
        return "Send, read, and search Gmail messages."

    @property
    def args_schema(self) -> dict:
        return {
            "action": "send | read | search",
            "to": "contact name (for send)",
            "subject": "email subject (for send)",
            "message": "email body (for send)",
            "query": "search query (for search or read)",
            "limit": "integer limit (for search or read, default 5)",
            "unread_only": "boolean (for read)"
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").lower()
        
        try:
            if action == "send":
                to_name = args.get("to")
                subject = args.get("subject", "Message from Nida")
                message = args.get("message", "")
                
                if not to_name:
                    return ToolResult(success=False, error="Who do you want to send the email to?")
                if not message:
                    return ToolResult(success=False, error="What should the email say?")
                    
                send_email(to_name, subject, message)
                return ToolResult(success=True, output=f"Email sent to {to_name} with subject '{subject}'.")
                
            elif action == "read" or action == "search":
                query = args.get("query", "")
                limit = args.get("limit", 5)
                unread_only = args.get("unread_only", False)
                
                if action == "read" and unread_only and "is:unread" not in query:
                    query = f"is:unread {query}".strip()
                    
                emails = search_emails(query, max_results=limit)
                
                if not emails:
                    return ToolResult(success=True, output="No matching emails found.")
                    
                output = [f"Found {len(emails)} emails."]
                for i, em in enumerate(emails, 1):
                    output.append(f"{i}. From {em['from']}: {em['subject']}\nSnippet: {em['snippet']}")
                    
                return ToolResult(success=True, output="\n\n".join(output))
                
            else:
                return ToolResult(success=False, error=f"Unknown gmail action: {action}")
                
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Gmail API error: {e}")
