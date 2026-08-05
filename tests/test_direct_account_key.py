"""Read-only account-key display after direct seed verification."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as drv  # noqa: E402
from seedwitness.ui import flow_address as FA  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)
ADDRESS = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
XPUB = "xpub-test-only"


def _template(bip):
    return next(t for t in drv.PATH_TEMPLATES if t.bip == bip)


def _direct_screen(bip=84):
    return FA.AddressDisplayScreen(
        MNEMONIC, _template(bip), 0, "m/%dh/0h/0h/0/0" % bip, ADDRESS)


def test_direct_address_offers_account_key_but_enrolled_address_does_not():
    direct = _direct_screen()
    assert "Account Key" in [b.label for b in direct.buttons]
    assert "Enrol Account" in [b.label for b in direct.buttons]

    enrolled = FA.AddressDisplayScreen(
        None, _template(84), 0, "m/84h/0h/0h/0/0", ADDRESS, xpub=XPUB)
    assert enrolled.key_button is None
    assert "Account Key" not in [b.label for b in enrolled.buttons]


def test_account_key_uses_session_passphrase_and_writes_nothing(
        tmp_path, monkeypatch):
    store = tmp_path / "accounts.json"
    monkeypatch.setattr(acct, "PATH", str(store))
    calls = []

    def fake_account_xpub(mnemonic, template, passphrase="", **kwargs):
        calls.append((mnemonic, template.bip, passphrase))
        return "m/84h/0h/0h", XPUB

    monkeypatch.setattr(FA.drv, "account_xpub", fake_account_xpub)
    monkeypatch.setattr(
        FA.acct, "add",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("persisted")))

    screen = _direct_screen()
    app = App()
    app.passphrase = "correct horse"
    app.stack = [screen]
    b = screen.key_button
    screen.handle_tap(app, MockCanvas(), b.x + 1, b.y + 1)

    assert calls == [(MNEMONIC, 84, "correct horse")]
    assert isinstance(app.screen, FA.AccountKeyScreen)
    assert app.screen.xpub == XPUB
    assert app.screen.path == "m/84h/0h/0h"
    assert not store.exists()


def test_account_key_offers_only_the_matching_public_key_forms():
    labels = {
        bip: [b.label for b in FA.AccountKeyScreen(
            XPUB, _template(bip), "m/%dh/0h/0h" % bip).buttons]
        for bip in (44, 49, 84, 86)
    }
    assert "ypub QR" in labels[49] and "zpub QR" not in labels[49]
    assert "zpub QR" in labels[84] and "ypub QR" not in labels[84]
    for bip in (44, 86):
        assert "Show xpub QR" in labels[bip]
        assert not any("ypub" in label or "zpub" in label
                       for label in labels[bip])


def test_account_key_copy_keeps_address_as_the_verification_gate():
    class RecordingCanvas(MockCanvas):
        def __init__(self):
            super().__init__()
            self.texts = []

        def text(self, x, y, s, color, bg=None, scale=1, font=None):
            self.texts.append((y, s))
            return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    for bip, expected in ((49, "ypub"), (84, "zpub"), (86, "no zpub")):
        screen = FA.AccountKeyScreen(
            XPUB, _template(bip), "m/%dh/0h/0h" % bip)
        canvas = RecordingCanvas()
        screen.draw(App(), canvas)
        body = " ".join(s for y, s in canvas.texts if y < 252).lower()
        assert "action stores nothing" in body
        assert "receive address" in body
        assert expected in body
        assert max(y for y, s in canvas.texts
                   if y < 252 and s not in ("BACK",)) < 252


def test_qr_choice_passes_ephemeral_record_and_selected_form(monkeypatch):
    seen = []

    class FakeQR:
        def __init__(self, rec, slip132=False):
            seen.append((rec, slip132))

    monkeypatch.setattr(
        FA, "flow_screen",
        lambda flow, name: FakeQR if (flow, name) == (
            "accounts", "AccountQRScreen") else None)
    screen = FA.AccountKeyScreen(XPUB, _template(84), "m/84h/0h/0h")
    app = App()
    screen._show_qr(app, False)
    screen._show_qr(app, True)

    assert [slip for rec, slip in seen] == [False, True]
    assert all(rec == {"xpub": XPUB, "bip": 84, "label": "direct check"}
               for rec, slip in seen)
    assert not hasattr(screen, "mnemonic")
