"""Business days: weekends and the fixed-date market holidays, without weekend observance or floating holidays."""
from __future__ import annotations

from datetime import date, timedelta

HOLIDAYS = {(1, 1), (6, 19), (7, 4), (12, 25)}


def is_bday(d: date) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in HOLIDAYS


def bday(d: date, n: int = 0) -> date:
    """The business day n business days from d (n may be negative); n=0 rolls a non-business day back."""
    while not is_bday(d):
        d -= timedelta(days=1)
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        d += timedelta(days=step)
        while not is_bday(d):
            d += timedelta(days=step)
    return d


def next_bday(d: date) -> date:
    """d itself on a business day, otherwise the next one."""
    while not is_bday(d):
        d += timedelta(days=1)
    return d


def add_bdays(d: date, n: int) -> date:
    """n business days after d, counting from the next business day when d isn't one."""
    return bday(next_bday(d), n)


def bdays_between(a: date, b: date) -> int:
    """Business days after a, up to and including b (0 when b <= a)."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_bday(d)
    return n


def quarter_bounds(d: date, offset: int = 0) -> tuple[date, date]:
    q = (d.month - 1) // 3 + offset
    year, q = d.year + q // 4, q % 4
    start = date(year, 3 * q + 1, 1)
    end = date(year + (q == 3), (3 * q + 3) % 12 + 1, 1) - timedelta(days=1)
    return start, end
