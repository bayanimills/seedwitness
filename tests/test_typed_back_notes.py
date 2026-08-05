"""What the backup screens say about typing the thing back in.

passphrase.to_passphrase() joins the words with single spaces, and that
exact lowercase ASCII string is what PBKDF2 stretches. A user who writes
the words down from a numbered column and later types them back without
spaces, or capitalised, silently derives a different wallet -- so the
screen that shows the words must also show the string as it will be typed,
and must state the two rules a column cannot carry: lowercase, single
spaces. Same reasoning, one line's worth, for the BIP39 seed word list.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import passphrase as pph  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)

# Real EFF-list words (no wordlist file needed: the screen takes the words
# as given), in each offered length.
SIX = ["banjo", "cupcake", "dinosaur", "emblem", "fabric", "gazebo"]
EIGHT = SIX + ["hamster", "icicle"]
TEN = EIGHT + ["jaundice", "kangaroo"]

# The EFF large list's longest word is 9 characters; ten of them is the
# worst layout case the screen can ever be handed.
WORST = ["abrasives"] * 10


class RecordingCanvas(MockCanvas):
    def __init__(self):
        super().__init__()
        self.texts = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.texts.append((x, y, s, scale, font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


def _drawn(canvas):
    return " ".join(t[2] for t in canvas.texts)


def _render(words):
    screen = S.PassphraseWordsScreen(MNEMONIC, words)
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    return screen, canvas


# ---------------------------------------------------------------------------
# The passphrase screen

@pytest.mark.parametrize("words", [SIX, EIGHT, TEN], ids=["6", "8", "10"])
def test_the_screen_states_the_separator_and_the_case(words):
    _, canvas = _render(words)
    drawn = _drawn(canvas)
    assert "single spaces" in drawn, "the separator is not stated"
    assert "lowercase" in drawn, "the case is not stated"
    assert "exactly" in drawn, "exact matching is not stated"


@pytest.mark.parametrize("words", [SIX, EIGHT, TEN, WORST],
                         ids=["6", "8", "10", "worst"])
def test_the_typed_form_on_screen_is_the_exact_pbkdf2_string(words):
    """The wrapped lines, read as one line, must reconstruct the precise
    string handed to PBKDF2 -- the display may not drift from the engine."""
    phrase = pph.to_passphrase(words)
    expected = th.wrap_text(phrase, (th.WIDTH - 2 * S.MARGIN) // th.CHAR_W)
    _, canvas = _render(words)
    texts = [t[2] for t in canvas.texts]
    for line in expected:
        assert line in texts, "typed-form line %r not on screen" % line
    assert " ".join(expected) == phrase, (
        "wrapping does not reconstruct the phrase; the display would lie")


@pytest.mark.parametrize("words", [SIX, EIGHT, TEN, WORST],
                         ids=["6", "8", "10", "worst"])
def test_the_numbered_list_is_still_there(words):
    """The typed form is an addition. The numbered list is what people
    transcribe from and must not be removed."""
    _, canvas = _render(words)
    texts = [t[2] for t in canvas.texts]
    for i, w in enumerate(words):
        assert w in texts, "word %r missing from the list" % w
        assert any(t.strip() == str(i + 1) for t in texts), (
            "number %d missing from the list" % (i + 1))


@pytest.mark.parametrize("words", [SIX, EIGHT, TEN, WORST],
                         ids=["6", "8", "10", "worst"])
def test_nothing_clips_or_runs_under_the_button(words):
    screen, canvas = _render(words)
    button_top = min(b.y for b in screen.buttons)
    for x, y, s, scale, font in canvas.texts:
        cw = font.width if font is not None else th.CHAR_W * scale
        chh = font.height if font is not None else th.CHAR_H * scale
        assert x + len(s) * cw <= th.WIDTH, "clips right: %r" % s
        if y < button_top:   # content; the button draws its own label below
            assert y + chh <= button_top, (
                "%r (y=%d) runs into the Use This button" % (s, y))


def test_the_check_value_survived_the_relayout():
    screen, canvas = _render(EIGHT)
    assert "check " + pph.check_value(screen.phrase) in _drawn(canvas)


# ---------------------------------------------------------------------------
# The seed word list

def test_the_final_word_list_page_states_case_and_separator():
    canvas = RecordingCanvas()
    S.WordListScreen(MNEMONIC, page=1).draw(App(), canvas)   # last page of 12
    drawn = _drawn(canvas)
    assert "lowercase" in drawn
    assert "single spaces" in drawn


def test_the_note_appears_on_24_word_seeds_too():
    m24 = " ".join(["abandon"] * 23 + ["art"])
    screen = S.WordListScreen(m24, page=3)                   # last of 4 pages
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    assert "lowercase" in _drawn(canvas)


def test_earlier_pages_leave_the_words_alone():
    canvas = RecordingCanvas()
    S.WordListScreen(MNEMONIC, page=0).draw(App(), canvas)
    assert "lowercase" not in _drawn(canvas)
