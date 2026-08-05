"""Enrolment must store the account of the wallet the user just compared.

This file exists because of a real defect, not a hypothetical one. EnrolScreen
called account_xpub() without the session passphrase, while every address the
user compared on the way to that button was derived WITH it. The device showed
MATCH against a passphrase wallet and then stored the account key of the
no-passphrase wallet. Nothing failed loudly: later checks from Accounts derived
addresses of a wallet the user does not own and reported confident mismatches
against a perfectly good backup.

That is the sharpest failure this product has -- it is the exact thing the
device is sold to prevent, produced by the device itself -- and no test in the
suite covered enrolment with a non-empty passphrase, which is why it survived.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as drv  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402
from seedwitness.ui import flow_address as FA  # noqa: E402

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")
PHRASE = "correct horse battery staple"
T84 = [t for t in drv.PATH_TEMPLATES if t.bip == 84][0]


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    drv.clear_seed_cache()
    yield
    drv.clear_seed_cache()


def _enrol_with(passphrase):
    app = App()
    app.passphrase = passphrase
    app.last_mnemonic = M
    screen = FA.EnrolScreen(M, T84)
    app.push(screen)
    screen._enrol(app)
    return acct.load()


def test_enrolment_stores_the_passphrase_wallets_account():
    """The one that matters. The stored xpub must be the passphrase wallet's,
    which is the wallet whose addresses the user just compared."""
    records = _enrol_with(PHRASE)
    assert len(records) == 1
    _, expected = drv.account_xpub(M, T84, passphrase=PHRASE)
    assert records[0]["xpub"] == expected


def test_enrolment_without_a_passphrase_is_unchanged():
    records = _enrol_with("")
    _, expected = drv.account_xpub(M, T84, passphrase="")
    assert records[0]["xpub"] == expected


def test_the_two_wallets_are_actually_different():
    """Guards the guard. If a passphrase did not change the account key, both
    tests above would pass against a broken implementation."""
    _, with_pp = drv.account_xpub(M, T84, passphrase=PHRASE)
    _, without = drv.account_xpub(M, T84, passphrase="")
    assert with_pp != without


def test_enrolled_account_derives_the_address_the_user_compared():
    """End to end, in the units the user sees: the address shown during
    verification and the address the enrolled account produces later must be
    the same string. This is the assertion whose absence let the defect ship."""
    records = _enrol_with(PHRASE)
    shown_path, shown = drv.derive_address(M, T84, index=0, passphrase=PHRASE)
    later = drv.address_from_xpub(records[0]["xpub"], T84, index=0)
    assert later == shown, (
        "enrolled account derives %s but verification showed %s (%s)"
        % (later, shown, shown_path))


def test_the_derivation_screen_states_which_wallet_it_will_use():
    """A footnote that says 'assumes no BIP39 passphrase' while one is armed
    is worse than no footnote: it tells the user the mismatch they are about
    to see is someone else's fault."""
    from mock_canvas import MockCanvas

    class Recording(MockCanvas):
        def __init__(self):
            super().__init__()
            self.drawn = []

        def text(self, x, y, s, color, bg=None, scale=1, font=None):
            self.drawn.append(s)
            return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    def _footnotes(passphrase):
        app = App()
        app.passphrase = passphrase
        canvas = Recording()
        FA.DerivationPathScreen(M).draw(app, canvas)
        return " ".join(canvas.drawn)

    armed = _footnotes(PHRASE)
    assert "SET" in armed
    assert "no BIP39 passphrase" not in armed

    plain = _footnotes("")
    assert "no BIP39 passphrase" in plain
    assert "SET" not in plain
