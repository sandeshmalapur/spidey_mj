"""
Sarvam AI Voice Call integration for ClassMail J.

Triggers an outbound voice call when an item reaches 'urgent' priority.
Sarvam AI is an Indian voice AI platform (https://www.sarvam.ai/) with
support for Hindi and English.

API reference: https://docs.sarvam.ai/api-reference/telephony
Configuration (all via environment variables — see .env.example):
  SARVAM_VOICE_ENABLED  - master toggle (default false)
  SARVAM_API_KEY        - Sarvam AI API key (required when enabled)
  SARVAM_FROM_NUMBER    - Sarvam-provisioned caller number (required when enabled)
  SARVAM_TO_NUMBER      - recipient phone number E.164 e.g. +919876543210
  SARVAM_AGENT_ID       - optional pre-built agent id; falls back to TTS prompt
  SARVAM_MAX_CALLS_PER_HOUR - rate limit (default 3)

NOTE: Sarvam AI telephony API details were sourced from their public docs
(https://docs.sarvam.ai). If the endpoint path or payload schema changes,
update SARVAM_CALLS_URL and the _build_payload() method accordingly.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Official Sarvam AI telephony endpoint (verify against docs.sarvam.ai/api-reference/telephony)
SARVAM_CALLS_URL = "https://api.sarvam.ai/v1/calls"

# In-process rate-limit tracker: list of UNIX timestamps of recent calls
_recent_call_times: list[float] = []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_voice_enabled() -> bool:
    """Return True only when all required config is present and feature is on."""
    return bool(
        settings.SARVAM_VOICE_ENABLED
        and settings.SARVAM_API_KEY
        and settings.SARVAM_FROM_NUMBER
        and settings.SARVAM_TO_NUMBER
    )


def get_voice_status() -> dict:
    """Return a status dict for the /api/sarvam/status endpoint."""
    return {
        "enabled": is_voice_enabled(),
        "configured": bool(
            settings.SARVAM_API_KEY
            and settings.SARVAM_FROM_NUMBER
            and settings.SARVAM_TO_NUMBER
        ),
        "max_calls_per_hour": settings.SARVAM_MAX_CALLS_PER_HOUR,
        "calls_in_last_hour": _calls_in_last_hour(),
    }


def trigger_urgent_voice_call(
    item_id: int,
    item_title: str,
    deadline_label: str = "",
    *,
    dry_run: bool = False,
) -> dict:
    """
    Attempt to place a voice call alerting the user about an urgent item.

    Parameters
    ----------
    item_id:        DB id of the item (used for logging / dedup).
    item_title:     Human-readable title of the item.
    deadline_label: Optional deadline string e.g. "due in 2 hours".
    dry_run:        If True, build the payload but do NOT send (for tests).

    Returns
    -------
    dict with keys: success, call_id (optional), error (optional), skipped (bool).
    """
    if not is_voice_enabled():
        return {"success": False, "skipped": True, "reason": "voice_calls_disabled"}

    if not _within_rate_limit():
        logger.warning(
            "sarvam_voice: rate limit reached (%d/hr), skipping call for item %d",
            settings.SARVAM_MAX_CALLS_PER_HOUR,
            item_id,
        )
        return {"success": False, "skipped": True, "reason": "rate_limit_exceeded"}

    payload = _build_payload(item_title, deadline_label)
    logger.info("sarvam_voice: placing call for item %d — %r", item_id, item_title)

    if dry_run:
        return {"success": True, "skipped": False, "dry_run": True, "payload": payload}

    try:
        resp = httpx.post(
            SARVAM_CALLS_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.SARVAM_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        call_id = data.get("call_id") or data.get("id")
        _record_call()
        logger.info("sarvam_voice: call placed, call_id=%s", call_id)
        return {"success": True, "skipped": False, "call_id": call_id}
    except httpx.HTTPStatusError as exc:
        logger.error(
            "sarvam_voice: HTTP %d from Sarvam API: %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return {"success": False, "skipped": False, "error": str(exc)}
    except httpx.RequestError as exc:
        logger.error("sarvam_voice: request error: %s", exc)
        return {"success": False, "skipped": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_payload(item_title: str, deadline_label: str) -> dict:
    """
    Build the JSON payload for a Sarvam AI outbound call.

    Sarvam AI telephony API accepts:
      from_phone_number, to_phone_number, agent_id (optional),
      and additional TTS/prompt fields when no agent_id is set.

    Payload schema verified against: https://docs.sarvam.ai/api-reference/telephony
    Items marked [UNVERIFIED] could not be confirmed from docs — adjust if needed.
    """
    deadline_part = f" {deadline_label}" if deadline_label else ""
    message = (
        f"Urgent alert from ClassMail J. "
        f"You have an urgent academic item: {item_title}{deadline_part}. "
        f"Please check ClassMail J immediately."
    )

    payload: dict = {
        "from_phone_number": settings.SARVAM_FROM_NUMBER,
        "to_phone_number": settings.SARVAM_TO_NUMBER,
    }

    if settings.SARVAM_AGENT_ID:
        payload["agent_id"] = settings.SARVAM_AGENT_ID
        # [UNVERIFIED] dynamic variable injection into agent script
        payload["dynamic_variables"] = {
            "item_title": item_title,
            "deadline": deadline_label or "unknown",
            "urgency_message": message,
        }
    else:
        # Fallback: simple TTS prompt — [UNVERIFIED] field name may differ
        payload["initial_message"] = message

    return payload


def _calls_in_last_hour() -> int:
    _prune_old_calls()
    return len(_recent_call_times)


def _within_rate_limit() -> bool:
    return _calls_in_last_hour() < settings.SARVAM_MAX_CALLS_PER_HOUR


def _record_call() -> None:
    _recent_call_times.append(time.monotonic())


def _prune_old_calls() -> None:
    cutoff = time.monotonic() - 3600
    _recent_call_times[:] = [t for t in _recent_call_times if t > cutoff]

