"""Data read back from flash is unauthenticated, and must never wedge a screen.

Three separate crashes, all from the same seam: the code checks that a field
is PRESENT but not that it has the right TYPE, then indexes or formats it.

The accounts one was the worst, because the crash happened inside draw().
A record like {"xpub": 5} passed the key-only check, then raised
AttributeError when xpub_fingerprint called .encode() on an int. Since that
is a draw failure, it repeated on every single entry to Accounts, so the
damaged record could never be reached to DELETE it. An enrolled xpub the user
cannot remove is exactly the permanent privacy exposure MAX_ACCOUNTS exists to
prevent, with the consent taken away.

The wordmark one is worse in kind though not in likelihood: it is on the BOOT
path, before any crash containment exists, so an uncaught IndexError from a
truncated asset leaves a lit panel, a dead app and a live REPL.

Each case here was reproduced against the original code before being fixed.
"""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402
from seedwitness import accounts as acct  # noqa: E402
from seedwitness.ui import app as APP  # noqa: E402
from seedwitness.ui import flow_accounts as FA  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

GOOD_XPUB = ("xpub6CatWdiZiodmUeTDp8LT5or8nmbKNcuyvz7WyksVFkKB4RHwCD3Xyuv"
             "PEbvqAQY3rAPshWcMLoP2fMFMKHPJ4ZeZXYVUhLv1VMrjPC7PW6V")


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    return tmp_path


def _write(records):
    io.open(acct.PATH, "w", encoding="utf-8").write(json.dumps(records))


# --------------------------------------------------------------------------
# accounts.json field types
# --------------------------------------------------------------------------

BAD_RECORDS = [
    ("xpub is an int", {"xpub": 5, "bip": 84, "account": 0}),
    ("xpub is null", {"xpub": None, "bip": 84, "account": 0}),
    ("xpub is a list", {"xpub": ["x"], "bip": 84, "account": 0}),
    ("bip is a string", {"xpub": GOOD_XPUB, "bip": "84", "account": 0}),
    ("bip is a bool", {"xpub": GOOD_XPUB, "bip": True, "account": 0}),
    ("account is a string", {"xpub": GOOD_XPUB, "bip": 84, "account": "0"}),
    ("account is null", {"xpub": GOOD_XPUB, "bip": 84, "account": None}),
    # A second round found these: the first pass checked xpub, bip and
    # account, and missed `label`, which recreated the identical crash one
    # field over. AccountListScreen does `rec.get("label") or "account"`, so
    # only TRUTHY wrong types reach canvas.text and get sliced: {"label": 5}
    # raises TypeError, {"label": {...}} raises KeyError, both inside draw().
    ("label is an int", {"xpub": GOOD_XPUB, "bip": 84, "account": 0, "label": 5}),
    ("label is a dict", {"xpub": GOOD_XPUB, "bip": 84, "account": 0,
                         "label": {"a": 1}}),
    ("label is a list", {"xpub": GOOD_XPUB, "bip": 84, "account": 0,
                         "label": ["x"]}),
    ("label is falsy int", {"xpub": GOOD_XPUB, "bip": 84, "account": 0,
                            "label": 0}),
    # bool is an int in Python, so True survived the int check and rendered
    # as "m/84h/0h/1h"; -1 rendered "m/84h/0h/-1h". The path line is the
    # user's stated check that they have the right wallet.
    ("account is a bool", {"xpub": GOOD_XPUB, "bip": 84, "account": True}),
    ("account is negative", {"xpub": GOOD_XPUB, "bip": 84, "account": -1}),
]


@pytest.mark.parametrize("label,rec", BAD_RECORDS, ids=[r[0] for r in BAD_RECORDS])
def test_a_type_corrupt_record_never_reaches_a_screen_that_will_crash(label, rec):
    """load() is the gate. Either the record is dropped, or it survives with
    types the screens can actually format."""
    _write([rec])
    for kept in acct.load():
        assert isinstance(kept["xpub"], str), label
        assert isinstance(kept["bip"], int) and not isinstance(kept["bip"], bool), label
        if "account" in kept:
            assert isinstance(kept["account"], int), label
            assert not isinstance(kept["account"], bool), label
            assert kept["account"] >= 0, label
        if "label" in kept:
            assert isinstance(kept["label"], str), label


@pytest.mark.parametrize("label,rec", BAD_RECORDS, ids=[r[0] for r in BAD_RECORDS])
def test_the_accounts_list_draws_over_a_damaged_store(label, rec):
    """The original failure was a crash inside draw(), which repeats forever
    and makes the record undeletable."""
    _write([rec])
    FA.AccountListScreen().draw(App(), MockCanvas())


@pytest.mark.parametrize("label,rec", BAD_RECORDS, ids=[r[0] for r in BAD_RECORDS])
def test_opening_a_damaged_record_refuses_instead_of_raising(label, rec):
    _write([rec])
    app = App()
    screen = FA.AccountListScreen()
    for kept in acct.load():
        screen._open(app, kept)   # must not raise


def test_a_good_record_still_loads():
    """Guards the guard: type checks that reject everything would pass all
    the tests above and break the product."""
    _write([{"xpub": GOOD_XPUB, "bip": 84, "account": 0, "label": "test"}])
    records = acct.load()
    assert len(records) == 1
    assert records[0]["xpub"] == GOOD_XPUB
    FA.AccountListScreen().draw(App(), MockCanvas())


def test_a_damaged_record_does_not_take_good_ones_with_it():
    _write([{"xpub": 5, "bip": 84, "account": 0},
            {"xpub": GOOD_XPUB, "bip": 84, "account": 0, "label": "keep"}])
    records = acct.load()
    assert [r.get("label") for r in records] == ["keep"]


# --------------------------------------------------------------------------
# wordmark.bin, on the boot path
# --------------------------------------------------------------------------

TRUNCATIONS = [b"", b"SW", b"SWM1", b"SWM1\x01", b"SWM1\x02\xff\xff",
               b"SWM1\x01\x10", b"XXXX\x01\x10\x10\x02"]


@pytest.mark.parametrize("blob", TRUNCATIONS, ids=[repr(b) for b in TRUNCATIONS])
def test_a_truncated_wordmark_never_stops_the_device_booting(blob, tmp_path, monkeypatch):
    """The splash is decoration. An exception here unwinds out of
    run_on_hardware() before crash containment exists, leaving a lit panel, a
    dead app and a live REPL."""
    p = tmp_path / "wordmark.bin"
    p.write_bytes(blob)
    monkeypatch.setattr(APP, "WORDMARK_FILE", str(p))
    APP._splash_frame(MockCanvas())   # must not raise


def test_a_missing_wordmark_never_stops_the_device_booting(tmp_path, monkeypatch):
    monkeypatch.setattr(APP, "WORDMARK_FILE", str(tmp_path / "absent.bin"))
    APP._splash_frame(MockCanvas())


# --------------------------------------------------------------------------
# xpub CONTENT, not just its type
# --------------------------------------------------------------------------

MALFORMED_XPUBS = [
    "xpub6NOTVALID",
    "xpub",
    "",
    "x" * 111,
    GOOD_XPUB[:-1],            # one char short, checksum fails
    GOOD_XPUB[:-1] + "Z",      # right length, wrong checksum
    "ypub6CatWdiZiodmUeTDp8LT5or8nmbKNcuyvz7WyksVFkKB4RHwCD3XyuvPEbvqA",
]


@pytest.mark.parametrize("bad", MALFORMED_XPUBS, ids=lambda s: repr(s[:18]))
def test_a_malformed_xpub_refuses_instead_of_crashing_on_derive(bad):
    """A well-formed STRING that is not a valid extended key used to open the
    account screen happily, then raise ValueError out of HDKey.from_base58 the
    moment the user asked to derive, ending the session on the crash screen.
    DamagedAccountScreen exists for exactly this record."""
    _write([{"xpub": bad, "bip": 84, "account": 0, "label": "bad"}])
    app = App()
    screen = FA.AccountListScreen()
    screen.draw(app, MockCanvas())        # list must still render
    for kept in acct.load():
        screen._open(app, kept)
        assert type(app.screen).__name__ == "DamagedAccountScreen", bad


def test_a_valid_xpub_still_opens():
    """Guards the guard: validation that rejects everything would satisfy
    every test above and break the feature."""
    _write([{"xpub": GOOD_XPUB, "bip": 84, "account": 0, "label": "good"}])
    app = App()
    screen = FA.AccountListScreen()
    screen._open(app, acct.load()[0])
    assert type(app.screen).__name__ == "AddressIndexScreen"
