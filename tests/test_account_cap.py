"""The five visible account rows are also the enrolment/storage limit.

This is deliberately an explicit product-limit test rather than a loop over
``MAX_ACCOUNTS``: the old value (16) made that sort of test pass while records
6--16 were persisted but unreachable from the five-row UI.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import accounts as acct  # noqa: E402
from seedwitness.ui import flow_address as FA  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


def _fill(store, count=5):
    for i in range(count):
        store.add("account %d" % i, "xpub%d" % i, 84)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    return acct


def test_product_limit_is_exactly_five_and_store_refuses_a_sixth(store):
    assert store.MAX_ACCOUNTS == 5
    _fill(store)

    with pytest.raises(ValueError, match="at most 5 accounts"):
        store.add("sixth", "xpub5", 84)

    assert len(store.load()) == 5


def test_full_enrol_screen_explains_refusal_and_offers_accounts(store):
    _fill(store)
    screen = FA.EnrolScreen("mnemonic is not used while full", object())
    app = App()
    canvas = MockCanvas()

    screen.draw(app, canvas)

    assert [button.label for button in screen.buttons] == ["Accounts", "Back"]


def test_full_enrol_handler_never_derives_or_throws(store, monkeypatch):
    """The domain exception must not escape a tap handler into CrashScreen."""
    _fill(store)
    called = []
    monkeypatch.setattr(FA.drv, "account_xpub",
                        lambda *args, **kwargs: called.append(True))
    screen = FA.EnrolScreen("mnemonic", object())

    screen._enrol(App())

    assert called == []
    assert len(store.load()) == 5
    assert screen.done is None


def test_re_enrolling_existing_xpub_can_still_rename_at_cap(store):
    _fill(store)

    store.add("renamed", "xpub2", 84)

    assert len(store.load()) == 5
    assert store.load()[2]["label"] == "renamed"
