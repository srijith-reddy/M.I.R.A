import re
import subprocess
import calendar
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional


class MacCalendarAgent:
    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------
    def _split_prompt(self, prompt: str) -> Tuple[str, str]:
        """Extract time-related part and leftover (for title)."""
        text = prompt.lower()

        time_patterns = [
            r"(next\s+\w+\s+at\s+\d{1,2}(:\d{2})?\s*(am|pm))",
            r"(tomorrow\s+at\s+\d{1,2}(:\d{2})?\s*(am|pm))",
            r"(\b\d{1,2}(:\d{2})?\s*(am|pm)\b)",
            r"(today\s+at\s+\d{1,2}(:\d{2})?\s*(am|pm)?)",
            r"(next\s+\w+)",
            r"(tomorrow)",
            r"(today)",
            r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
        ]

        time_str = ""
        for pat in time_patterns:
            m = re.search(pat, text)
            if m and not time_str:  # keep first match for datetime parsing
                time_str = m.group(0)
            # remove *all* matches from the leftover
            text = re.sub(pat, "", text)

        return time_str.strip(), text.strip()

    @staticmethod
    def _extract_title(text: str) -> str:
        fillers = [
            "could you add an event", "could you add", "add an event",
            "add that to my calendar", "that to my calendar",
            "add this to my calendar", "to my calendar",
            "to my computer calendar", "to my computer", "on my calendar",
            "schedule", "please", "thank you", "can you add",
            "i have", "i've got", "saying that"
        ]

        clean = text.lower()
        for f in fillers:
            clean = clean.replace(f, "")

        # strip "at 5pm", "at 10:30 am", etc.
        clean = re.sub(r"\bat\s+\d{1,2}(:\d{2})?\s*(am|pm|o'clock)?", "", clean)

        # strip dangling am/pm
        clean = re.sub(r"\b(am|pm)\b", "", clean)

        # strip stray punctuation
        clean = re.sub(r"[?.!]+", " ", clean)

        # normalize spaces
        clean = re.sub(r"\s+", " ", clean).strip()

        return clean.title() if clean else "Untitled"

    def _parse_datetime(self, time_str: str) -> Tuple[datetime, datetime]:
        now = datetime.now()
        base = now
        text = (time_str or "").lower()

        # Handle "tomorrow"
        if "tomorrow" in text:
            base = now + timedelta(days=1)

        # Handle weekdays
        for i, day in enumerate(calendar.day_name):
            day_lower = day.lower()
            if f"next {day_lower}" in text or day_lower in text:
                days_ahead = (i - now.weekday() + 7) % 7
                if days_ahead == 0:
                    days_ahead = 7
                base = now + timedelta(days=days_ahead)
                break

        # Match times
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if not match:
            start = datetime.combine(base.date(), datetime.min.time()).replace(hour=9)
            end = start + timedelta(hours=1)
            return start, end

        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)

        if ampm and "pm" in ampm and hour < 12:
            hour += 12
        if ampm and "am" in ampm and hour == 12:
            hour = 0

        start = datetime.combine(base.date(), datetime.min.time()).replace(hour=hour, minute=minute)
        end = start + timedelta(hours=1)
        return start, end

    # ---------------------------------------------------------
    # AppleScript wrappers
    # ---------------------------------------------------------
    def add_event(self, title: str, start_dt: datetime, end_dt: datetime, calendar: str = "Home") -> Dict[str, Any]:
        start_str = start_dt.strftime("%d %B %Y %H:%M:%S")
        end_str   = end_dt.strftime("%d %B %Y %H:%M:%S")

        script = f'''
        tell application "Calendar"
            tell calendar "{calendar}"
                set startDate to date "{start_str}"
                set endDate to date "{end_str}"
                make new event at end with properties {{summary:"{title}", start date:startDate, end date:endDate}}
            end tell
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", script], check=True)
            return {"status": "added", "event": title, "calendar": calendar}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_day(self, day: datetime, calendar: str = "Home") -> Dict[str, Any]:
        """List events for a specific date."""
        start_str = day.strftime("%d %B %Y 00:00:00")
        end_str   = (day + timedelta(days=1)).strftime("%d %B %Y 00:00:00")

        script = f'''
        tell application "Calendar"
            tell calendar "{calendar}"
                set dayStart to date "{start_str}"
                set dayEnd to date "{end_str}"
                set dayEvents to (every event whose start date ≥ dayStart and start date < dayEnd)
                set output to ""
                repeat with e in dayEvents
                    set output to output & (summary of e) & " at " & (start date of e as string) & linefeed
                end repeat
                return output
            end tell
        end tell
        '''
        try:
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=True)
            events = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return {"status": "ok", "events": events}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------
    # Entry point
    # ---------------------------------------------------------
    def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        fn = (payload.get("fn") or "").lower()

        if fn == "add":
            raw_title = payload.get("title", "")
            time_str, title_str = self._split_prompt(raw_title)
            start_dt, end_dt = payload.get("start"), payload.get("end")
            if not start_dt or not end_dt:
                start_dt, end_dt = self._parse_datetime(time_str or raw_title)
            title = self._extract_title(title_str or raw_title)
            return {"ok": True, **self.add_event(title, start_dt, end_dt, payload.get("calendar", "Home"))}

        if fn == "today":
            return {"ok": True, **self.list_day(datetime.now(), payload.get("calendar", "Home"))}

        if fn == "day":
            # expects a 'date' field
            when: Optional[datetime] = payload.get("date")
            if not when:
                return {"ok": False, "error": "Missing date for 'day' query"}
            return {"ok": True, **self.list_day(when, payload.get("calendar", "Home"))}

        return {"ok": False, "error": f"Unknown fn: {fn}"}
