import json
import time
import pywhatkit
from pathlib import Path

from tools.base import BaseTool, ToolResult
from tools.browser import BrowserSession
from core.voice import Voice

CONTACTS_PATH = Path("contacts.json")

def _load_contacts() -> dict:
    if not CONTACTS_PATH.exists():
        return {}
    with open(CONTACTS_PATH) as f:
        return json.load(f)

def _resolve_contact(name: str) -> tuple[str, str]:
    contacts = _load_contacts()
    name_lower = name.strip().lower()

    if not contacts:
        raise ValueError("Your contacts file is empty.")

    # Exact match first
    for contact_name, info in contacts.items():
        if contact_name.lower() == name_lower:
            phone = info.get("phone", "")
            if not phone:
                raise ValueError(f"Contact '{contact_name}' has no phone number.")
            return contact_name, phone

    # Partial / substring match
    matches = [
        (cn, info) for cn, info in contacts.items()
        if name_lower in cn.lower() or cn.lower() in name_lower
    ]
    if len(matches) == 1:
        contact_name, info = matches[0]
        phone = info.get("phone", "")
        if not phone:
            raise ValueError(f"Contact '{contact_name}' has no phone number.")
        return contact_name, phone

    if len(matches) > 1:
        names = ", ".join(cn for cn, _ in matches)
        raise ValueError(f"Multiple contacts match '{name}': {names}.")

    raise ValueError(f"I couldn't find a contact named {name}.")

def _clean_phone(phone: str) -> str:
    # WhatsApp Web API expects string like "+919876543210"
    return "+" + "".join(c for c in phone if c.isdigit())
    
class WhatsAppTool(BaseTool):
    """
    Nida WhatsApp Integration - Sends messages via pywhatkit.
    """
    
    @property
    def name(self) -> str:
        return "whatsapp"

    @property
    def description(self) -> str:
        return "Send WhatsApp messages automatically."

    @property
    def args_schema(self) -> dict:
        return {
            "action": "send",
            "to": "contact name",
            "message": "message text",
            "include_current_tab": "optional boolean, set to true if user asks to send the current website/url/link"
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").lower()
        
        if action == "send":
            return self._send(args)
        else:
            return ToolResult(success=False, error=f"Unknown whatsapp action: {action}")

    def _send(self, args: dict) -> ToolResult:
        to_name = args.get("to")
        message = args.get("message", "")
        include_current_tab = args.get("include_current_tab", False)

        if str(include_current_tab).lower() == "true": 
            include_current_tab = True

        if not to_name:
            return ToolResult(success=False, error="Who do you want to send a message to?")
        if not message and not include_current_tab:
            return ToolResult(success=False, error=f"What do you want to say to {to_name}?")

        try:
            contact_name, raw_phone = _resolve_contact(to_name)
            phone = _clean_phone(raw_phone)
            
            if include_current_tab:
                from tools.browser import launch_brave_with_cdp
                sess = BrowserSession()
                err = sess.connect()
                if err:
                    launch_err = launch_brave_with_cdp()
                    if launch_err:
                        return ToolResult(success=False, error=f"Could not connect to browser: {launch_err}")
                    BrowserSession._instance = None
                    sess = BrowserSession()
                    err = sess.connect()
                    if err:
                        return ToolResult(success=False, error=f"Could not connect to browser: {err}")

                active = sess.active_page()
                if active:
                    tab_url = active.url
                    # Append or set message
                    message = f"{message} {tab_url}".strip()
                else:
                    return ToolResult(success=False, error="I couldn't find an active browser window to grab the URL from.")

            # Give instant audio feedback
            Voice().speak(f"Sending message to {contact_name}. Please wait and do not touch your mouse or keyboard.")

            try:
                import time
                import pyautogui
                from pywhatkit.core.core import WIDTH, HEIGHT
                
                # Let pywhatkit open WhatsApp Web with the message pre-filled in the URL.
                # We disable its built-in tab_close so we control the full lifecycle.
                pywhatkit.sendwhatmsg_instantly(
                    phone_no=phone, 
                    message=message, 
                    wait_time=8, 
                    tab_close=False
                )
                
                # ── Link-preview workaround ──────────────────────────────
                # When the message contains a URL, WhatsApp Web generates a
                # link preview card. While that preview is loading:
                #   - the input field can lose focus
                #   - Enter keypresses get swallowed / ignored
                #
                # Fix: wait for the preview to fully render, click directly
                # on the message input box to guarantee focus, then send.
                # ─────────────────────────────────────────────────────────
                
                # Wait for link preview to finish loading
                time.sleep(8)
                
                # Instead of clicking absolute screen coordinates (which fails if the 
                # browser isn't maximized), we just press Enter again. 
                # PyWhatKit's initial Enter gets swallowed by the link preview, 
                # so a second (and third) Enter guarantees the message is sent.
                pyautogui.press("enter")
                time.sleep(1)
                pyautogui.press("enter")
                
                # Give the message a moment to actually transmit
                time.sleep(3)
                pyautogui.hotkey("ctrl", "w")
                
                return ToolResult(
                    success=True, 
                    output=f"Done. I've sent the WhatsApp to {contact_name}."
                )
            except Exception as toolkit_err:
                return ToolResult(success=False, error=f"PyWhatKit failed: {toolkit_err}")

        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to send: {e}")
