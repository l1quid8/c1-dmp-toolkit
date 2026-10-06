import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from timefmt import clock_time, short_datetime


def test_clock_time_has_no_leading_zero():
    assert clock_time(datetime(2026, 10, 6, 8, 19)) == "8:19 AM"
    assert clock_time(datetime(2026, 10, 6, 0, 5)) == "12:05 AM"
    assert clock_time(datetime(2026, 10, 6, 13, 38)) == "1:38 PM"


def test_short_datetime():
    assert short_datetime(datetime(2026, 7, 1, 13, 38)) == "Jul 1, 1:38 PM"


def test_no_platform_specific_strftime_flags_in_app_code():
    # Dash flags crash strftime on Windows; hash flags crash it on macOS.
    offenders = []
    for path in (ROOT / "scripts").rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"%[-#][a-zA-Z]", line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert offenders == []
