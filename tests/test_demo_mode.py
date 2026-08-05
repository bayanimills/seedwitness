"""Demonstration mode and the boot splash.

Demo mode fills a ceremony with a FIXED practice value so the flow can be
tried without 50 real rolls. Its one hard rule: the result must be
unmistakably marked, everywhere, for as long as it lives. A demonstration
seed a user later mistakes for a real one is the worst outcome this device
can produce -- worse than any crash -- because they might put money behind
it. So these tests pin four things: the [!] disappears the moment a real
ceremony starts (no synthetic rolls into genuine entropy), nothing is
populated before the user agrees, the DEMO stamp travels with the seed
through every subsequent screen, and enrolment refuses a demo seed outright.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import accounts as acct  # noqa: E402
from seedwitness import entropy as ent  # noqa: E402
from seedwitness import mnemonic as mn  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App, _splash_frame  # noqa: E402

CANONICAL = " ".join(["abandon"] * 11) + " about"


@pytest.fixture(autouse=True)
def _demo_flag_never_leaks():
    """The module flag is synced by App.draw; make sure no test in this file
    can leave it set for the rest of the suite."""
    yield
    S.DEMO = False


class RecordingCanvas(MockCanvas):
    def __init__(self):
        super().__init__()
        self.texts = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.texts.append((x, y, s, font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


def _tap(app, canvas, b):
    app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)


def _by_label(app, fragment):
    return next(b for b in app.screen.buttons
                if fragment.lower() in b.label.replace("\n", " ").lower())


def _to_roll_screen(app, canvas):
    _tap(app, canvas, _by_label(app, "Roll a Seed"))
    _tap(app, canvas, _by_label(app, "Dice: D6"))
    _tap(app, canvas, _by_label(app, "12 words"))
    assert type(app.screen).__name__ == "RollEntryScreen"
    return app.screen


def _drawn(app, canvas=None):
    rc = RecordingCanvas()
    app.draw(rc)
    return " ".join(t[2] for t in rc.texts)


def _expected_demo_mnemonic():
    """The fixed pattern, computed independently of the screen code."""
    s = ent.RollSession(ent.D6, 12)
    while not s.is_complete:
        s.add_roll(len(s.rolls) % 6 + 1)
    return mn.entropy_to_mnemonic(s.final_entropy())


# ---------------------------------------------------------------------------
# The [!] and its gate

def test_demo_button_gone_after_the_first_real_roll():
    """Synthetic rolls must never be injectable into a genuine ceremony:
    once anything real is entered, the [!] no longer exists as a target."""
    app, canvas = App(), MockCanvas()
    screen = _to_roll_screen(app, canvas)
    d = screen._demo_button()
    screen.session.add_roll(3)
    _tap(app, canvas, d)                    # same spot, one roll in
    assert type(app.screen).__name__ == "RollEntryScreen", \
        "the demo gate opened mid-ceremony"
    # and the untouched screen does offer it
    app2, canvas2 = App(), MockCanvas()
    screen2 = _to_roll_screen(app2, canvas2)
    _tap(app2, canvas2, screen2._demo_button())
    assert type(app2.screen).__name__ == "DemoConfirmScreen"


def test_nothing_is_populated_before_the_user_agrees():
    app, canvas = App(), MockCanvas()
    screen = _to_roll_screen(app, canvas)
    _tap(app, canvas, screen._demo_button())
    assert not screen.session.rolls, "the gate populated before consent"
    assert app.demo is False
    _tap(app, canvas, _by_label(app, "Cancel"))
    assert app.screen is screen
    assert not screen.session.rolls
    assert app.demo is False


def test_confirm_fills_and_continues_through_the_normal_flow():
    app, canvas = App(), MockCanvas()
    screen = _to_roll_screen(app, canvas)
    _tap(app, canvas, screen._demo_button())
    _tap(app, canvas, _by_label(app, "Start Demo"))
    assert type(app.screen).__name__ == "EntropyCapturedScreen"
    assert screen.session.is_complete
    assert app.demo is True
    assert mn.validate_mnemonic(app.last_mnemonic)
    # FIXED, not arbitrary: the same published pattern for everyone, every
    # time, so no run of the demo can be believed to be a private ceremony
    assert app.last_mnemonic == _expected_demo_mnemonic()


def test_verify_path_demo_uses_the_canonical_mnemonic():
    app, canvas = App(), MockCanvas()
    _tap(app, canvas, _by_label(app, "Verify Seed"))
    app.draw(MockCanvas())                  # builds the [!] into the buttons
    _tap(app, canvas, app.screen._demo_button())
    assert type(app.screen).__name__ == "DemoConfirmScreen"
    _tap(app, canvas, _by_label(app, "Start Demo"))
    assert type(app.screen).__name__ == "DerivationPathScreen"
    assert app.demo is True
    assert app.last_mnemonic == CANONICAL


# ---------------------------------------------------------------------------
# The stamp travels

def test_demo_stamp_travels_through_the_whole_flow():
    """Every frame drawn while the demo seed lives must carry the stamp --
    the words screen someone might copy down, the checksum word, the address
    comparison, all of it."""
    app, canvas = App(), MockCanvas()
    screen = _to_roll_screen(app, canvas)
    _tap(app, canvas, screen._demo_button())
    _tap(app, canvas, _by_label(app, "Start Demo"))
    seen = []
    for label in ("Reveal Words", "Next", "Continue", "Continue",
                  "Verify Address", "BIP84", "Derive Address"):
        assert "DEMO" in _drawn(app), \
            "%s is unstamped before tapping %r" % (type(app.screen).__name__, label)
        seen.append(type(app.screen).__name__)
        _tap(app, canvas, _by_label(app, label))
    assert type(app.screen).__name__ == "AddressDisplayScreen"
    assert "DEMO" in _drawn(app), "the address comparison is unstamped"
    assert "WordListScreen" in seen and "ChecksumWordScreen" in seen


def test_stamp_clears_only_with_the_session():
    app, canvas = App(), MockCanvas()
    screen = _to_roll_screen(app, canvas)
    _tap(app, canvas, screen._demo_button())
    _tap(app, canvas, _by_label(app, "Start Demo"))
    assert "DEMO" in _drawn(app)
    app.reset_to_home()
    assert app.demo is False
    assert app.last_mnemonic is None, "the demo seed outlived its stamp"
    assert "DEMO" not in _drawn(app)


# ---------------------------------------------------------------------------
# Never enrollable

def test_demo_seed_is_refused_at_enrolment(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    app, canvas = App(), MockCanvas()
    screen = _to_roll_screen(app, canvas)
    _tap(app, canvas, screen._demo_button())
    _tap(app, canvas, _by_label(app, "Start Demo"))
    for label in ("Reveal Words", "Next", "Continue", "Continue",
                  "Verify Address", "BIP84", "Derive Address"):
        _tap(app, canvas, _by_label(app, label))
    _tap(app, canvas, _by_label(app, "Enrol Account"))
    enrol = app.screen
    assert type(enrol).__name__ == "EnrolScreen"
    text = _drawn(app)
    assert "Refused" in text and "DEMONSTRATION" in text
    # the refusal replaced the controls: no way to proceed, only back
    assert [b.label for b in enrol.buttons] == ["Back"]
    # and the handler is a backstop even if reached directly
    enrol._enrol(app)
    assert acct.load() == [], "a demonstration seed reached the store"
    assert enrol.done is None


# ---------------------------------------------------------------------------
# The splash

def test_splash_frame_draws_the_wordmark_within_the_panel():
    rc = RecordingCanvas()
    _splash_frame(rc)
    drawn = [t[2] for t in rc.texts]
    assert "seed" in drawn and "witness" in drawn and "ready" in drawn
    for x, y, s, font in rc.texts:
        w = font.width if font is not None else 8
        assert x >= 0 and x + len(s) * w <= rc.width, "%r clips" % s
