import re
import subprocess
import calendar
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
import threading

from Foundation import NSDate
import EventKit


class MacCalendarAgent:
    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------
    def _split_prompt(self, prompt: str) -> Tuple[str, str]:
        text = prompt.lower()
        time_patterns = [
            r"\b(on|this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(\s+at\s+\d{1,2}([:.]\d{2})?\s*(a\.?m\.?|p\.?m\.?)?)?",
            r"\b(tomorrow|today)\b(\s+at\s+\d{1,2}([:.]\d{2})?\s*(a\.?m\.?|p\.?m\.?)?)?",
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(\s+at\s+\d{1,2}([:.]\d{2})?\s*(a\.?m\.?|p\.?m\.?)?)?",
            r"\b\d{1,2}/\d{1,2}(\s+at\s+\d{1,2}([:.]\d{2})?\s*(a\.?m\.?|p\.?m\.?)?)?",
            r"\b\d{1,2}([:.]\d{2})?\s*(a\.?m\.?|p\.?m\.?)?\b"
        ]
        time_str = ""
        for pat in time_patterns:
            m = re.search(pat, text)
            if m:
                time_str = m.group(0)
                break
        if time_str:
            text = text.replace(time_str, "", 1)
        return time_str.strip(), text.strip()

    @staticmethod
    def _extract_title(text: str) -> str:
        fillers = [
            "could you add an event", "could you add", "could you", "can you add",
            "can you", "would you", "add an event", "add that to my calendar",
            "add this to my calendar", "that to my calendar", "to my calendar",
            "to my computer calendar", "to my computer", "on my calendar",
            "schedule", "please", "thank you", "i have", "i've got", "saying that"
        ]
        clean = text.lower()
        for f in fillers:
            clean = clean.replace(f, "")

        # Remove linking verbs and common prepositions
        clean = re.sub(r"\b(is|was|were|will be|am|are|at|on|by|around|about|for)\b", "", clean)
        clean = re.sub(r"\d{1,2}([:.]\d{2})?\s*(a\.?m\.?|p\.?m\.?)?", "", clean)
        clean = re.sub(r"[?.!,]+", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean.title() if clean else "Untitled"

    # ---------------------------------------------------------
    # Date/Time Parser
    # ---------------------------------------------------------
    def _parse_datetime(self, time_str: str) -> Tuple[datetime, datetime]:
        now = datetime.now()
        base = now
        text = (time_str or "").lower().strip()

        # ✅ Normalize dots and am/pm forms
        text = re.sub(r"\.", ":", text)              # 7.15 → 7:15
        text = re.sub(r"p\.?m\.?", "pm", text)
        text = re.sub(r"a\.?m\.?", "am", text)

        # Parse absolute month-day
        absolute_patterns = ["%B %d", "%b %d", "%m/%d", "%d/%m"]
        for pat in absolute_patterns:
            try:
                parsed = datetime.strptime(re.sub(r"(\d)(st|nd|rd|th)", r"\1", text), pat)
                base = parsed.replace(year=now.year)
                break
            except ValueError:
                continue

        # Relative keywords
        if "tomorrow" in text:
            base = now + timedelta(days=1)
        elif "today" in text:
            base = now

        # Day-of-week
        for i, day in enumerate(calendar.day_name):
            if re.search(rf"\b(on|this|next)?\s*{day.lower()}\b", text):
                days_ahead = (i - now.weekday() + 7) % 7
                if "next" in text:
                    days_ahead += 7
                base = now + timedelta(days=days_ahead)
                break

        # Time parsing
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            ampm = match.group(3)
            if ampm:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
        else:
            hour, minute = 9, 0

        start = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        return start, end

    # ---------------------------------------------------------
    # AppleScript add_event (write)
    # ---------------------------------------------------------
    def add_event(self, title: str, start_dt: datetime, end_dt: datetime, calendar: str = "Home") -> Dict[str, Any]:
        title = title.replace('"', "'")
        start_ts = int(start_dt.timestamp())
        end_ts   = int(end_dt.timestamp())

        # AppleScript builds date objects directly from epoch seconds
        script = f'''
        set startDate to (current date) + ({start_ts} - (do shell script "date +%s") as integer)
        set endDate   to (current date) + ({end_ts} - (do shell script "date +%s") as integer)
        tell application "Calendar"
            if not (exists calendar "{calendar}") then
                make new calendar with properties {{name:"{calendar}"}}
            end if
            tell calendar "{calendar}"
                make new event at end with properties {{summary:"{title}", start date:startDate, end date:endDate}}
            end tell
        end tell
        '''

        try:
            subprocess.run(["osascript", "-e", script], check=True)
            return {"status": "added", "event": title, "calendar": calendar}
        except subprocess.CalledProcessError as e:
            return {"status": "error", "error": e.stderr.decode() if e.stderr else str(e)}
        except Exception as e:
            return {"status": "error", "error": str(e)}


    # ---------------------------------------------------------
    # EventKit read (list_day & list_upcoming)
    # ---------------------------------------------------------
    def _to_nsdate(self, py_date: datetime):
        return NSDate.dateWithTimeIntervalSince1970_(py_date.timestamp())

    def _fetch_events(self, start: datetime, end: datetime):
        store = EventKit.EKEventStore.alloc().init()
        granted = []
        access_done = threading.Event()

        def _handler(grant, err):
            granted.append(grant)
            access_done.set()

        store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, _handler)
        access_done.wait()

        if not granted or not granted[0]:
            return {"status": "error", "error": "Calendar access denied"}

        all_cals = store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(
            self._to_nsdate(start),
            self._to_nsdate(end),
            all_cals
        )

        events = store.eventsMatchingPredicate_(pred)
        if not events:
            return {"status": "ok", "events": ["You're free today!"]}

        events_out = []
        for e in events:
            if getattr(e, "isDetached", lambda: False)() or getattr(e, "isCancelled", False):
                continue
            title = e.title() or "(no title)"
            start_time = e.startDate()
            events_out.append(f"{title} at {start_time}")
        return {"status": "ok", "events": events_out or ["No events found"]}

    def list_day(self, day: datetime) -> Dict[str, Any]:
        start = datetime(day.year, day.month, day.day)
        end = start + timedelta(days=1)
        return self._fetch_events(start, end)

    def list_upcoming(self, days_ahead: int = 7) -> Dict[str, Any]:
        start = datetime.now()
        end = start + timedelta(days=days_ahead)
        return self._fetch_events(start, end)

    # ---------------------------------------------------------
    # Entry point
    # ---------------------------------------------------------
    def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        text = (payload.get("title") or "").lower().strip()
        fn = (payload.get("fn") or "").lower()

        # Infer function if user says "what do I have tomorrow?"
        if any(k in text for k in ("what do i have", "show me", "list", "schedule", "events", "do i have anything")):
            if "tomorrow" in text:
                fn = "day"
                payload["date"] = datetime.now() + timedelta(days=1)
            elif any(k in text for k in ("week", "upcoming", "next few days")):
                fn = "upcoming"
                payload["days"] = 7
            else:
                fn = "today"

        # Add event
        if fn == "add":
            raw_title = payload.get("title", "")
            time_str, title_str = self._split_prompt(raw_title)
            start_dt, end_dt = payload.get("start"), payload.get("end")
            if not start_dt or not end_dt:
                start_dt, end_dt = self._parse_datetime(time_str or raw_title)
            title = self._extract_title(title_str or raw_title)
            return {"ok": True, **self.add_event(title, start_dt, end_dt, payload.get("calendar", "Home"))}

        # List events
        if fn == "today":
            return {"ok": True, **self.list_day(datetime.now())}
        if fn == "day":
            when = payload.get("date")
            if not when:
                return {"ok": False, "error": "Missing date for 'day' query"}
            return {"ok": True, **self.list_day(when)}
        if fn == "upcoming":
            return {"ok": True, **self.list_upcoming(int(payload.get("days", 7)))}

        return {"ok": False, "error": f"Unknown fn: {fn}"}
