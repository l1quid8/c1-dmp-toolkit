"""Human-readable times that work on every platform.

strftime's "no leading zero" flags exist only on macOS/Linux; Windows raises
ValueError for them. Build those pieces by hand instead."""
from datetime import datetime


def clock_time(when: datetime) -> str:
    """'8:19 AM'"""
    return f"{when.hour % 12 or 12}:{when:%M} {when:%p}"


def short_datetime(when: datetime) -> str:
    """'Jul 1, 1:38 PM'"""
    return f"{when:%b} {when.day}, {clock_time(when)}"
