import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from app.models import DeadlineStatus, ItemKind, ItemType

# Timezone abbreviation to UTC offset mapping
TZ_OFFSETS: Dict[str, timedelta] = {
    "UTC": timedelta(hours=0),
    "GMT": timedelta(hours=0),
    "Z": timedelta(hours=0),
    "EST": timedelta(hours=-5),
    "EDT": timedelta(hours=-4),
    "CST": timedelta(hours=-6),
    "CDT": timedelta(hours=-5),
    "MST": timedelta(hours=-7),
    "MDT": timedelta(hours=-6),
    "PST": timedelta(hours=-8),
    "PDT": timedelta(hours=-7),
    "IST": timedelta(hours=5, minutes=30),
}

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9, "sept": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


class ExtractionResult(BaseModel):
    item_type: ItemType
    kind: ItemKind
    title: str
    summary: str
    required_actions: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    deadline_text: Optional[str] = None
    deadline_at: Optional[str] = None  # UTC ISO
    deadline_status: DeadlineStatus
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reasons: List[str] = Field(default_factory=list)


class ExtractionProvider(ABC):
    @abstractmethod
    def extract(
        self,
        text: str,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        received_at: Optional[datetime] = None,
        timezone_str: str = "UTC",
        now: Optional[datetime] = None,
    ) -> ExtractionResult:
        """Extract structured task/information fields from raw message text."""
        pass


class RulesProvider(ExtractionProvider):
    def extract(
        self,
        text: str,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        received_at: Optional[datetime] = None,
        timezone_str: str = "UTC",
        now: Optional[datetime] = None,
    ) -> ExtractionResult:
        ref_now = now or datetime.now(timezone.utc)
        ref_received = received_at or ref_now

        # 1. Sanitize & cap input
        cleaned_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text[:20000]).strip()
        cleaned_subject = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", (subject or "")[:500]).strip()

        reasons: List[str] = []
        required_actions: List[str] = []

        # 2. Extract links (http / https only; never fetch or render)
        links = self._extract_links(cleaned_text)

        # 3. Determine title & summary
        title = self._derive_title(cleaned_subject, cleaned_text)
        summary = self._derive_summary(cleaned_text)

        # 4. Check for instructions or "urgent" keywords in text/subject
        has_urgent_keyword = bool(
            re.search(r"\b(urgent|asap|immediately|right away)\b", f"{cleaned_subject} {cleaned_text}", re.I)
        )
        if has_urgent_keyword:
            reasons.append("Subject/body mentions urgency keyword (does not alter priority without deadline).")

        # 5. Extract deadline
        deadline_text, deadline_at, deadline_status, dl_reasons = self._parse_deadline(
            cleaned_text, ref_received
        )
        reasons.extend(dl_reasons)

        # 6. Classify kind & item_type
        item_type, kind, class_actions, class_reasons = self._classify_content(
            cleaned_subject, cleaned_text, deadline_status
        )
        required_actions.extend(class_actions)
        reasons.extend(class_reasons)

        # 7. Calculate confidence
        confidence = 0.95
        if deadline_status == DeadlineStatus.AMBIGUOUS:
            confidence -= 0.15
            reasons.append("Confidence lowered due to ambiguous deadline.")
        elif deadline_status == DeadlineStatus.NONE and item_type == ItemType.TASK:
            confidence -= 0.10
            reasons.append("Confidence lowered: actionable request has no stated deadline.")

        if len(cleaned_text) < 40:
            confidence -= 0.20
            reasons.append("Confidence lowered due to very short input text.")

        if has_urgent_keyword and item_type == ItemType.INFORMATION:
            confidence -= 0.10
            reasons.append("Confidence lowered due to conflicting urgency cue on informational text.")

        confidence = max(0.1, min(1.0, round(confidence, 2)))

        return ExtractionResult(
            item_type=item_type,
            kind=kind,
            title=title,
            summary=summary,
            required_actions=required_actions,
            links=links,
            deadline_text=deadline_text,
            deadline_at=deadline_at,
            deadline_status=deadline_status,
            extraction_confidence=confidence,
            reasons=reasons,
        )

    def _extract_links(self, text: str) -> List[str]:
        raw_links = re.findall(r"https?://[^\s<>\"']+", text)
        cleaned_links = []
        for l in raw_links:
            trimmed = l.rstrip(".,;:!?)>}\"']")
            if trimmed and trimmed not in cleaned_links:
                cleaned_links.append(trimmed)
        return cleaned_links

    def _derive_title(self, subject: str, text: str) -> str:
        if subject:
            return subject[:120]
        # First non-empty line or first sentence
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            first_line = lines[0]
            if len(first_line) > 100:
                first_line = first_line[:97] + "..."
            return first_line
        return "Untitled Item"

    def _derive_summary(self, text: str) -> str:
        # Extract first 1-2 sentences
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        meaningful = [s.strip() for s in sentences if s.strip()]
        if not meaningful:
            return text[:200]
        if len(meaningful) == 1:
            return meaningful[0][:250]
        summary = f"{meaningful[0]} {meaningful[1]}"
        return summary[:300]

    def _parse_deadline(
        self, text: str, ref_date: datetime
    ) -> Tuple[Optional[str], Optional[str], DeadlineStatus, List[str]]:
        reasons: List[str] = []

        # Check for duration phrases that are NOT deadlines
        # e.g. "90-minute exam", "takes 2 hours", "for 3 hours"
        duration_match = re.search(
            r"\b(\d+[- ]?(?:minute|min|hour|hr|day|week)[- ]?(?:exam|test|duration|break|shutoff|outage|maintenance|repair|window))\b",
            text,
            re.I,
        )
        if duration_match and not re.search(r"\b(due|deadline|by|before)\b", text, re.I):
            reasons.append(f"Detected event duration ('{duration_match.group(1)}'), not a deadline.")
            return None, None, DeadlineStatus.NONE, reasons

        # Pattern for confirmed deadline:
        # Needs explicit date + explicit time + explicit timezone
        # e.g. "Due Friday, Nov 13, 2026 at 11:59 PM EST"
        # e.g. "Submit by November 20, 2026 at 5:00 PM UTC"
        confirmed_pattern = re.compile(
            r"(?:due|deadline|by|before|at)?\s*"
            r"(?:([A-Za-z]+),\s*)?"  # Weekday
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"  # Month
            r"\s+(\d{1,2})"  # Day
            r"(?:st|nd|rd|th)?,\s*(\d{4})"  # Year
            r"\s+(?:at\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)?"  # Time
            r"\s+([A-Z]{2,4}|[+-]\d{2}:?\d{2}|Z)",  # Zone
            re.I,
        )

        match = confirmed_pattern.search(text)
        if match:
            weekday, month_str, day_str, year_str, hour_str, min_str, sec_str, ampm_str, zone_str = match.groups()
            month = MONTH_MAP.get(month_str.lower(), 1)
            day = int(day_str)
            year = int(year_str)
            hour = int(hour_str)
            minute = int(min_str)
            second = int(sec_str) if sec_str else 0

            if ampm_str:
                ampm = ampm_str.upper()
                if ampm == "PM" and hour < 12:
                    hour += 12
                elif ampm == "AM" and hour == 12:
                    hour = 0

            # Determine timezone offset
            tz_upper = zone_str.upper()
            offset = TZ_OFFSETS.get(tz_upper)
            if offset is None and re.match(r"^[+-]\d{2}:?\d{2}$", zone_str):
                sign = 1 if zone_str[0] == "+" else -1
                clean = zone_str[1:].replace(":", "")
                h_off = int(clean[:2])
                m_off = int(clean[2:4]) if len(clean) >= 4 else 0
                offset = timedelta(hours=sign * h_off, minutes=sign * m_off)

            if offset is not None:
                # We have verified date, time, and timezone!
                try:
                    local_tz = timezone(offset)
                    dt_local = datetime(year, month, day, hour, minute, second, tzinfo=local_tz)
                    dt_utc = dt_local.astimezone(timezone.utc)
                    utc_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

                    # Extract the verbatim deadline phrase
                    start_idx = max(0, match.start() - 15)
                    end_idx = min(len(text), match.end() + 5)
                    snippet = text[start_idx:end_idx].strip()
                    reasons.append(f"Found confirmed calendar deadline with explicit time and timezone ({zone_str}).")
                    return snippet, utc_iso, DeadlineStatus.CONFIRMED, reasons
                except ValueError:
                    pass

        # If not confirmed, check for ambiguous date cues or relative deadlines
        ambiguous_patterns = [
            r"(?:due|deadline|by|before)\s+([A-Za-z]+,\s*)?(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{4})?",
            r"(?:due|deadline|by|before)\s+(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+\d{1,2}(?:st|nd|rd|th)?|\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?",
            r"(?:due|deadline|by|before)\s+(today|tomorrow|tonight|this\s+weekend|next\s+week|early\s+next\s+week)",
            r"(?:within\s+\d+\s+(?:hours?|days?|weeks?))",
            r"(?:sometime\s+early\s+next\s+week(?:[^\n.]+)?)"
        ]

        for p in ambiguous_patterns:
            amb_match = re.search(p, text, re.I)
            if amb_match:
                phrase = amb_match.group(0).strip()
                reasons.append(f"Deadline phrase found ('{phrase}') but lacks explicit time or timezone; flagged as ambiguous.")
                return phrase, None, DeadlineStatus.AMBIGUOUS, reasons

        # General "due" or "deadline" word presence without date
        gen_match = re.search(r"\b(due|deadline|reply\s+by|submit\s+by|rsvp\s+by)\b[^\n.]{0,40}", text, re.I)
        if gen_match:
            phrase = gen_match.group(0).strip()
            reasons.append(f"Action timeframe found ('{phrase}') but missing specific calendar date and timezone.")
            return phrase, None, DeadlineStatus.AMBIGUOUS, reasons

        return None, None, DeadlineStatus.NONE, reasons

    def _classify_content(
        self, subject: str, text: str, deadline_status: DeadlineStatus
    ) -> Tuple[ItemType, ItemKind, List[str], List[str]]:
        combined = f"{subject} {text}".lower()
        reasons: List[str] = []
        actions: List[str] = []

        # Info override cues
        info_cues = ["no action required", "no action needed", "no response needed", "for your information", "fyi"]
        has_info_override = any(cue in combined for cue in info_cues)

        # Kind determination
        if any(w in combined for w in ["assignment", "homework", "problem set", "pset", "gradescope", "lab report"]):
            kind = ItemKind.ASSIGNMENT
        elif any(w in combined for w in ["interview", "technical screen", "coding interview", "phone screen", "interview invitation", "schedule your interview", "interview scheduling"]):
            kind = ItemKind.INTERVIEW
        elif any(w in combined for w in ["recruiter", "talent acquisition", "careers", "internship application"]):
            kind = ItemKind.RECRUITER
        elif any(w in combined for w in ["proof of", "upload a copy", "upload proof", "document request", "submit form", "verification checklist"]):
            kind = ItemKind.DOCUMENT_REQUEST
        elif any(w in combined for w in ["hackathon", "ceremony", "webinar", "conference", "kickoff"]):
            kind = ItemKind.EVENT
        elif any(w in combined for w in ["newsletter", "digest", "bi-weekly digest", "weekly bulletin", "roundup"]):
            kind = ItemKind.ROUTINE
        elif any(w in combined for w in ["maintenance", "shutoff", "advisory", "interruption", "outage", "announcement"]):
            kind = ItemKind.ANNOUNCEMENT
        elif any(w in combined for w in ["meeting", "office hours", "sync", "standup"]):
            kind = ItemKind.MEETING
        else:
            kind = ItemKind.OTHER

        # Actionable cues
        actionable_matches = re.findall(
            r"\b(submit|due\b|deadline|rsvp|reply\s+by|action\s+required|action\s+needed|please\s+upload|please\s+complete|please\s+confirm|please\s+register|please\s+schedule|register\s+for|upload\s+a\s+copy)\b",
            combined,
        )

        if has_info_override:
            item_type = ItemType.INFORMATION
            reasons.append("Identified explicit informational statement ('no action required/needed').")
        elif kind == ItemKind.ROUTINE:
            item_type = ItemType.INFORMATION
            reasons.append("Identified periodic routine publication / newsletter.")
        elif deadline_status == DeadlineStatus.CONFIRMED or deadline_status == DeadlineStatus.AMBIGUOUS:
            item_type = ItemType.TASK
            reasons.append("Identified actionable deadline or due date in text.")
            actions.append("Complete requested action before stated deadline")
        elif actionable_matches:
            item_type = ItemType.TASK
            matched_words = ", ".join(set(actionable_matches[:3]))
            reasons.append(f"Identified actionable cues: {matched_words}.")
            actions.append("Review action request and reply or submit")
        else:
            item_type = ItemType.INFORMATION
            reasons.append("No actionable requests or deadlines identified; categorized as information.")

        return item_type, kind, actions, reasons

