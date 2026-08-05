"""Ticks on enrolled-account addresses.

The tick is the user's own per-(account, index) bookkeeping: the device
records that it exists and stores no meaning with it. It lives inside the
account's record in accounts.json, so it persists across sessions, follows
the account through renames, and dies with the account on delete. The
screens flip through successive addresses cheaply (xpub derivation, no
seed) and must present the tick neutrally -- never as "verified", "safe"
or "funded", which the device cannot know.

The store is real accounts.py against a temp path and the xpubs are real
derivations, same discipline as test_account_manage.py.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as drv  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)


def _template(bip):
    return next(t for t in drv.PATH_TEMPLATES if t.bip == bip)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    return acct


@pytest.fixture
def xpub(store):
    _, x = drv.account_xpub(MNEMONIC, _template(84))
    store.add("cold savings", x, 84)
    return x


class RecordingCanvas(MockCanvas):
    def __init__(self):
        super().__init__()
        self.texts = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.texts.append((x, y, s, scale, font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


def _drawn(canvas):
    return " ".join(t[2] for t in canvas.texts)


def _tap(app, canvas, screen, label):
    b = next(b for b in screen.buttons if b.label == label)
    screen.handle_tap(app, canvas, b.x + b.w // 2, b.y + b.h // 2)


def _open_display(app, xpub, index=0):
    """The enrolled-account verify path, driven for real: stepper in,
    Derive out."""
    screen = S.AddressIndexScreen(None, _template(84), index=index, xpub=xpub)
    screen.account_path = "m/84h/0h/0h"
    app.push(screen)
    canvas = MockCanvas()
    b = screen.derive_button
    screen.handle_tap(app, canvas, b.x + 1, b.y + 1)
    assert type(app.screen).__name__ == "AddressDisplayScreen"
    return app.screen


# ---------------------------------------------------------------------------
# The store

def test_marks_persist_across_a_store_round_trip(store, xpub):
    store.toggle_mark(xpub, 3)
    assert store.marks(xpub) == [3]
    # a fresh read of the file, as the next session would do it
    assert store.marks(xpub) == [3]
    store.save(store.load())
    assert store.marks(xpub) == [3]
    # and the on-flash format stays readable plain JSON
    raw = json.loads(Path(store.PATH).read_text())
    assert raw[0]["marks"] == [3]


def test_marks_are_per_index(store, xpub):
    store.toggle_mark(xpub, 2)
    store.toggle_mark(xpub, 5)
    assert store.marks(xpub) == [2, 5]
    assert store.toggle_mark(xpub, 2) is False   # off again
    assert store.marks(xpub) == [5], "unticking one index took another with it"
    assert store.toggle_mark(xpub, 5) is False
    assert store.marks(xpub) == []


def test_marks_are_per_account(store, xpub):
    _, other = drv.account_xpub(MNEMONIC, _template(44))
    store.add("legacy", other, 44)
    store.toggle_mark(xpub, 1)
    assert store.marks(other) == [], "a tick leaked across accounts"
    store.toggle_mark(other, 4)
    assert store.marks(xpub) == [1]
    assert store.marks(other) == [4]


def test_deleting_the_account_deletes_its_marks(store, xpub):
    store.toggle_mark(xpub, 0)
    store.toggle_mark(xpub, 7)
    store.remove(xpub)
    assert store.marks(xpub) == []
    # re-enrolling the same account starts clean, not haunted
    store.add("cold savings", xpub, 84)
    assert store.marks(xpub) == []


def test_rename_keeps_the_marks(store, xpub):
    store.toggle_mark(xpub, 6)
    store.add("new name", xpub, 84)   # add() updates the label in place
    assert store.marks(xpub) == [6]


def test_toggling_an_unenrolled_xpub_changes_nothing(store, xpub):
    before = Path(store.PATH).read_text()
    assert store.toggle_mark("xpub6NotEnrolled", 0) is False
    assert Path(store.PATH).read_text() == before


def test_damaged_marks_degrade_to_what_can_be_salvaged(store, xpub):
    """Same contract as the file itself: a damaged store degrades, never
    raises out of a screen constructor."""
    records = store.load()
    records[0]["marks"] = "garbage"
    store.save(records)
    assert store.marks(xpub) == []
    records[0]["marks"] = [3, "x", -5, True, 1, 1, None]
    store.save(records)
    assert store.marks(xpub) == [1, 3]   # ints only, no bools, deduped, sorted
    # and toggling on top of damage writes a clean list back
    store.toggle_mark(xpub, 2)
    assert store.marks(xpub) == [1, 2, 3]


# ---------------------------------------------------------------------------
# The screens

def test_mark_controls_exist_only_on_the_enrolled_path(store, xpub):
    seed_screen = S.AddressDisplayScreen(
        MNEMONIC, _template(84), 0, "m/84h/0h/0h/0/0", "bc1qtest")
    assert seed_screen.mark_button is None
    assert "Mark" not in [b.label for b in seed_screen.buttons]
    app = App()
    screen = _open_display(app, xpub)
    labels = [b.label for b in screen.buttons]
    assert "Mark" in labels and "<" in labels and ">" in labels


def test_tapping_mark_persists_and_the_label_flips(store, xpub):
    app = App()
    screen = _open_display(app, xpub)
    canvas = MockCanvas()
    _tap(app, canvas, screen, "Mark")
    assert store.marks(xpub) == [0]
    assert screen.mark_button.label == "Unmark"
    _tap(app, canvas, screen, "Unmark")
    assert store.marks(xpub) == []
    assert screen.mark_button.label == "Mark"


def test_next_steps_to_the_next_address_and_keeps_the_stepper_honest(store, xpub):
    app = App()
    screen = _open_display(app, xpub)
    canvas = MockCanvas()
    _tap(app, canvas, screen, ">")
    shown = app.screen
    assert shown is not screen
    assert shown.index == 1
    assert shown.address == drv.address_from_xpub(xpub, _template(84), index=1)
    assert shown.path.endswith("/0/1")
    # the index stepper underneath followed, so Change Index lands on the
    # index being shown
    assert app.stack[-2].index == 1
    _tap(app, canvas, shown, "<")
    assert app.screen.index == 0
    assert app.screen.address == drv.address_from_xpub(xpub, _template(84), index=0)


def test_stepping_stays_inside_the_stepper_bounds(store, xpub):
    app = App()
    screen = _open_display(app, xpub, index=0)
    canvas = MockCanvas()
    _tap(app, canvas, screen, "<")
    assert app.screen is screen, "prev below index 0 must be a no-op"
    app2 = App()
    screen9 = _open_display(app2, xpub, index=9)
    _tap(app2, canvas, screen9, ">")
    assert app2.screen is screen9, "next past the stepper ceiling must be a no-op"


def test_a_tick_reattaches_to_its_own_index_only(store, xpub):
    store.toggle_mark(xpub, 1)
    app = App()
    screen0 = _open_display(app, xpub, index=0)
    assert screen0.marked is False
    canvas = MockCanvas()
    _tap(app, canvas, screen0, ">")
    assert app.screen.marked is True
    assert app.screen.mark_button.label == "Unmark"


def test_the_tick_is_presented_neutrally(store, xpub):
    """The device cannot know what the user's tick means, so no screen may
    dress it up as a verdict."""
    store.toggle_mark(xpub, 0)
    app = App()
    screen = _open_display(app, xpub)
    rc = RecordingCanvas()
    screen.draw(app, rc)
    drawn = _drawn(rc).lower()
    assert "marked" in drawn
    for verdict in ("verified", "safe", "funded", "match"):
        assert verdict not in drawn, (
            "the tick is being presented as %r, a claim the device cannot make"
            % verdict)


def test_the_stepper_shows_the_tick_for_the_current_index(store, xpub):
    screen = S.AddressIndexScreen(None, _template(84), index=2, xpub=xpub)
    screen.account_path = "m/84h/0h/0h"
    store.toggle_mark(xpub, 2)
    rc = RecordingCanvas()
    screen.draw(App(), rc)
    assert "marked" in _drawn(rc)
    screen.index = 3
    rc = RecordingCanvas()
    screen.draw(App(), rc)
    assert "marked" not in _drawn(rc)


def test_new_controls_stay_inside_the_screen_and_their_buttons(store, xpub):
    app = App()
    screen = _open_display(app, xpub)
    for b in screen.buttons:
        assert b.x >= 0 and b.y >= 0
        assert b.x + b.w <= th.WIDTH
        assert b.y + b.h <= th.HEIGHT
        need = max(len(line) for line in b.label.split("\n")) * b.char_w
        assert need <= b.w, "label %r overflows its button" % b.label
    rc = RecordingCanvas()
    screen.draw(app, rc)
    for x, y, s, scale, font in rc.texts:
        cw = font.width if font is not None else th.CHAR_W * scale
        assert x + len(s) * cw <= th.WIDTH, "clips right: %r" % s
