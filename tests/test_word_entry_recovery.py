"""Word entry must never wedge, and a typo must not cost all twelve words.

Re-entering a seed is the flow where a user is most likely to make a mistake:
up to 8 key presses per word, 12 or 24 words, on a 34px keyboard. The failure
only surfaces at the very end, as a checksum error that says nothing about
WHICH word is wrong. So the recovery path matters as much as the happy one.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

VALID = ("abandon abandon abandon abandon abandon abandon "
         "abandon abandon abandon abandon abandon about").split()
# right words, wrong checksum
INVALID = ["abandon"] * 11 + ["zoo"]


def _entry(words=None, target=None):
    return S.WordEntryScreen(12, words=list(words or []), target_mnemonic=target)


def test_confirming_past_the_word_count_cannot_wedge():
    """The bug this guards: with `==`, a list already at the target length
    never matched again, so every further tap appended another word and the
    screen could never be completed."""
    screen = _entry(VALID)          # already full
    app = App()
    screen._confirm_word(app, "zoo")
    assert len(screen.words) == 12, "a 13th word was appended"


def test_a_full_valid_seed_advances():
    screen = _entry(VALID[:-1])
    app = App()
    app.push(screen)
    screen._confirm_word(app, VALID[-1])
    assert type(app.screen).__name__ == "DerivationPathScreen"


def test_a_bad_checksum_reaches_the_invalid_screen_with_the_words():
    screen = _entry(INVALID[:-1])
    app = App()
    app.push(screen)
    screen._confirm_word(app, INVALID[-1])
    assert type(app.screen).__name__ == "InvalidSeedScreen"
    assert app.screen.words == INVALID, "typed words were not carried over"


def test_retry_keeps_everything_but_the_last_word():
    """One typo must not cost twelve words of typing."""
    app = App()
    app.push(S.InvalidSeedScreen(12, INVALID))
    retry = next(b for b in app.screen.buttons if "Try Again" in b.label)
    retry.on_tap(app)
    assert type(app.screen).__name__ == "WordEntryScreen"
    assert app.screen.words == INVALID[:-1]
    assert len(app.screen.words) == 11


def test_retry_lands_in_a_state_that_can_be_completed():
    """Restoring words must leave the screen usable -- this is exactly where
    the wedge showed up, at 11 of 12 with one word to re-enter."""
    app = App()
    app.push(S.InvalidSeedScreen(12, INVALID))
    next(b for b in app.screen.buttons if "Try Again" in b.label).on_tap(app)
    screen = app.screen
    screen._confirm_word(app, "about")
    assert type(app.screen).__name__ != "WordEntryScreen", "could not complete after retry"


def test_start_over_clears_everything():
    app = App()
    app.push(S.InvalidSeedScreen(12, INVALID))
    next(b for b in app.screen.buttons if "Start Over" in b.label).on_tap(app)
    assert app.screen.words == []


def test_repeated_failures_do_not_pile_up_on_the_stack():
    """Fail, retry, fail again -- through the real code path, not by pushing
    screens by hand. Each cycle must replace, never accumulate, or Back from a
    later screen lands on a stale Invalid Seed frame."""
    app = App()
    app.push(_entry(INVALID[:-1]))
    depth = len(app.stack)
    for _ in range(4):
        app.screen._confirm_word(app, INVALID[-1])       # -> InvalidSeedScreen
        assert type(app.screen).__name__ == "InvalidSeedScreen"
        assert len(app.stack) == depth, "stack grew on failure"
        next(b for b in app.screen.buttons
             if "Try Again" in b.label).on_tap(app)      # -> WordEntryScreen
        assert type(app.screen).__name__ == "WordEntryScreen"
        assert len(app.stack) == depth, "stack grew on retry"
        # retry drops the last word, so top the list back up for the next round
        app.screen.words = list(INVALID[:-1])


def test_backspace_walks_back_through_confirmed_words():
    screen = _entry(VALID[:3])
    app = App()
    screen._backspace(app)
    assert screen.words == VALID[:2]
    screen.prefix = "ab"
    screen._backspace(app)
    assert screen.prefix == "a" and screen.words == VALID[:2], (
        "backspace ate a word while a prefix was still being typed")


def test_backspace_on_an_empty_screen_is_harmless():
    screen = _entry([])
    screen._backspace(App())
    assert screen.words == [] and screen.prefix == ""


def test_confirm_backup_mismatch_still_routes_to_the_result_screen():
    """The confirm-backup path has its own branch and must not regress."""
    screen = _entry(VALID[:-1], target=" ".join(VALID))
    app = App()
    app.push(screen)
    screen._confirm_word(app, "zoo")
    assert type(app.screen).__name__ == "BackupResultScreen"


def test_every_screen_in_the_recovery_path_renders():
    canvas = MockCanvas()
    app = App()
    for screen in (_entry(INVALID[:5]), S.InvalidSeedScreen(12, INVALID)):
        screen.draw(app, canvas)
