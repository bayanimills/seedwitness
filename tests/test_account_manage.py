"""Managing enrolled accounts: fingerprints everywhere, nickname entry,
delete with an honest confirmation, and the xpub QR.

The store is real accounts.py against a temp path -- no fakes -- and the
xpubs are real derivations, because a fingerprint test against a made-up
xpub proves only that sha256 runs.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
import zxingcpp  # noqa: E402
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
def enrolled(store):
    """Two real enrolled accounts of different types."""
    recs = []
    for bip, label in ((84, "cold savings"), (44, "BIP44 Legacy")):
        _, xpub = drv.account_xpub(MNEMONIC, _template(bip))
        recs.append(store.add(label, xpub, bip))
    return recs


class RecordingCanvas(MockCanvas):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.rects = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.texts.append((x, y, s, scale, font))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def fill_rect(self, x, y, w, h, color):
        self.rects.append((x, y, w, h, color))
        return super().fill_rect(x, y, w, h, color)


def _drawn(canvas):
    return " ".join(t[2] for t in canvas.texts)


def _tap(app, canvas, screen, label):
    b = next(b for b in screen.buttons if b.label == label)
    screen.handle_tap(app, canvas, b.x + b.w // 2, b.y + b.h // 2)


def _list_screen(app=None):
    app = app or App()
    screen = S.AccountListScreen()
    app.push(screen)
    screen.draw(app, MockCanvas())          # rows are built on draw
    return app, screen


# ---------------------------------------------------------------------------
# Fingerprints: on the list row and on the account's verify screen

def test_list_rows_show_label_and_fingerprint(enrolled):
    app, screen = _list_screen()
    canvas = RecordingCanvas()
    screen.draw(app, canvas)
    drawn = _drawn(canvas)
    for rec in enrolled:
        assert rec["label"] in drawn
        assert drv.xpub_fingerprint(rec["xpub"]) in drawn, \
            "the fingerprint is not on the list row"


def test_opening_an_account_shows_its_fingerprint(enrolled):
    app, screen = _list_screen()
    canvas = RecordingCanvas()
    row = next(b for b in screen.buttons if b.label == enrolled[0]["label"])
    screen.handle_tap(app, canvas, row.x + row.w // 2, row.y + row.h // 2)
    assert type(app.screen).__name__ == "AddressIndexScreen"
    canvas = RecordingCanvas()
    app.screen.draw(app, canvas)
    assert drv.xpub_fingerprint(enrolled[0]["xpub"]) in _drawn(canvas), \
        "the verify screen does not confirm which account it is using"


def test_manage_screen_shows_the_fingerprint(enrolled):
    screen = S.AccountManageScreen(enrolled[0])
    canvas = RecordingCanvas()
    screen.draw(App(), canvas)
    assert drv.xpub_fingerprint(enrolled[0]["xpub"]) in _drawn(canvas)
    assert enrolled[0]["label"] in _drawn(canvas)


# ---------------------------------------------------------------------------
# Delete

def _to_manage(enrolled, i=0):
    app, screen = _list_screen()
    canvas = MockCanvas()
    edits = [b for b in screen.buttons if b.label == "Edit"]
    screen.handle_tap(app, canvas, edits[i].x + edits[i].w // 2,
                      edits[i].y + edits[i].h // 2)
    assert type(app.screen).__name__ == "AccountManageScreen"
    return app, app.screen


def test_delete_confirms_without_overstating(enrolled):
    """The confirmation must say what is true: only a public key goes, and
    re-enrolment recreates it. A user who believes deletion is dangerous
    keeps records they meant to remove."""
    app, manage = _to_manage(enrolled)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "Delete")
    assert type(app.screen).__name__ == "AccountDeleteScreen"
    rc = RecordingCanvas()
    app.screen.draw(app, rc)
    drawn = _drawn(rc).lower()
    assert "public key" in drawn
    assert "enrolling the same seed" in drawn
    assert "recreates" in drawn


def test_delete_removes_the_account_and_the_list_refreshes(store, enrolled):
    app, manage = _to_manage(enrolled, i=0)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "Delete")
    _tap(app, canvas, app.screen, "Delete")     # the confirmation
    assert type(app.screen).__name__ == "AccountListScreen"
    assert [r["label"] for r in store.load()] == [enrolled[1]["label"]]
    rc = RecordingCanvas()
    app.screen.draw(app, rc)
    assert enrolled[0]["label"] not in _drawn(rc), "a deleted account still lists"


def test_cancel_deletes_nothing(store, enrolled):
    app, manage = _to_manage(enrolled)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "Delete")
    _tap(app, canvas, app.screen, "Cancel")
    assert len(store.load()) == 2
    assert type(app.screen).__name__ == "AccountManageScreen"


# ---------------------------------------------------------------------------
# Nickname

def _type_on(app, canvas, screen, text):
    for ch in text:
        _tap(app, canvas, screen, ch)


def test_rename_updates_the_stored_label(store, enrolled):
    app, manage = _to_manage(enrolled, i=0)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "Rename")
    nick = app.screen
    assert type(nick).__name__ == "NicknameScreen"
    _type_on(app, canvas, nick, "vault2026")
    _tap(app, canvas, nick, "Save")
    assert app.screen is manage
    labels = [r["label"] for r in store.load()]
    assert "vault2026" in labels
    assert len(store.load()) == 2, "rename must never duplicate the account"
    rc = RecordingCanvas()
    manage.draw(app, rc)
    assert "vault2026" in _drawn(rc), "the manage screen shows a stale label"


def test_nickname_keys_are_exactly_the_covered_charset():
    """a-z and 0-9 only: the set every face on the device covers, including
    the 16x32 hero subset. Anything else could render as blank cells."""
    screen = S.NicknameScreen("", lambda a, t: None)
    keys = [b.label for b in screen.buttons if len(b.label) == 1]
    assert sorted(keys) == sorted("abcdefghijklmnopqrstuvwxyz0123456789")


def test_nickname_length_is_capped_to_fit_the_list_row(store, enrolled):
    app, manage = _to_manage(enrolled)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "Rename")
    nick = app.screen
    _type_on(app, canvas, nick, "abcdefghij0123456789")   # 20 presses
    assert len(nick.value) == S.NicknameScreen.MAX_LEN
    # and the cap actually fits the row: nickname at the row's face, inside
    # the row's width
    row_w = th.WIDTH - 2 * S.MARGIN - S.AccountListScreen.EDIT_W - S.BUTTON_GAP
    assert S.NicknameScreen.MAX_LEN * S.fonts.S.width <= row_w - 12


def test_backspace_and_empty_save_falls_back_to_the_auto_label(store, enrolled):
    app, manage = _to_manage(enrolled, i=0)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "Rename")
    nick = app.screen
    _type_on(app, canvas, nick, "ab")
    _tap(app, canvas, nick, "<- Del")
    _tap(app, canvas, nick, "<- Del")
    assert nick.value == ""
    _tap(app, canvas, nick, "Save")
    labels = [r["label"] for r in store.load()]
    assert "BIP84 SegWit" in labels, "empty nickname should fall back, not blank"


def test_enrolment_offers_a_nickname(store):
    """Set at enrolment, because that is when the user still knows which
    wallet this is."""
    app = App()
    screen = S.EnrolScreen(MNEMONIC, _template(84))
    app.push(screen)
    canvas = MockCanvas()
    _tap(app, canvas, screen, "Enrol Account")
    assert [b.label for b in screen.buttons][0] == "Add Nickname"
    _tap(app, canvas, screen, "Add Nickname")
    nick = app.screen
    _type_on(app, canvas, nick, "mine")
    _tap(app, canvas, nick, "Save")
    assert [r["label"] for r in store.load()] == ["mine"]
    assert len(store.load()) == 1


# ---------------------------------------------------------------------------
# The xpub QR

def test_xpub_qr_opens_on_a_plain_tap(enrolled):
    # enrolled[0] is BIP84, so its manage screen splits the QR row into
    # "xpub QR" / "zpub QR" (see test_slip132.py for the alternate form)
    app, manage = _to_manage(enrolled)
    canvas = MockCanvas()
    _tap(app, canvas, manage, "xpub QR")
    assert type(app.screen).__name__ == "AccountQRScreen"
    assert not hasattr(app.screen, "SHOW_MS"), "an auto-blank crept in"


def test_xpub_qr_decodes_to_the_exact_xpub(enrolled):
    """Byte mode, case preserved: base58 is case-significant, so a decoder
    must hand back the identical 111 chars a watch-only wallet needs."""
    screen = S.AccountQRScreen(enrolled[0])
    assert screen.size == 41               # version 6
    canvas = MockCanvas()
    screen.draw(App(), canvas)
    img = canvas.image.resize((canvas.width * 3, canvas.height * 3))
    res = zxingcpp.read_barcode(img)
    assert res is not None, "the on-screen xpub frame did not decode"
    assert res.text == enrolled[0]["xpub"]


def test_xpub_qr_fits_the_panel_and_states_the_exposure(enrolled):
    screen = S.AccountQRScreen(enrolled[0])
    rc = RecordingCanvas()
    screen.draw(App(), rc)
    panels = [r for r in rc.rects if r[4] == th.FG and r[2] >= 100 and r[2] == r[3]]
    assert panels, "no QR panel drawn"
    x0, y0, side, _, _ = panels[0]
    px = side // (screen.size + 2 * screen.QUIET)
    assert px >= 4, "modules under 4px will not scan reliably"
    assert x0 >= 0 and x0 + side <= th.WIDTH
    assert y0 >= 0 and y0 + side <= th.HEIGHT
    drawn = _drawn(rc).lower()
    assert "every address" in drawn and "forever" in drawn, \
        "the permanent-exposure sentence is missing"
    assert drv.xpub_fingerprint(enrolled[0]["xpub"]) in _drawn(rc)


# ---------------------------------------------------------------------------
# Nothing new clips

def test_no_account_screen_draws_text_past_the_edges(enrolled):
    app, lst = _list_screen()
    screens = [
        lst,
        S.AccountManageScreen(enrolled[0]),
        S.AccountDeleteScreen(enrolled[0]),
        S.AccountQRScreen(enrolled[0]),
        S.NicknameScreen("abcdefghij0123", lambda a, t: None),
    ]
    for screen in screens:
        rc = RecordingCanvas()
        screen.draw(app, rc)
        for x, y, s, scale, font in rc.texts:
            cw = font.width if font is not None else th.CHAR_W * scale
            chh = font.height if font is not None else th.CHAR_H * scale
            assert x + len(s) * cw <= th.WIDTH, (
                "%s clips right: %r" % (type(screen).__name__, s))
            assert y + chh <= th.HEIGHT, (
                "%s clips bottom: %r" % (type(screen).__name__, s))
        for b in getattr(screen, "buttons", ()):
            assert b.x >= 0 and b.y >= 0
            assert b.x + b.w <= th.WIDTH
            assert b.y + b.h <= th.HEIGHT, (
                "%s button %r past the bottom" % (type(screen).__name__, b.label))
