import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, Union

from app.config import IMPORTANT_WITHIN_DAYS, URGENT_WITHIN_HOURS
from app.models import DeadlineStatus, ItemState, ItemType, Priority


@dataclass
class PriorityConfig:
    urgent_within_hours: int = URGENT_WITHIN_HOURS  # demo default: 48 hours
    important_within_days: int = IMPORTANT_WITHIN_DAYS  # demo default: 14 days


def recompute_priority(
    item: Union[Dict[str, Any], Any],
    now: Optional[datetime] = None,
    config: Optional[PriorityConfig] = None,
) -> Tuple[Priority, str]:
    """Pure deterministic priority calculation.
    
    Returns (priority, priority_reason). Note that priority_override
    is handled separately at the presentation layer (effective_priority).
    """
    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    cfg = config or PriorityConfig()

    def get_field(name: str, default=None):
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    state = get_field("state", ItemState.NEEDS_REVIEW)
    if isinstance(state, ItemState):
        state = state.value

    item_type = get_field("item_type", ItemType.TASK)
    if isinstance(item_type, ItemType):
        item_type = item_type.value

    kind = get_field("kind", "")
    if hasattr(kind, "value"):
        kind = kind.value
    is_event = str(kind).lower() in ("interview", "meeting", "event")

    deadline_status = get_field("deadline_status", DeadlineStatus.NONE)
    if isinstance(deadline_status, DeadlineStatus):
        deadline_status = deadline_status.value

    deadline_at_str = get_field("deadline_at")
    title = get_field("title", "") or ""
    evidence = get_field("evidence", "") or ""
    summary = get_field("summary", "") or ""

    # 1. Completed / Cancelled items are always Normal
    if state == ItemState.COMPLETED.value:
        return Priority.NORMAL, "Completed items marked normal"
    if state == ItemState.CANCELLED.value:
        return Priority.NORMAL, "Cancelled items marked normal"

    # 2. Confirmed deadline logic
    if deadline_status == DeadlineStatus.CONFIRMED.value and deadline_at_str:
        try:
            # Parse UTC ISO string
            clean_str = deadline_at_str.replace("Z", "+00:00")
            deadline_dt = datetime.fromisoformat(clean_str)
            if deadline_dt.tzinfo is None:
                deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)

            diff = deadline_dt - ref_now

            if diff.total_seconds() < 0:
                if is_event:
                    return Priority.NORMAL, "Event time has passed"
                return Priority.URGENT, "Overdue"
            elif diff <= timedelta(hours=cfg.urgent_within_hours):
                hours = max(1, int(diff.total_seconds() // 3600))
                if is_event:
                    return Priority.URGENT, f"Event starts within {hours} hours (under {cfg.urgent_within_hours}h threshold)"
                return Priority.URGENT, f"Due within {hours} hours (under {cfg.urgent_within_hours}h threshold)"
            elif diff <= timedelta(days=cfg.important_within_days):
                days = max(1, int(diff.days))
                return Priority.IMPORTANT, f"Due in {days} days (under {cfg.important_within_days}d threshold)"
            else:
                return Priority.NORMAL, f"Deadline is more than {cfg.important_within_days} days away"
        except Exception:
            pass

    # 3. Ambiguous deadline or actionable item with no deadline
    if deadline_status == DeadlineStatus.AMBIGUOUS.value:
        # NEVER urgent from an ambiguous date
        return Priority.IMPORTANT, "Deadline needs confirmation"

    if item_type == ItemType.TASK.value:
        return Priority.IMPORTANT, "Action requested, no deadline"

    # 4. Information items
    if item_type == ItemType.INFORMATION.value:
        combined = f"{title} {summary} {evidence}".lower()
        change_cues = re.search(r"\b(cancelled|canceled|rescheduled|postponed|moved|extended|revised deadline)\b", combined)
        if change_cues:
            return Priority.IMPORTANT, f"Schedule change or revised announcement ('{change_cues.group(1)}')"

        if deadline_status == DeadlineStatus.CONFIRMED.value and deadline_at_str:
            try:
                clean_str = deadline_at_str.replace("Z", "+00:00")
                deadline_dt = datetime.fromisoformat(clean_str)
                if deadline_dt.tzinfo is None:
                    deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
                diff = deadline_dt - ref_now
                if timedelta(0) <= diff <= timedelta(hours=cfg.urgent_within_hours):
                    return Priority.URGENT, f"Event occurs within {cfg.urgent_within_hours} hours"
            except Exception:
                pass

        return Priority.NORMAL, "Informational announcement"

    return Priority.NORMAL, "Standard priority"

