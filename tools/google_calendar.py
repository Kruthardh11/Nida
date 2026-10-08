import datetime
import dateutil.parser
from googleapiclient.discovery import build
from tools.base import BaseTool, ToolResult

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from auth import get_credentials

def _get_calendar_service():
    creds = get_credentials()
    if not creds or not creds.valid:
        raise ValueError("Valid Google credentials not found. Run auth.py first.")
    return build("calendar", "v3", credentials=creds)

def get_schedule(date_str: str = "today") -> list:
    service = _get_calendar_service()
    
    now = datetime.datetime.now().astimezone()
    
    if date_str.lower() == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif date_str.lower() == "tomorrow":
        start_time = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = (now + datetime.timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        try:
            target_date = dateutil.parser.parse(date_str).astimezone()
            start_time = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = target_date.replace(hour=23, minute=59, second=59, microsecond=0)
        except Exception:
            # Fallback to today
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = now.replace(hour=23, minute=59, second=59, microsecond=0)

    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.isoformat(),
        timeMax=end_time.isoformat(),
        maxResults=20,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    
    return events_result.get('items', [])

def create_event(summary: str, start_time_str: str, duration_minutes: int, description: str = "") -> dict:
    service = _get_calendar_service()
    
    try:
        # Provide the current local timezone info to the parser
        now = datetime.datetime.now().astimezone()
        
        start_time_str = start_time_str.lower()
        if start_time_str == "now":
            start_dt = now
        else:
            # Handle "today 14:00" or "tomorrow 14:00"
            if "today" in start_time_str:
                date_part = now.strftime("%Y-%m-%d")
                start_time_str = start_time_str.replace("today", date_part)
            elif "tomorrow" in start_time_str:
                tomorrow = now + datetime.timedelta(days=1)
                date_part = tomorrow.strftime("%Y-%m-%d")
                start_time_str = start_time_str.replace("tomorrow", date_part)
                
            # Parses "2026-04-26 15:00" or similar
            start_dt = dateutil.parser.parse(start_time_str)
            # Make sure it's timezone aware
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now.tzinfo)
                
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        
    except Exception as e:
        raise ValueError(f"Could not parse the date/time '{start_time_str}'. Please use format YYYY-MM-DD HH:MM.")

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_dt.isoformat(),
        },
        'end': {
            'dateTime': end_dt.isoformat(),
        },
    }

    event = service.events().insert(calendarId='primary', body=event).execute()
    return event

class CalendarTool(BaseTool):
    """
    Nida Google Calendar Integration
    """
    
    @property
    def name(self) -> str:
        return "calendar"

    @property
    def description(self) -> str:
        return "Read schedule and create meetings/reminders via Google Calendar."

    @property
    def args_schema(self) -> dict:
        return {
            "action": "read | create",
            "date": "Date to read schedule for (e.g., 'today', 'tomorrow', '2026-04-26')",
            "summary": "Meeting/Task title (for create)",
            "start_time": "Start time in format 'YYYY-MM-DD HH:MM' or 'now' (for create)",
            "duration": "Duration in minutes (integer, for create)",
            "description": "Optional description for the event"
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").lower()
        
        try:
            if action == "read":
                date_str = args.get("date", "today")
                events = get_schedule(date_str)
                
                if not events:
                    return ToolResult(success=True, output=f"Your schedule for {date_str} is clear! No upcoming events.")
                    
                output = [f"Here is your schedule for {date_str}:"]
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    # Format standard dateTime to something readable
                    try:
                        dt = dateutil.parser.parse(start)
                        if 'date' in event['start']:
                            time_str = "All-day"
                        else:
                            time_str = dt.strftime("%I:%M %p")
                    except:
                        time_str = start
                        
                    output.append(f"- {time_str}: {event['summary']}")
                    
                return ToolResult(success=True, output="\n".join(output))
                
            elif action == "create":
                summary = args.get("summary")
                start_time = args.get("start_time")
                duration = args.get("duration", 30) # Default to 30 mins
                description = args.get("description", "")
                
                if not summary or not start_time:
                    return ToolResult(success=False, error="Need 'summary' and 'start_time' to create an event.")
                    
                # Clean up if the LLM provided duration as a string
                if isinstance(duration, str):
                    try:
                        duration = int(''.join(filter(str.isdigit, duration)) or 30)
                    except:
                        duration = 30
                        
                event = create_event(summary, start_time, duration, description)
                link = event.get('htmlLink')
                
                return ToolResult(success=True, output=f"Successfully scheduled '{summary}' for {duration} minutes. Event created!")
                
            else:
                return ToolResult(success=False, error=f"Unknown calendar action: {action}")
                
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Google Calendar API error: {e}")
