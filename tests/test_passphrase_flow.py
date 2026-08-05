"""Drive the passphrase screens, not just the engine underneath them.

The engine is well covered by test_passphrase.py. What was untested until now
is the part a user actually touches: that rolling on the grid produces the
same words the engine would, that the result reaches derivation, and above all
that it is cleared when the session ends. A passphrase left set silently
derives a different wallet for whatever the next person checks, and the screen
would still say MATCH or MISMATCH with total confidence.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from seedwitness import passphrase as pph  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

WORDS = ROOT / "_build_mpy" / "eff_words.bin"
needs_build = pytest.mark.skipif(
    not WORDS.exists(), reason="run tools/build_mpy.sh first")

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")


@pytest.fixture(autouse=True)
def _wordlist(monkeypatch):
    """The device reads /eff_words.bin; on the desktop it lives in the build."""
    monkeypatch.setattr(pph, "WORDS_FILE", str(WORDS))


def _roll_through(screen, app, rolls):
    for r in rolls:
        screen._handler(r)(app)


@needs_build
def test_rolling_the_grid_produces_the_engine_s_words():
    """The screen must not have its own idea of the mapping."""
    app = App()
    screen = S.PassphraseRollScreen(M, 8)
    app.push(screen)
    rolls = [((i % 6) + 1) for i in range(pph.rolls_needed(8))]
    _roll_through(screen, app, rolls)
    assert type(app.screen).__name__ == "PassphraseWordsScreen"
    assert app.screen.words == pph.words_from_rolls(rolls, str(WORDS))
    assert len(app.screen.words) == 8


@needs_build
def test_a_short_ceremony_does_not_advance():
    """One roll short must not produce a passphrase. The failure this guards
    is a partial ceremony being silently padded or truncated into something
    that looks complete."""
    app = App()
    screen = S.PassphraseRollScreen(M, 6)
    app.push(screen)
    _roll_through(screen, app, [1] * (pph.rolls_needed(6) - 1))
    assert type(app.screen).__name__ == "PassphraseRollScreen"


def test_roll_screen_back_discards_partial_rolls():
    """Changing one's mind must not require finishing 30--50 rolls.

    The partial input belongs only to the discarded screen and must neither
    advance to the word display nor arm any part of a passphrase.
    """
    app = App()
    app.push(S.DerivationPathScreen(M))
    app.push(S.PassphraseLengthScreen(M))
    screen = S.PassphraseRollScreen(M, 8)
    app.push(screen)
    screen.rolls.extend([1, 2, 3, 4])

    back = screen.back_button(app)
    screen.handle_tap(app, MockCanvas(),
                      back.x + back.w // 2, back.y + back.h // 2)

    assert type(app.screen).__name__ == "PassphraseLengthScreen"
    assert app.passphrase == ""
    assert screen not in app.stack


def test_roll_screen_back_goes_home_safely_at_shallow_depth():
    """The same escape remains safe if this screen is ever reached directly.

    App.pop() treats a pop to Home as session teardown, so an already-armed
    passphrase cannot leak through this unusual stack shape.
    """
    app = App()
    app.passphrase = "left over words"
    screen = S.PassphraseRollScreen(M, 6)
    app.push(screen)
    back = screen.back_button(app)

    screen.handle_tap(app, MockCanvas(),
                      back.x + back.w // 2, back.y + back.h // 2)

    assert type(app.screen).__name__ == "HomeScreen"
    assert app.passphrase == ""


@needs_build
def test_use_this_sets_the_session_passphrase():
    # Realistic stack: Use This steps back over this screen and the length
    # screen, to the Address Type screen the user came from.
    app = App()
    app.push(S.DerivationPathScreen(M))
    app.push(S.PassphraseLengthScreen(M))
    words = pph.words_from_rolls([1, 2, 3, 4, 5] * 8, str(WORDS))
    screen = S.PassphraseWordsScreen(M, words)
    app.push(screen)
    screen._use(app)
    assert type(app.screen).__name__ == "DerivationPathScreen"
    assert app.passphrase == pph.to_passphrase(words)
    assert app.passphrase.isascii()


@needs_build
def test_use_this_does_not_arm_a_passphrase_on_the_home_screen():
    """If the pops land Home the session is over, and a passphrase left set
    would silently derive a different wallet for whatever the next person
    checks."""
    app = App()
    words = pph.words_from_rolls([1, 2, 3, 4, 5] * 6, str(WORDS))
    screen = S.PassphraseWordsScreen(M, words)
    app.push(screen)
    screen._use(app)
    assert type(app.screen).__name__ == "HomeScreen"
    assert app.passphrase == ""


@needs_build
def test_the_passphrase_changes_the_derived_address():
    """If it did not reach derivation, every screen above it would be
    theatre."""
    from seedwitness import derive as drv
    words = pph.words_from_rolls([1, 2, 3, 4, 5] * 8, str(WORDS))
    phrase = pph.to_passphrase(words)
    t = [x for x in drv.PATH_TEMPLATES if x.bip == 84][0]
    _, plain = drv.derive_address(M, t, index=0)
    _, withpp = drv.derive_address(M, t, index=0, passphrase=phrase)
    assert plain != withpp


@needs_build
def test_returning_home_clears_the_passphrase():
    """The one that matters most.

    A passphrase surviving the session would derive a different wallet for
    whatever the next person checks, and the verdict screen would report that
    mismatch with complete confidence. It has to die with the mnemonic.
    """
    app = App()
    app.passphrase = "left over words"
    app.last_mnemonic = M
    app.reset_to_home()
    assert app.passphrase == ""
    assert app.last_mnemonic is None


@needs_build
def test_backing_out_to_home_also_clears_it():
    """Back is the path that used to leak the mnemonic, because the corner
    button relabels itself Home without clearing. The passphrase must not
    reintroduce that hole."""
    app = App()
    app.push(S.PassphraseLengthScreen(M))
    app.passphrase = "left over words"
    app.pop()          # stack was 2 deep, so this lands home
    assert app.passphrase == ""


def test_only_the_offered_lengths_are_reachable():
    """Every button on the length screen must map to a length the engine
    accepts, so a UI edit cannot introduce an unvetted entropy target."""
    screen = S.PassphraseLengthScreen(M)
    labels = [b.label for b in screen.buttons if "words" in b.label]
    assert len(labels) == len(pph.LENGTHS)
    for n in pph.LENGTHS:
        assert any(l.startswith("%d words" % n) for l in labels)


def test_the_length_screen_states_entropy_honestly():
    """The bits figure shown must be the engine's rounded-down one, not a
    prettier number typed into the label."""
    screen = S.PassphraseLengthScreen(M)
    for b in screen.buttons:
        if "words" not in b.label:
            continue
        count = int(b.label.split()[0])
        assert "%d bits" % pph.bits_for(count) in b.label


def test_armed_passphrase_handoff_repeats_its_check_value():
    """The value shown with the words must survive the hand-off to derivation.

    This makes the internal pass-through observable before the expensive
    derive. It does not pretend to validate the user's paper transcription;
    the resulting address comparison remains the authoritative gate.
    """
    phrase = "correct horse battery staple"
    app = App()
    app.passphrase = phrase
    screen = S.DerivationPathScreen(M)

    class RecordingCanvas(MockCanvas):
        def __init__(self):
            super().__init__()
            self.strings = []

        def text(self, x, y, value, color, **kwargs):
            self.strings.append(value)
            return super().text(x, y, value, color, **kwargs)

    canvas = RecordingCanvas()
    screen.draw(app, canvas)

    footer = " ".join(canvas.strings)
    assert "passphrase SET" in footer
    assert "check " + pph.check_value(phrase) in footer
