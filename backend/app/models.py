import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator


class ItemType(str, Enum):
    INFORMATION = "information"
    TASK = "task"


class ItemKind(str, Enum):
    ASSIGNMENT = "assignment"
    ANNOUNCEMENT = "announcement"
    RECRUITER = "recruiter"
    INTERVIEW = "interview"
    DOCUMENT_REQUEST = "document_request"
    EVENT = "event"
    MEETING = "meeting"
    ROUTINE = "routine"
    OTHER = "other"


class Priority(str, Enum):
    NORMAL = "normal"
    IMPORTANT = "important"
    URGENT = "urgent"


class ItemState(str, Enum):
    NEEDS_REVIEW = "needs_review"
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DeadlineStatus(str, Enum):
    NONE = "none"
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"


class SourceType(str, Enum):
    MANUAL = "manual"
    GMAIL = "gmail"
    CLASSROOM = "classroom"
    DEMO = "demo"


class ItemCreate(BaseModel):
    title: str = Field(..., min_length=1)
    item_type: ItemType = ItemType.TASK
    kind: ItemKind = ItemKind.OTHER
    summary: Optional[str] = None
    priority: Priority = Priority.NORMAL
    priority_reason: Optional[str] = None
    priority_override: Optional[Priority] = None
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    state: ItemState = ItemState.NEEDS_REVIEW
    deadline_text: Optional[str] = None
    deadline_at: Optional[str] = None
    deadline_status: DeadlineStatus = DeadlineStatus.NONE
    links: List[str] = Field(default_factory=list)
    source_type: SourceType = SourceType.MANUAL
    source_id: Optional[str] = None
    source_title: Optional[str] = None
    sender: Optional[str] = None
    received_at: Optional[str] = None
    evidence: Optional[str] = None
    is_demo: bool = False


class ItemUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1)
    summary: Optional[str] = None
    deadline_text: Optional[str] = None
    deadline_at: Optional[str] = None
    deadline_status: Optional[DeadlineStatus] = None
    priority_override: Optional[Priority] = None
    kind: Optional[ItemKind] = None
    item_type: Optional[ItemType] = None


class SnoozeRequest(BaseModel):
    until: str = Field(..., description="UTC ISO timestamp until which item is snoozed")

    @field_validator("until")
    def validate_until(cls, v: str) -> str:
        try:
            # Normalize trailing 'Z' if present
            clean_v = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            raise ValueError("until must be a valid ISO timestamp")


class ItemResponse(BaseModel):
    id: int
    title: str
    item_type: ItemType
    kind: ItemKind
    summary: Optional[str] = None
    priority: Priority
    priority_reason: Optional[str] = None
    priority_override: Optional[Priority] = None
    effective_priority: Priority
    extraction_confidence: float
    state: ItemState
    acknowledged_at: Optional[str] = None
    snoozed_until: Optional[str] = None
    deadline_text: Optional[str] = None
    deadline_at: Optional[str] = None
    deadline_status: DeadlineStatus
    links: List[str] = Field(default_factory=list)
    source_type: SourceType
    source_id: Optional[str] = None
    source_title: Optional[str] = None
    sender: Optional[str] = None
    received_at: Optional[str] = None
    evidence: Optional[str] = None
    is_demo: bool
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ItemResponse":
        raw_links = row.get("links")
        if isinstance(raw_links, str):
            try:
                links_list = json.loads(raw_links)
            except Exception:
                links_list = []
        elif isinstance(raw_links, list):
            links_list = raw_links
        else:
            links_list = []

        override = row.get("priority_override")
        priority = row["priority"]
        effective_priority = override if override is not None else priority

        return cls(
            id=row["id"],
            title=row["title"],
            item_type=row["item_type"],
            kind=row["kind"],
            summary=row.get("summary"),
            priority=priority,
            priority_reason=row.get("priority_reason"),
            priority_override=override,
            effective_priority=effective_priority,
            extraction_confidence=float(row.get("extraction_confidence", 1.0)),
            state=row["state"],
            acknowledged_at=row.get("acknowledged_at"),
            snoozed_until=row.get("snoozed_until"),
            deadline_text=row.get("deadline_text"),
            deadline_at=row.get("deadline_at"),
            deadline_status=row.get("deadline_status", "none"),
            links=links_list,
            source_type=row["source_type"],
            source_id=row.get("source_id"),
            source_title=row.get("source_title"),
            sender=row.get("sender"),
            received_at=row.get("received_at"),
            evidence=row.get("evidence"),
            is_demo=bool(row.get("is_demo", 0)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row.get("completed_at"),
        )

