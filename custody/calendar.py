"""Business days by calendar.

- "fed": the Federal Reserve's 11 holidays, which close the cash rails (ACH, Fedwire) and the settlement bank. A holiday
  on a Sunday is observed the Monday after; one on a Saturday isn't observed on a weekday (the Fed opens the Friday).
- "nyse": the NYSE's 10 trading holidays, Good Friday included. A Saturday holiday closes the Friday before, except
  New Year's Day; a Sunday holiday closes the Monday after.
- "nscc": NSCC's equity clearing calendar, which runs ACATS. It closes on NYSE holidays except Good Friday and a Friday
  observed for a Saturday holiday, and stays open on Columbus Day and Veterans Day, with some services limited
  (NSCC Important Notice A#9660, Oct. 15, 2025: the 2026 schedule).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

CALENDARS = ("fed", "nyse", "nscc")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-based) weekday of a month; n=-1 is the last one."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def easter(year: int) -> date:
    """Western Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7     # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


@lru_cache(maxsize=64)
def holidays(year: int, cal: str = "fed") -> frozenset[date]:
    if cal not in CALENDARS:
        raise ValueError(f"Unknown calendar {cal}")
    mon, thu = 0, 3
    floating = {"mlk": _nth_weekday(year, 1, mon, 3), "presidents": _nth_weekday(year, 2, mon, 3),
                "memorial": _nth_weekday(year, 5, mon, -1), "labor": _nth_weekday(year, 9, mon, 1),
                "thanksgiving": _nth_weekday(year, 11, thu, 4)}
    fixed = {"new_year": date(year, 1, 1), "juneteenth": date(year, 6, 19), "independence": date(year, 7, 4),
             "christmas": date(year, 12, 25)}
    out: set[date] = set(floating.values())
    if cal == "fed":
        fixed |= {"veterans": date(year, 11, 11)}
        out.add(_nth_weekday(year, 10, mon, 2))                    # Columbus Day
        for d in fixed.values():
            if d.weekday() == 6:
                out.add(d + timedelta(days=1))
            elif d.weekday() < 5:
                out.add(d)
        return frozenset(out)
    if cal == "nyse":
        out.add(easter(year) - timedelta(days=2))                  # Good Friday
    for name, d in fixed.items():
        if d.weekday() == 6:
            out.add(d + timedelta(days=1))
        elif d.weekday() == 5:
            if cal == "nyse" and name != "new_year":
                out.add(d - timedelta(days=1))
        else:
            out.add(d)
    return frozenset(out)


def is_holiday(d: date, cal: str = "fed") -> bool:
    return d in holidays(d.year, cal)


def is_bday(d: date, cal: str = "fed") -> bool:
    return d.weekday() < 5 and not is_holiday(d, cal)


def bday(d: date, n: int = 0, cal: str = "fed") -> date:
    """The business day n business days from d (n may be negative); n=0 rolls a non-business day back."""
    while not is_bday(d, cal):
        d -= timedelta(days=1)
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        d += timedelta(days=step)
        while not is_bday(d, cal):
            d += timedelta(days=step)
    return d


def next_bday(d: date, cal: str = "fed") -> date:
    """d itself on a business day, otherwise the next one."""
    while not is_bday(d, cal):
        d += timedelta(days=1)
    return d


def following_bday(d: date, cal: str = "fed") -> date:
    """The first business day after d."""
    return next_bday(d + timedelta(days=1), cal)


def add_bdays(d: date, n: int, cal: str = "fed") -> date:
    """n business days after d, counting from the next business day when d isn't one."""
    return bday(next_bday(d, cal), n, cal)


def bdays_between(a: date, b: date, cal: str = "fed") -> int:
    """Business days after a, up to and including b (0 when b <= a)."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_bday(d, cal)
    return n


def quarter_bounds(d: date, offset: int = 0) -> tuple[date, date]:
    q = (d.month - 1) // 3 + offset
    year, q = d.year + q // 4, q % 4
    start = date(year, 3 * q + 1, 1)
    end = date(year + (q == 3), (3 * q + 3) % 12 + 1, 1) - timedelta(days=1)
    return start, end
