"""A crash must be visible without turning its message into a secret leak."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from seedwitness.ui import screens as S


def test_crash_screen_never_reads_or_displays_exception_message():
    class SecretBearingError(Exception):
        def __str__(self):
            raise AssertionError("CrashScreen read the exception message")

    screen = S.CrashScreen(SecretBearingError("abandon"))

    assert screen.text == "SecretBearingError"
    assert "abandon" not in screen.text
