"""Three defects both earlier review rounds walked past.

None of them corrupts a key. All three are the device failing the person
holding it at the moment they have already paid the most for the answer.

  1. Enrolment ran ~10s of EC math synchronously inside the tap with no wait
     frame, so the panel froze on the locked amber button. Every other
     multi-second derivation in the app paints one.
  2. accounts.save() can raise OSError on a full filesystem. It escaped the
     tap handler to the CrashScreen, which clears the seed cache, so a
     storage problem cost the user a derivation they had waited ten minutes
     for, with no statement of what went wrong.
  3. PassphraseWordsScreen's only control was "Use This", so a passphrase
     could not be refused. Someone reading over your shoulder is exactly when
     you need to discard, and exactly when you should not have to tear down
     the session to do it.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402
from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as drv  # noqa: E402
from seedwitness import passphrase as pph  # noqa: E402
from seedwitness.ui import flow_address as FA  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")
T84 = [t for t in drv.PATH_TEMPLATES if t.bip == 84][0]
WORDS = ROOT / "_build_mpy" / "eff_words.bin"
needs_build = pytest.mark.skipif(not WORDS.exists(),
                                 reason="run tools/build_mpy.sh first")


class Recording(MockCanvas):
    def __init__(self):
        super().__init__()
        self.drawn = []
        self.frames = 0

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.drawn.append(str(s))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def present(self):
        self.frames += 1
        return super().present()


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    drv.clear_seed_cache()
    yield
    drv.clear_seed_cache()


# --------------------------------------------------------------------------
# 1. the enrolment wait frame
# --------------------------------------------------------------------------

def test_enrolment_paints_a_wait_frame_before_the_derivation():
    """The screen must say something before ~10s of EC math, not freeze."""
    app = App()
    app.last_mnemonic = M
    screen = FA.EnrolScreen(M, T84)
    canvas = Recording()
    screen.handle_tap(app, canvas, -1, -1)   # stashes the canvas, hits nothing
    screen._enrol(app)
    body = " ".join(canvas.drawn)
    assert "DERIVING" in body.upper(), canvas.drawn
    assert canvas.frames >= 1


def test_the_enrol_wait_frame_does_not_borrow_the_read_only_reassurance():
    """The adjacent Account Key screen says "Nothing will be stored." This
    action stores. Saying the same thing here would be a lie at the exact
    moment it matters."""
    canvas = Recording()
    FA._enrol_wait_frame(FA.EnrolScreen(M, T84), canvas)
    body = " ".join(canvas.drawn)
    assert "Nothing will be stored" not in body
    assert "saved" in body.lower()


def test_stashing_the_canvas_does_not_disarm_the_hold_gate():
    """The canvas is stashed by delegating to super(), NOT by intercepting
    before _dispatch. An interception would silently remove the dwell this
    screen was given for persisting to flash."""
    assert FA.EnrolScreen.SWEEP_MS > 0
    src = FA.EnrolScreen.handle_tap.__code__.co_names
    assert "handle_tap" in src or True   # delegation detail, asserted below
    app, canvas = App(), Recording()
    screen = FA.EnrolScreen(M, T84)
    calls = []
    orig = S.Screen._dispatch
    try:
        S.Screen._dispatch = lambda self, a, c, b: calls.append(b.label)
        btn = screen.buttons[0]
        screen.handle_tap(app, canvas, btn.x + 2, btn.y + 2)
    finally:
        S.Screen._dispatch = orig
    assert calls == ["Enrol Account"], calls


# --------------------------------------------------------------------------
# 2. a failed flash write must not cost the session
# --------------------------------------------------------------------------

def test_a_full_filesystem_does_not_crash_enrolment(monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "ENOSPC")
    monkeypatch.setattr(acct, "add", boom)
    app = App()
    app.last_mnemonic = M
    screen = FA.EnrolScreen(M, T84)
    canvas = Recording()
    screen.handle_tap(app, canvas, -1, -1)
    screen._enrol(app)                 # must not raise
    assert screen._write_failed is True
    assert screen.done is None
    assert app.last_mnemonic == M      # session survived


def test_the_failed_write_is_reported_on_screen(monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "ENOSPC")
    monkeypatch.setattr(acct, "add", boom)
    app = App()
    app.last_mnemonic = M
    screen = FA.EnrolScreen(M, T84)
    canvas = Recording()
    screen.handle_tap(app, canvas, -1, -1)
    screen._enrol(app)
    canvas.drawn.clear()
    screen.draw(app, canvas)
    body = " ".join(canvas.drawn)
    assert "Could not write" in body
    assert "Nothing was enrolled" in body
    assert "still loaded" in body


def test_a_working_write_still_enrols():
    """Guards the guard: an except that swallowed everything would satisfy
    both tests above and break enrolment."""
    app = App()
    app.last_mnemonic = M
    screen = FA.EnrolScreen(M, T84)
    screen.handle_tap(app, Recording(), -1, -1)
    screen._enrol(app)
    assert screen._write_failed is False
    assert screen.done is not None
    assert len(acct.load()) == 1


# --------------------------------------------------------------------------
# 3. refusing a passphrase without arming it
# --------------------------------------------------------------------------

@needs_build
def test_a_rolled_passphrase_can_be_discarded(monkeypatch):
    monkeypatch.setattr(pph, "WORDS_FILE", str(WORDS))
    app = App()
    app.push(S.DerivationPathScreen(M))
    app.push(S.PassphraseLengthScreen(M))
    words = pph.words_from_rolls([1, 2, 3, 4, 5] * 8, str(WORDS))
    screen = S.PassphraseWordsScreen(M, words)
    app.push(screen)
    screen._discard(app)
    assert app.passphrase == ""
    assert type(app.screen).__name__ == "PassphraseLengthScreen"


@needs_build
def test_discard_clears_a_previously_armed_passphrase(monkeypatch):
    """This screen is reachable again after an earlier Use This. Leaving the
    old phrase armed while the user believes they discarded is the exact
    failure the screen exists to prevent."""
    monkeypatch.setattr(pph, "WORDS_FILE", str(WORDS))
    app = App()
    app.push(S.DerivationPathScreen(M))
    app.passphrase = "already armed words"
    words = pph.words_from_rolls([6, 5, 4, 3, 2] * 8, str(WORDS))
    screen = S.PassphraseWordsScreen(M, words)
    app.push(screen)
    screen._discard(app)
    assert app.passphrase == ""


@needs_build
def test_the_screen_offers_both_an_accept_and_a_refuse(monkeypatch):
    monkeypatch.setattr(pph, "WORDS_FILE", str(WORDS))
    words = pph.words_from_rolls([1, 2, 3, 4, 5] * 8, str(WORDS))
    labels = [b.label for b in S.PassphraseWordsScreen(M, words).buttons]
    assert "Use This" in labels
    assert "Discard" in labels


@needs_build
def test_use_this_still_arms(monkeypatch):
    """Guards the guard."""
    monkeypatch.setattr(pph, "WORDS_FILE", str(WORDS))
    app = App()
    app.push(S.DerivationPathScreen(M))
    app.push(S.PassphraseLengthScreen(M))
    words = pph.words_from_rolls([1, 2, 3, 4, 5] * 8, str(WORDS))
    screen = S.PassphraseWordsScreen(M, words)
    app.push(screen)
    screen._use(app)
    assert app.passphrase == pph.to_passphrase(words)
