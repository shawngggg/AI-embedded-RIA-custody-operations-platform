"""
SLA clocks (walkthrough note 15, Nov. 1, 2026). An operations SLA counts business hours on the queue's calendar
(custody/calendar.py), Eastern time; weekends and that calendar's holidays don't count.

- A same-day SLA (under 24 hours: the 4-hour release, the 8-hour callback) counts the operating day, 8:00 a.m. to
  6:00 p.m. Eastern. A release that arrives at 5:00 p.m. on a Friday has one hour on Friday and falls due at 11:00 a.m.
  Monday, not 9:00 p.m. Friday.
- A multi-day SLA (24 hours or more) counts whole business days: 24 hours is one business day, so a sanctions review
  that arrives at 10:00 a.m. Friday falls due at 10:00 a.m. Monday. One that arrives over a weekend starts Monday.
- Asset transfer's ACATS queues run on the NSCC calendar (ACATS runs on NSCC days); everything else on the Federal
  Reserve calendar. The operating day is the platform's convention, not a rule: it isn't a team roster or shifts.

A case waiting on the RIA or the client (a queue with "waits_on") has no SLA of the custodian's own: it shows when the
reminders go and when the request is withdrawn (app/nigo.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from custody.calendar import is_bday

from .queues import QUEUES

ET = ZoneInfo("America/New_York")
OPEN_HOUR, CLOSE_HOUR = 8, 18                  # the operating day for same-day SLAs, Eastern time
STEP = timedelta(minutes=15)
QUEUE_CALENDAR = {"transfer_exceptions": "nscc", "in_transit": "nscc", "transfers_waiting": "nscc"}
DEFAULT_CALENDAR = "fed"


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _like(t: datetime, original: datetime) -> datetime:
    """Hand back a naive UTC time when the caller gave one (the database stores naive UTC on SQLite)."""
    return t if original.tzinfo else t.astimezone(timezone.utc).replace(tzinfo=None)


def calendar(queue: str | None) -> str:
    return QUEUE_CALENDAR.get(queue or "", DEFAULT_CALENDAR)


def same_day(queue: str | None) -> bool:
    return bool(queue) and QUEUES[queue]["sla_hours"] < 24


def _counts(t: datetime, queue: str | None) -> bool:
    """Whether the clock runs in the quarter hour starting at t."""
    local = t.astimezone(ET)
    if not is_bday(local.date(), calendar(queue)):
        return False
    return OPEN_HOUR <= local.hour < CLOSE_HOUR if same_day(queue) else True


def _walk(start: datetime, quarters: int, queue: str | None, direction: int = 1) -> datetime:
    cursor, left, guard = _aware(start), quarters, 0
    while left > 0 and guard < 4 * 24 * 90:
        probe = cursor if direction > 0 else cursor - STEP
        if _counts(probe, queue):
            left -= 1
        cursor = cursor + STEP if direction > 0 else cursor - STEP
        guard += 1
    return cursor


def due(start: datetime, queue: str | None) -> datetime | None:
    """When a case entering `queue` at `start` falls due."""
    if not queue or QUEUES[queue].get("waits_on"):
        return None
    return _like(_walk(start, int(QUEUES[queue]["sla_hours"] * 4), queue), start)


def back(end: datetime, hours: float, queue: str | None = None) -> datetime:
    """The time `hours` business hours before `end` on the queue's clock. The seed ages cases on an operations queue
    this way, so the demo shows the same SLA picture on a Monday morning as on a Thursday."""
    return _like(_walk(end, int(hours * 4), queue, -1), end)


def business_hours_between(a: datetime, b: datetime, queue: str | None = None) -> float:
    """Business hours from a to b on the queue's clock, for "Xh left" on the screens."""
    cursor, end, n, guard = _aware(a), _aware(b), 0, 0
    while cursor < end and guard < 4 * 24 * 90:
        if _counts(cursor, queue):
            n += 1
        cursor += STEP
        guard += 1
    return n / 4


def state(case, now: datetime | None = None) -> dict:
    """What the queues, the Work page, and the case show about the clock."""
    now = now or datetime.now(timezone.utc)
    spec = QUEUES.get(case.queue or "", {})
    if spec.get("waits_on"):
        from .nigo import REMIND_DAYS, WAITING, WITHDRAW_DAYS
        since = _aware(case.queue_entered_at).date() if case.queue_entered_at else None
        withdraws = case.status in WAITING.get(case.kind, ())
        out = {"due": None, "breached": False, "hours_left": None, "waiting_on": spec["waits_on"],
               "since": since.isoformat() if since else None, "next_reminder": None, "withdraw_on": None}
        if since and withdraws:
            today = now.date()
            reminders = [since + timedelta(days=d) for d in REMIND_DAYS]
            nxt = next((r for r in reminders if r >= today), None)
            out["next_reminder"] = nxt.isoformat() if nxt else None
            out["withdraw_on"] = (since + timedelta(days=WITHDRAW_DAYS)).isoformat()
        return out
    if not case.sla_due:
        return {"due": None, "breached": False, "hours_left": None}
    d = _aware(case.sla_due)
    left = (d - now).total_seconds() / 3600
    return {"due": d.isoformat(), "breached": left < 0, "hours_left": round(left, 1),
            "business_hours_left": business_hours_between(now, d, case.queue) if left > 0 else None,
            "clock": "operating day, 8 a.m. to 6 p.m. ET" if same_day(case.queue) else "business days",
            "calendar": calendar(case.queue)}


def on(d: date, queue: str | None = None) -> bool:
    return is_bday(d, calendar(queue))
