"""The diagnostics breadcrumb (device/seedwitness/diag.py) exists because two
field failures had to be reconstructed from symptoms. These tests hold its two
load-bearing promises:

  * it can NEVER contain seed material -- not because exception messages
    happen to be clean today, but because the recorder is structurally unable
    to see them (it reads only type names and traceback frame coordinates);
  * it can NEVER raise out of the crash handler, which is the one code path
    guaranteed to be running when things are already wrong.

Plus the bookkeeping that keeps the file deployable at all: diag.txt must be
exempt from build verification in all three hand-maintained RUNTIME lists,
must never grow an executable extension, and its persistent frequency counters
must remain bounded without trusting a nonexistent wall clock.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "tools"))

from seedwitness import attest, diag  # noqa: E402

# A real mnemonic head plus distinctive fake secrets. None of these strings
# may ever appear in the record, whatever an exception message says.
SECRET_WORDS = ("abandon", "ability", "zebra", "wagon",
                "correcthorsebatterystaple")
SECRET_MESSAGE = "Word 'abandon' is not in the dictionary " + " ".join(
    SECRET_WORDS)


@pytest.fixture
def diag_file(tmp_path, monkeypatch):
    p = tmp_path / "diag.txt"
    monkeypatch.setattr(diag, "_PATH", str(p))
    return p


def _raise_with_secret():
    raise ValueError(SECRET_MESSAGE)


def _crash():
    try:
        _raise_with_secret()
    except ValueError as exc:
        return exc


def test_crash_record_names_the_type_and_the_code_location(diag_file):
    assert diag.record_crash(_crash())
    rec = diag.read()
    assert rec["crash"].startswith("ValueError")
    # deepest frame: the helper that raised, in this file, with a line number
    assert "test_diag.py:" in rec["crash"]
    assert rec["crash"].rsplit(":", 1)[1].isdigit()


def test_no_part_of_the_message_ever_reaches_the_file(diag_file):
    """The hard constraint. embit's bip39 really does raise with a user word
    in the message; the record must be immune to that class forever, so this
    asserts against the RAW FILE BYTES, not the parsed record."""
    diag.record_crash(_crash())
    raw = diag_file.read_text(encoding="utf-8").lower()
    for word in SECRET_WORDS:
        assert word not in raw, "secret %r leaked into the record" % word
    assert "dictionary" not in raw  # nor any other fragment of the message


def test_message_lines_disguised_as_frames_cannot_inject_a_location():
    """The MicroPython path parses printed traceback text, so a message
    containing a line SHAPED like a frame is the obvious injection. The
    parser stops at the first non-frame line -- where the message begins --
    so everything after it is unreachable."""
    text = (
        "Traceback (most recent call last):\n"
        '  File "screens.py", line 10, in handle_tap\n'
        '  File "entropy.py", line 42, in add_roll\n'
        "ValueError: sneaky\n"
        '  File "abandon.py", line 1, in x\n'
    )
    assert diag._last_frame(text) == "entropy.py:42"


def test_headerless_text_yields_no_location():
    assert diag._last_frame("ValueError: just a message\n") == ""


def test_clean_is_a_whitelist_with_a_hard_cap():
    """Second, independent layer under the structural guarantee: no spaces or
    quotes survive (a multi-word secret cannot), and length is bounded."""
    assert diag._clean("a b'c\"d\ne", 48) == "abcde"
    assert len(diag._clean("x" * 500, 48)) == 48


def test_boot_and_crash_records_preserve_each_other(diag_file):
    """The whole point of the boot record is explaining the reboot AFTER a
    crash, so writing one line must never erase the other."""
    fake = types.SimpleNamespace(
        PWRON_RESET=1, HARD_RESET=2, WDT_RESET=3, DEEPSLEEP_RESET=4,
        SOFT_RESET=5, reset_cause=lambda: 3)
    assert diag.record_boot(fake)
    assert diag.record_crash(_crash())
    rec = diag.read()
    assert rec["boot"] == "WDT_RESET"
    assert rec["crash"].startswith("ValueError")
    # and the next boot keeps the crash breadcrumb
    fake.reset_cause = lambda: 1
    assert diag.record_boot(fake)
    rec = diag.read()
    assert rec["boot"] == "PWRON_RESET"
    assert rec["crash"].startswith("ValueError")


def test_unknown_reset_cause_is_still_recorded(diag_file):
    fake = types.SimpleNamespace(reset_cause=lambda: 9)
    assert diag.record_boot(fake)
    assert diag.read()["boot"] == "CAUSE_9"


def test_boot_record_is_a_noop_on_the_desktop(diag_file):
    """No `machine` module on CPython: nothing recorded, nothing raised, so
    the sim and the test suite import the same main-path code safely."""
    assert diag.record_boot() is False
    assert diag_file.exists() is False


def test_recording_never_raises_when_the_write_fails(tmp_path, monkeypatch):
    """A full filesystem or bad path must degrade to 'no breadcrumb', never
    to a second exception out of the crash handler."""
    monkeypatch.setattr(diag, "_PATH", str(tmp_path))  # a directory: unwritable
    assert diag.record_crash(_crash()) is False
    fake = types.SimpleNamespace(reset_cause=lambda: 1, PWRON_RESET=1)
    assert diag.record_boot(fake) is False


def test_reading_a_corrupt_or_huge_file_is_bounded(diag_file):
    diag_file.write_bytes(b"\xff\x00garbage " * 4 + b"x" * 1_000_000)
    assert diag.read() == {}
    diag_file.write_text("boot WDT_RESET\nnot a record line\n")
    assert diag.read() == {
        "boot": "WDT_RESET", "boots": 1, "crashes": 0,
        "ack_crashes": 0,
    }


def test_boot_count_is_durable_and_build_repeat_skips_flash_write(diag_file):
    """One bounded write per actual boot gives crash frequency a durable
    denominator. Facts that did not change must still avoid flash writes."""
    import builtins
    fake = types.SimpleNamespace(PWRON_RESET=1, reset_cause=lambda: 1)
    assert diag.record_boot(fake)

    calls = []
    real_open = builtins.open

    def counting_open(path, mode="r", *a, **k):
        if "w" in mode:
            calls.append(path)
        return real_open(path, mode, *a, **k)

    builtins.open = counting_open
    try:
        assert diag.record_boot(fake)
        assert diag.record_build("0123456789abcdef" * 4)
        calls.clear()
        assert diag.record_build("0123456789abcdef" * 4)
    finally:
        builtins.open = real_open
    assert diag.read()["boots"] == 2
    assert calls == [], "an unchanged build was rewritten"


def test_crash_frequency_and_acknowledgement_are_persistent(diag_file):
    fake = types.SimpleNamespace(PWRON_RESET=1, reset_cause=lambda: 1)
    assert diag.record_boot(fake)
    assert diag.record_boot(fake)
    assert diag.record_crash(_crash())
    rec = diag.read()
    assert rec["boots"] == 2
    assert rec["crashes"] == 1
    assert rec["ack_crashes"] == 0
    assert rec["last_crash_boot"] == 2
    assert diag.has_unacknowledged()

    assert diag.acknowledge_crashes()
    rec = diag.read()
    assert rec["crashes"] == rec["ack_crashes"] == 1
    assert rec["crash"].startswith("ValueError")  # acknowledgement is not deletion
    assert not diag.has_unacknowledged()

    assert diag.record_crash(_crash())
    assert diag.read()["crashes"] == 2
    assert diag.has_unacknowledged(), "the next crash must become new again"


def test_legacy_crash_is_migrated_as_unacknowledged(diag_file):
    diag_file.write_text(
        "boot PWRON_RESET\ncrash MemoryError _sha512.py:55\n",
        encoding="utf-8")
    rec = diag.read()
    assert rec["boots"] == 1
    assert rec["crashes"] == 1
    assert rec["ack_crashes"] == 0
    assert rec["last_crash_boot"] == 1
    assert diag.has_unacknowledged()


def test_counts_and_file_size_are_bounded(diag_file):
    diag_file.write_text(
        "boot PWRON_RESET\nboots 999999999\n"
        "crash MemoryError _sha512.py:55\ncrashes 999999999\n"
        "ack_crashes 999999998\nlast_crash_boot 999999999\n"
        "build 0123456789abcdef\n",
        encoding="utf-8")
    fake = types.SimpleNamespace(PWRON_RESET=1, reset_cause=lambda: 1)
    assert diag.record_boot(fake)
    assert diag.record_crash(_crash())
    rec = diag.read()
    assert rec["boots"] == rec["crashes"] == diag._MAX_COUNT
    assert diag_file.stat().st_size <= diag._MAX_FILE


def test_build_context_accepts_only_a_digest(diag_file):
    assert not diag.record_build("not-a-digest")
    assert not diag_file.exists()
    assert diag.record_build("A1" * 32)
    assert diag.read()["build"] == ("a1" * 8)


def test_diag_txt_is_exempt_in_all_three_hand_maintained_lists():
    """attest.py, tools/verify_device.py, tools/deploy.py each hold their own
    copy of the runtime-file set; a miss in any one of them recreates the
    accounts.json bug (permanent, trust-destroying mismatch) on first crash."""
    assert "diag.txt" in attest.RUNTIME_FILES
    import deploy
    assert "diag.txt" in deploy.RUNTIME_FILES
    verify = (ROOT / "tools" / "verify_device.py").read_text(encoding="utf-8")
    assert '"diag.txt"' in verify


def test_the_record_file_is_data_not_code():
    """Redundant with test_attest.py's rule, but stated here where the file
    is defined: an exemption that can execute is a verifier-blessed backdoor."""
    assert diag._PATH.endswith(".txt")


def test_diag_screen_shows_the_record(diag_file):
    from mock_canvas import MockCanvas
    from seedwitness.ui import screens as S
    from seedwitness.ui.app import App

    fake = types.SimpleNamespace(WDT_RESET=3, reset_cause=lambda: 3)
    diag.record_boot(fake)
    diag.record_crash(_crash())

    drawn = []

    class Recording(MockCanvas):
        def text(self, x, y, s, color, bg=None, scale=1, font=None):
            drawn.append(s)
            return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    S.DiagScreen().draw(App(), Recording())
    joined = "\n".join(drawn)
    assert "WDT_RESET" in joined
    assert any(s.startswith("ValueError") for s in drawn)
    for word in SECRET_WORDS:
        assert word not in joined.lower()


def test_pending_crash_marks_home_and_can_be_acknowledged(diag_file,
                                                           monkeypatch):
    from mock_canvas import MockCanvas
    from seedwitness.ui import flow_info
    from seedwitness.ui import screens as S
    from seedwitness.ui.app import App

    fake = types.SimpleNamespace(PWRON_RESET=1, reset_cause=lambda: 1)
    diag.record_boot(fake)
    diag.record_crash(_crash())

    # main.py copies this one bit from the register at boot; the crash handler
    # sets it immediately when a crash is successfully persisted.
    monkeypatch.setattr(S, "DIAG_PENDING", diag.has_unacknowledged())
    home = S.HomeScreen()
    marked = [b for b in home.buttons if b.label == "Verify Build !"]
    assert len(marked) == 1
    assert marked[0].edge == S.th.WARN

    app = App()
    app.push(home)  # keep acknowledgement's pop away from reset_to_home
    screen = flow_info.DiagScreen()
    app.push(screen)
    acknowledge = next(b for b in screen.buttons if b.label == "Acknowledge")

    class Lifted(MockCanvas):
        def touch_active(self):
            return False

    # A brush that lifts before the standard hold finishes changes nothing.
    screen.handle_tap(app, Lifted(), acknowledge.x + 1, acknowledge.y + 1)
    assert diag.has_unacknowledged()

    # MockCanvas holds through the sweep (without sleeping) and commits.
    screen.handle_tap(app, MockCanvas(),
                      acknowledge.x + 1, acknowledge.y + 1)

    assert not diag.has_unacknowledged()
    assert S.DIAG_PENDING is False
    assert not any(b.label == "Acknowledge" for b in app.screen.buttons)
    assert diag.read()["crash"].startswith("ValueError")


def test_runtime_crash_sets_the_resident_indicator(diag_file, monkeypatch):
    from seedwitness.ui import app as A
    from seedwitness.ui import screens as S

    monkeypatch.setattr(S, "DIAG_PENDING", False)
    app = A.App()
    A._crash(app, _crash())
    assert S.DIAG_PENDING is True
    assert diag.has_unacknowledged()


def test_boot_copies_only_pending_bit_into_resident_ui():
    src = (ROOT / "device" / "main.py").read_text(encoding="utf-8")
    assert "diag_pending = diag.has_unacknowledged()" in src
    assert "_screens.DIAG_PENDING = diag_pending" in src
    assert src.index("del sys.modules[\"seedwitness.diag\"]") < src.index(
        "from seedwitness.ui.app import run_on_hardware")


def test_crash_handler_wires_record_crash_after_the_seed_clear():
    """run_on_hardware's except block is hardware-bound and cannot execute
    under pytest, so pin the wiring in source the way test_attest.py pins
    verify_device's list: record_crash must be called, and it must come
    AFTER clear_seed_cache so the recorder can never observe live secrets."""
    src = (ROOT / "device" / "seedwitness" / "ui" / "app.py").read_text(
        encoding="utf-8")
    assert "diag.record_crash(exc)" in src
    assert src.index("clear_seed_cache()") < src.index("diag.record_crash(exc)")
