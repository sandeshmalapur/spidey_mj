import html
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, model_validator

import app.config
from app.config import DEFAULT_IST_MEANING, TIMEZONE
from app.models import DeadlineStatus, ItemKind, ItemType

MONTH_MAP: Dict[str, int] = {
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

WEEKDAY_MAP: Dict[str, int] = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

FIXED_ZONES: Dict[str, timedelta] = {
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
}

DATE_CANDIDATE_RE = re.compile(
    r"(?i)"
    r"(?:(?P<label>due|deadline|submit\s+by|submission\s+by|proposed\s+time|proposed\s+date|scheduled(?:\s+for)?|interview\s+on|interview\s+at|interview\s+time|meeting\s+at|meeting\s+on|meeting\s+time|rsvp\s+by|reply\s+by|by|before)[\s:]+)?"
    r"(?P<phrase>"
    r"(?:(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,\s]+)?"
    r"(?:"
    r'(?P<m1>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(?P<y1>\d{4}))?'
    r"|"
    r'(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m2>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?:[,\s]+(?P<y2>\d{4}))?'
    r"|"
    r"(?P<iso_y>\d{4})-(?P<iso_m>\d{2})-(?P<iso_d>\d{2})"
    r"|"
    r"(?P<num_p1>\d{1,2})[/-](?P<num_p2>\d{1,2})[/-](?P<num_y>\d{4})"
    r")"
    r"(?:"
    r"[,\s]+(?:(?:at|@|-)\s+)?"
    r"(?P<start_h>\d{1,2})(?::(?P<start_m>\d{2}))?(?::(?P<start_s>\d{2}))?"
    r"(?:\s*(?P<start_ampm>AM|PM|am|pm))?"
    r"(?:"
    r"\s*(?:-|–|to)\s*"
    r"(?P<end_h>\d{1,2})(?::(?P<end_m>\d{2}))?(?:\s*(?P<end_ampm>AM|PM|am|pm))?"
    r")?"
    r"(?:\s*(?P<hrs>hrs|hours))?"
    r"(?:\s+(?P<zone>[A-Z]{2,5}|(?:GMT|UTC)?[+-]\d{1,2}(?::\d{2})?|Z))?"
    r")?"
    r")"
    r"(?:\b|(?=https?://)|$)"
)


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

    @model_validator(mode="after")
    def validate_consistency(self) -> "ExtractionResult":
        # Kind and item_type must be consistent
        if self.required_actions and self.item_type != ItemType.TASK:
            self.item_type = ItemType.TASK
        if self.kind in (ItemKind.INTERVIEW, ItemKind.MEETING, ItemKind.EVENT) and "Confirm attendance" in self.required_actions:
            self.item_type = ItemType.TASK
        return self


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


def normalize_text_for_parsing(text: str) -> str:
    """Normalize input text for parsing (strips markdown asterisks/underscores,

    HTML entities, and zero-width characters, collapses whitespace).
    NOTE: Stored evidence remains unmodified.
    """
    cleaned = re.sub(r"[\u200b-\u200f\ufeff\u00ad\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[*_]+", " ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def resolve_zone_offset(zone_str: Optional[str], dt_local: datetime) -> Tuple[Optional[timedelta], bool]:
    """Resolve timezone abbreviation to UTC offset.

    Returns (offset, is_ambiguous).
    """
    if not zone_str:
        return None, True
    z_clean = zone_str.strip().upper()
    if z_clean in FIXED_ZONES:
        return FIXED_ZONES[z_clean], False

    if z_clean == "IST":
        meaning = os.getenv("DEFAULT_IST_MEANING") or getattr(app.config, "DEFAULT_IST_MEANING", "Asia/Kolkata")
        try:
            zi = ZoneInfo(meaning)
            off = zi.utcoffset(dt_local)
            if off is not None:
                return off, False
        except Exception:
            pass
        return timedelta(hours=5, minutes=30), False

    # Numeric offset like +05:30, -04:00, GMT+5:30, UTC-5, +05, -5
    m_off = re.match(r"^(?:GMT|UTC)?([+-])(\d{1,2})(?::?(\d{2}))?$", z_clean)
    if m_off:
        sign = 1 if m_off.group(1) == "+" else -1
        h = int(m_off.group(2))
        m = int(m_off.group(3) or 0)
        return timedelta(hours=sign * h, minutes=sign * m), False

    # Any other ambiguous or unknown zone abbreviation (BST, AEST, etc.) -> ambiguous
    return None, True


class ParsedCandidate:
    def __init__(
        self,
        phrase: str,
        label: Optional[str],
        status: DeadlineStatus,
        dt_utc: Optional[str] = None,
        reason: str = "",
    ):
        self.phrase = phrase.strip().rstrip(".,;:!?)>}\"']")
        self.label = label.strip() if label else None
        self.status = status
        self.dt_utc = dt_utc
        self.reason = reason


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

        # Pre-normalize for parsing (without modifying the stored text)
        norm_text = normalize_text_for_parsing(cleaned_text)
        norm_subject = normalize_text_for_parsing(cleaned_subject)

        reasons: List[str] = []
        required_actions: List[str] = []

        # 2. Extract links (http / https only; never fetch or render)
        links = self._extract_links(cleaned_text)

        # 3. Determine title & summary
        title = self._derive_title(cleaned_subject, norm_text)
        summary = self._derive_summary(norm_text)

        # 4. Check for instructions or "urgent" keywords in text/subject
        has_urgent_keyword = bool(
            re.search(r"\b(urgent|asap|immediately|right away)\b", f"{norm_subject} {norm_text}", re.I)
        )
        if has_urgent_keyword:
            reasons.append("Subject/body mentions urgency keyword (does not alter priority without deadline).")

        # 5. Extract deadline using hardened candidate parser
        deadline_text, deadline_at, deadline_status, dl_reasons = self._parse_deadline(
            norm_text, ref_received
        )
        reasons.extend(dl_reasons)

        # 6. Classify kind & item_type
        item_type, kind, class_actions, class_reasons = self._classify_content(
            norm_subject, norm_text, deadline_status, links
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

        if len(norm_text) < 40:
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
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            first_line = lines[0]
            if len(first_line) > 100:
                first_line = first_line[:97] + "..."
            return first_line
        return "Untitled Item"

    def _derive_summary(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        meaningful = [s.strip() for s in sentences if s.strip()]
        if not meaningful:
            return text[:200]
        if len(meaningful) == 1:
            return meaningful[0][:250]
        summary = f"{meaningful[0]} {meaningful[1]}"
        return summary[:300]

    def _parse_deadline(
        self, norm_text: str, ref_date: datetime
    ) -> Tuple[Optional[str], Optional[str], DeadlineStatus, List[str]]:
        reasons: List[str] = []

        # Check for duration phrases that are NOT deadlines
        # e.g. "90-minute exam", "takes 2 hours", "for 3 hours"
        duration_match = re.search(
            r"\b(\d+[- ]?(?:minute|min|hour|hr|day|week)[- ]?(?:exam|test|duration|break|shutoff|outage|maintenance|repair|window))\b",
            norm_text,
            re.I,
        )
        if duration_match and not re.search(r"\b(due|deadline|by|before|scheduled|proposed|interview)\b", norm_text, re.I):
            reasons.append(f"Detected event duration ('{duration_match.group(1)}'), not a deadline.")
            return None, None, DeadlineStatus.NONE, reasons

        candidates: List[ParsedCandidate] = []

        for m in DATE_CANDIDATE_RE.finditer(norm_text):
            raw_phrase = m.group("phrase")
            label = m.group("label")
            weekday = m.group("weekday")

            year: Optional[int] = None
            month: Optional[int] = None
            day: Optional[int] = None
            is_num_ambiguous = False

            if m.group("m1"):
                month = MONTH_MAP[m.group("m1").lower()]
                day = int(m.group("d1"))
                year = int(m.group("y1")) if m.group("y1") else None
            elif m.group("d2"):
                month = MONTH_MAP[m.group("m2").lower()]
                day = int(m.group("d2"))
                year = int(m.group("y2")) if m.group("y2") else None
            elif m.group("iso_y"):
                year = int(m.group("iso_y"))
                month = int(m.group("iso_m"))
                day = int(m.group("iso_d"))
            elif m.group("num_p1"):
                p1 = int(m.group("num_p1"))
                p2 = int(m.group("num_p2"))
                year = int(m.group("num_y"))
                if p1 > 12 and p2 <= 12:
                    day, month = p1, p2
                elif p2 > 12 and p1 <= 12:
                    month, day = p1, p2
                else:
                    is_num_ambiguous = True

            if is_num_ambiguous:
                candidates.append(
                    ParsedCandidate(
                        raw_phrase,
                        label,
                        DeadlineStatus.AMBIGUOUS,
                        reason="Numeric date dd/mm vs mm/dd is ambiguous.",
                    )
                )
                continue

            if not month or not day:
                continue

            # Missing year check (Requirement 4)
            if year is None:
                if weekday:
                    w_target = WEEKDAY_MAP[weekday.lower()]
                    matched_year = None
                    for y_cand in [ref_date.year, ref_date.year + 1]:
                        try:
                            cand_dt = datetime(y_cand, month, day)
                            diff = cand_dt.date() - ref_date.date()
                            if 0 <= diff.days <= 366 and cand_dt.weekday() == w_target:
                                matched_year = y_cand
                                break
                        except ValueError:
                            pass
                    if matched_year:
                        year = matched_year
                    else:
                        candidates.append(
                            ParsedCandidate(
                                raw_phrase,
                                label,
                                DeadlineStatus.AMBIGUOUS,
                                reason="Missing year; weekday does not match next occurrence within 12 months.",
                            )
                        )
                        continue
                else:
                    candidates.append(
                        ParsedCandidate(
                            raw_phrase,
                            label,
                            DeadlineStatus.AMBIGUOUS,
                            reason="Missing year without weekday.",
                        )
                    )
                    continue

            # Construct calendar date and check weekday consistency (Requirement 3)
            try:
                date_obj = datetime(year, month, day)
            except ValueError:
                candidates.append(
                    ParsedCandidate(
                        raw_phrase,
                        label,
                        DeadlineStatus.AMBIGUOUS,
                        reason="Invalid calendar date.",
                    )
                )
                continue

            if weekday:
                if date_obj.weekday() != WEEKDAY_MAP[weekday.lower()]:
                    candidates.append(
                        ParsedCandidate(
                            raw_phrase,
                            label,
                            DeadlineStatus.AMBIGUOUS,
                            reason="weekday and date conflict",
                        )
                    )
                    continue

            # Check time
            if not m.group("start_h"):
                # Date found without explicit time
                candidates.append(
                    ParsedCandidate(
                        raw_phrase,
                        label,
                        DeadlineStatus.AMBIGUOUS,
                        reason="Date without explicit time.",
                    )
                )
                continue

            sh = int(m.group("start_h"))
            sm = int(m.group("start_m") or 0)
            ss = int(m.group("start_s") or 0)
            sa = m.group("start_ampm")
            ea = m.group("end_ampm")
            ampm = sa or ea
            if ampm:
                if ampm.upper() == "PM" and sh < 12:
                    sh += 12
                elif ampm.upper() == "AM" and sh == 12:
                    sh = 0

            # Check zone
            zone_str = m.group("zone")
            if not zone_str:
                candidates.append(
                    ParsedCandidate(
                        raw_phrase,
                        label,
                        DeadlineStatus.AMBIGUOUS,
                        reason="Missing timezone.",
                    )
                )
                continue

            dt_approx = datetime(year, month, day, sh, sm, ss)
            offset, is_amb_zone = resolve_zone_offset(zone_str, dt_approx)
            if is_amb_zone or offset is None:
                candidates.append(
                    ParsedCandidate(
                        raw_phrase,
                        label,
                        DeadlineStatus.AMBIGUOUS,
                        reason=f"Ambiguous or unknown timezone abbreviation ('{zone_str}').",
                    )
                )
                continue

            tz_local = timezone(offset)
            dt_local = datetime(year, month, day, sh, sm, ss, tzinfo=tz_local)
            dt_utc = dt_local.astimezone(timezone.utc)
            utc_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            candidates.append(
                ParsedCandidate(
                    raw_phrase,
                    label,
                    DeadlineStatus.CONFIRMED,
                    dt_utc=utc_iso,
                    reason=f"Confirmed deadline with timezone {zone_str}.",
                )
            )

        # Also check ambiguous relative patterns if no candidate found
        if not candidates:
            ambiguous_patterns = [
                r"\b(sometime\s+(?:early\s+|late\s+)?next\s+week)\b",
                r"(?:due|deadline|by|before|reply\s+by)\s+((?:today|tomorrow|tonight|this\s+weekend|next\s+week|early\s+next\s+week|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:[,\s]+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)?)?))\b",
                r"\b((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b",
                r"(?:within\s+\d+\s+(?:hours?|days?|weeks?))",
            ]
            for p in ambiguous_patterns:
                amb_m = re.search(p, norm_text, re.I)
                if amb_m:
                    phrase = (amb_m.group(1) if amb_m.lastindex and amb_m.group(1) else amb_m.group(0)).strip().rstrip(".,;:!?)>}\"']")
                    if phrase.lower() not in {"deadline", "due", "by", "before", "at", "none", "null"}:
                        candidates.append(
                            ParsedCandidate(
                                phrase,
                                None,
                                DeadlineStatus.AMBIGUOUS,
                                reason=f"Deadline phrase found ('{phrase}') but lacks explicit calendar date/time.",
                            )
                        )
                        break

        if not candidates:
            return None, None, DeadlineStatus.NONE, reasons

        # Multiple dates / conflict resolution (Requirement 5)
        confirmed_candidates = [c for c in candidates if c.status == DeadlineStatus.CONFIRMED]
        labeled_confirmed = [c for c in confirmed_candidates if c.label]

        if len(labeled_confirmed) > 1:
            distinct_utcs = {c.dt_utc for c in labeled_confirmed}
            if len(distinct_utcs) > 1:
                # Conflicting labeled confirmed dates -> ambiguous!
                conflict_desc = " vs ".join(f"'{c.phrase}'" for c in labeled_confirmed[:2])
                reasons.append(f"Conflicting labeled dates found: {conflict_desc}.")
                return labeled_confirmed[0].phrase, None, DeadlineStatus.AMBIGUOUS, reasons
            # If they all agree on the same UTC, use the first
            reasons.append(labeled_confirmed[0].reason)
            return labeled_confirmed[0].phrase, labeled_confirmed[0].dt_utc, DeadlineStatus.CONFIRMED, reasons

        if len(labeled_confirmed) == 1:
            chosen = labeled_confirmed[0]
            reasons.append(chosen.reason)
            return chosen.phrase, chosen.dt_utc, DeadlineStatus.CONFIRMED, reasons

        if confirmed_candidates:
            # Check for conflict among unlabeled confirmed dates
            distinct_utcs = {c.dt_utc for c in confirmed_candidates}
            if len(distinct_utcs) > 1:
                conflict_desc = " vs ".join(f"'{c.phrase}'" for c in confirmed_candidates[:2])
                reasons.append(f"Conflicting dates found: {conflict_desc}.")
                return confirmed_candidates[0].phrase, None, DeadlineStatus.AMBIGUOUS, reasons
            chosen = confirmed_candidates[0]
            reasons.append(chosen.reason)
            return chosen.phrase, chosen.dt_utc, DeadlineStatus.CONFIRMED, reasons

        # Only ambiguous candidates found
        amb_candidate = candidates[0]
        if amb_candidate.reason:
            reasons.append(amb_candidate.reason)
        return amb_candidate.phrase, None, DeadlineStatus.AMBIGUOUS, reasons

    def _classify_content(
        self, subject: str, text: str, deadline_status: DeadlineStatus, links: List[str]
    ) -> Tuple[ItemType, ItemKind, List[str], List[str]]:
        combined = f"{subject} {text}".lower()
        reasons: List[str] = []
        actions: List[str] = []

        # 1. Automated notices check (Requirement 6)
        automated_cues = [
            "security alert", "new sign-in", "sign-in attempt", "signin attempt",
            "password changed", "account-sharing notice", "account sharing notice",
            "two-factor authentication", "2-step verification", "verification code",
            "receipt for", "your receipt", "order confirmation", "payment received",
            "invoice #", "billing receipt", "statement is ready",
        ]
        if any(cue in combined for cue in automated_cues):
            reasons.append("Identified automated system notice / security alert; categorized as information.")
            return ItemType.INFORMATION, ItemKind.ANNOUNCEMENT, [], reasons

        # 2. Check for interview / meeting / event invitations (Requirement 6)
        has_meeting_link = any(
            re.search(r"https?://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|[\w.-]*webex\.com)/\S+", l)
            for l in links
        ) or bool(
            re.search(r"https?://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|[\w.-]*webex\.com)/\S+", text, re.I)
        )

        is_interview = bool(
            re.search(
                r"\b(interview|interviews|technical screen|tech screen|coding screen|phone screen)\b",
                combined,
                re.I,
            )
        )
        is_meeting = bool(
            re.search(
                r"\b(meeting invitation|invitation to meet|sync|standup|office hours|proposed time)\b",
                combined,
                re.I,
            )
        )
        is_event = bool(
            re.search(
                r"\b(hackathon|webinar|conference|workshop|ceremony|kickoff)\b",
                combined,
                re.I,
            )
        )

        # An invitation with a proposed time or meeting link is an actionable task
        if (is_interview or is_meeting or is_event or has_meeting_link) and (
            deadline_status != DeadlineStatus.NONE or has_meeting_link or "invitation" in combined or "proposed time" in combined
        ):
            if is_interview:
                kind = ItemKind.INTERVIEW
                reasons.append("Identified interview invitation with proposed time or meeting link.")
            elif is_event:
                kind = ItemKind.EVENT
                reasons.append("Identified event invitation with scheduled time.")
            else:
                kind = ItemKind.MEETING
                reasons.append("Identified meeting invitation with proposed time or meeting link.")

            actions.append("Confirm attendance")
            return ItemType.TASK, kind, actions, reasons

        # 3. Explicit info overrides
        info_cues = ["no action required", "no action needed", "no response needed", "for your information", "fyi"]
        has_info_override = any(cue in combined for cue in info_cues)

        # 4. Standard kind classification
        if any(w in combined for w in ["assignment", "homework", "problem set", "pset", "gradescope", "lab report"]):
            kind = ItemKind.ASSIGNMENT
        elif is_interview:
            kind = ItemKind.INTERVIEW
        elif any(w in combined for w in ["recruiter", "talent acquisition", "careers", "internship application"]):
            kind = ItemKind.RECRUITER
        elif any(w in combined for w in ["proof of", "upload a copy", "upload proof", "document request", "submit form", "verification checklist"]):
            kind = ItemKind.DOCUMENT_REQUEST
        elif is_event:
            kind = ItemKind.EVENT
        elif any(w in combined for w in ["newsletter", "digest", "bi-weekly digest", "weekly bulletin", "roundup"]):
            kind = ItemKind.ROUTINE
        elif any(w in combined for w in ["maintenance", "shutoff", "advisory", "interruption", "outage", "announcement"]):
            kind = ItemKind.ANNOUNCEMENT
        elif is_meeting:
            kind = ItemKind.MEETING
        else:
            kind = ItemKind.OTHER

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
        elif deadline_status in (DeadlineStatus.CONFIRMED, DeadlineStatus.AMBIGUOUS):
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
