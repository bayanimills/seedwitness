"""SLIP-132 display forms: the same account key, four different spellings.

A wallet asking for a zpub shows a zpub, so a user comparing this device's
xpub against it sees two CORRECT values that look like a mismatch -- on a
device whose whole job is telling match from mismatch. The fix is offering
the alternate spelling, but ONLY the one matching the account's own script
type: a zpub minted from a BIP44 account tells the importing wallet to derive
native SegWit from a legacy account, a valid-looking key watching the wrong
addresses. So BIP49 offers ypub, BIP84 offers zpub, BIP44 offers nothing
(xpub IS its form) and BIP86 offers nothing (SLIP-132 predates taproot).

The conversion is base58check re-encoding with different version bytes; the
key material must be bit-identical, the QR must encode exactly what the
screen says it shows, and the account fingerprint must stay computed from the
canonical xpub so it never changes with the display form.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
import zxingcpp  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from embit import base58  # noqa: E402
from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as d  # noqa: E402
from seedwitness import qr as qrm  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")

XPUB_VERSION = b"\x04\x88\xb2\x1e"


def _template(bip):
    return next(t for t in d.PATH_TEMPLATES if t.bip == bip)


def _xpub(bip):
    return d.account_xpub(M, _template(bip))[1]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    return acct


def _rec(store, bip, label="acct"):
    return store.add(label, _xpub(bip), bip)


# ---------------------------------------------------------------------------
# The conversion itself

@pytest.mark.parametrize("bip,prefix", [(49, "ypub"), (84, "zpub")])
def test_alternate_decodes_to_identical_key_material(bip, prefix):
    """Only the four version bytes may differ. Anything else changed would be
    a different key wearing a familiar prefix."""
    xpub = _xpub(bip)
    name, alt = d.slip132_form(xpub, bip)
    assert name == prefix
    assert alt.startswith(prefix), "version bytes do not spell %s" % prefix
    raw_x = base58.decode_check(xpub)
    raw_a = base58.decode_check(alt)
    assert raw_a[4:] == raw_x[4:], "key material changed in re-encoding"
    assert raw_a[:4] == d.SLIP132[bip][1]
    assert raw_x[:4] == XPUB_VERSION


@pytest.mark.parametrize("bip", [44, 86])
def test_no_alternate_for_bip44_or_bip86(bip):
    """BIP44: xpub IS the SLIP-132 form. BIP86: SLIP-132 predates taproot.
    Offering ypub/zpub for either would mint a key that imports cleanly and
    watches the wrong addresses."""
    assert d.slip132_form(_xpub(bip), bip) is None


def test_alternate_round_trips_to_the_canonical_xpub():
    """Re-encoding the zpub back with xpub version bytes must reproduce the
    canonical string exactly -- the strongest statement that nothing but the
    prefix moved."""
    xpub = _xpub(84)
    _, zpub = d.slip132_form(xpub, 84)
    back = base58.encode_check(XPUB_VERSION + base58.decode_check(zpub)[4:])
    assert back == xpub


# ---------------------------------------------------------------------------
# The screen: QR follows the display, fingerprint does not

def test_qr_encodes_the_displayed_form(store):
    """Encoding one spelling while labelling another would rebuild the exact
    mismatch this feature removes."""
    rec = _rec(store, 84)
    _, zpub = d.slip132_form(rec["xpub"], 84)
    screen = S.AccountQRScreen(rec, slip132=True)
    assert screen.form == "zpub"
    assert screen.key == zpub
    assert (screen.size, screen.mat) == qrm.byte_qr(zpub.encode())
    plain = S.AccountQRScreen(rec)
    assert plain.form == "xpub"
    assert (plain.size, plain.mat) == qrm.byte_qr(rec["xpub"].encode())


def test_zpub_screen_pixels_decode_to_the_zpub(store):
    """Through the sim canvas and an independent decoder, like the xpub
    test in test_account_manage.py: the rendered symbol must hand a wallet
    the identical SLIP-132 string."""
    rec = _rec(store, 84)
    screen = S.AccountQRScreen(rec, slip132=True)
    canvas = MockCanvas()
    screen.draw(App(), canvas)
    img = canvas.image.resize((canvas.width * 3, canvas.height * 3))
    res = zxingcpp.read_barcode(img)
    assert res is not None, "the on-screen zpub frame did not decode"
    assert res.text == d.slip132_form(rec["xpub"], 84)[1]


def test_fingerprint_stays_canonical_across_forms(store):
    """The id names the account, not its spelling. It must match what was
    written down at enrolment whichever form is on screen."""
    rec = _rec(store, 84)
    fp = d.xpub_fingerprint(rec["xpub"])
    assert S.AccountQRScreen(rec).fp == fp
    assert S.AccountQRScreen(rec, slip132=True).fp == fp


def test_slip132_request_on_a_taproot_account_falls_back_to_xpub(store):
    """Defensive: no UI path offers it, but a stale or hand-edited store must
    not conjure a nonexistent form."""
    rec = _rec(store, 86)
    screen = S.AccountQRScreen(rec, slip132=True)
    assert screen.form == "xpub"
    assert screen.key == rec["xpub"]


# ---------------------------------------------------------------------------
# The manage screen offers only correct answers

def test_manage_offers_only_the_matching_form(store):
    labels = {bip: [b.label for b in S.AccountManageScreen(_rec(store, bip)).buttons]
              for bip in (44, 49, 84, 86)}
    assert "ypub QR" in labels[49] and "zpub QR" not in labels[49]
    assert "zpub QR" in labels[84] and "ypub QR" not in labels[84]
    for bip in (44, 86):
        assert not any("ypub" in l or "zpub" in l for l in labels[bip]), \
            "BIP%d must not offer a SLIP-132 form" % bip
        assert "Show xpub QR" in labels[bip]


def test_taproot_gap_is_explained_on_screen(store):
    """BIP86 has no SLIP-132 form; the screen says so rather than leaving an
    unexplained gap next to the accounts that do offer one."""
    rec = _rec(store, 86)

    class RC(MockCanvas):
        def __init__(self):
            super().__init__()
            self.texts = []

        def text(self, x, y, s, color, bg=None, scale=1, font=None):
            self.texts.append(s)
            return super().text(x, y, s, color, bg=bg, scale=scale, font=font)

    rc = RC()
    S.AccountQRScreen(rec).draw(App(), rc)
    drawn = " ".join(rc.texts).lower()
    assert "taproot" in drawn and "no zpub" in drawn
    # and the accounts that DO have a form don't carry the taproot note
    rc = RC()
    S.AccountQRScreen(_rec(store, 84)).draw(App(), rc)
    assert "taproot" not in " ".join(rc.texts).lower()
