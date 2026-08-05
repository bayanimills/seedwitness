"""The xpub path must agree with the seed path exactly, or enrolment turns a
correct backup into a false mismatch.

This is the sharpest possible failure for this device. Someone enrols an
account, later checks an address, gets a mismatch, and concludes their backup
is wrong -- when in fact the two derivation routes disagreed. A verification
tool that manufactures false alarms is worse than no tool, because it sends
people to re-do backups that were fine.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

from seedwitness import accounts, derive as d  # noqa: E402

# The canonical BIP39 test mnemonic. Its BIP44/49/84/86 first receive
# addresses are published in the BIP texts themselves, so these are external
# reference values, not numbers this project made up and then agreed with.
M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")

KNOWN_FIRST_ADDRESS = {
    44: "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA",
    49: "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf",
    84: "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    86: "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
}


def _template(bip):
    return [t for t in d.PATH_TEMPLATES if t.bip == bip][0]


@pytest.mark.parametrize("bip", [44, 49, 84, 86])
def test_xpub_path_reproduces_the_published_address(bip):
    t = _template(bip)
    _, xpub = d.account_xpub(M, t)
    assert d.address_from_xpub(xpub, t, index=0) == KNOWN_FIRST_ADDRESS[bip]


@pytest.mark.parametrize("bip", [44, 49, 84, 86])
@pytest.mark.parametrize("index", [0, 1, 5])
def test_xpub_path_agrees_with_the_seed_path(bip, index):
    """The whole enrolment premise. If these two ever diverge, every enrolled
    account starts reporting false mismatches."""
    t = _template(bip)
    _, xpub = d.account_xpub(M, t)
    _, seed_addr = d.derive_address(M, t, index=index)
    assert d.address_from_xpub(xpub, t, index=index) == seed_addr


def test_change_addresses_also_agree():
    t = _template(84)
    _, xpub = d.account_xpub(M, t)
    _, seed_addr = d.derive_address(M, t, change=1, index=0)
    assert d.address_from_xpub(xpub, t, change=1, index=0) == seed_addr


def test_account_one_is_a_different_account():
    """Guards an off-by-one in the account level. A wrong account derives
    valid-looking addresses that are simply not yours, which looks exactly
    like the failure this device exists to detect."""
    t = _template(84)
    _, a0 = d.account_xpub(M, t, account=0)
    _, a1 = d.account_xpub(M, t, account=1)
    assert a0 != a1
    assert d.address_from_xpub(a0, t) != d.address_from_xpub(a1, t)


def test_xpub_never_carries_a_private_key():
    """An account xpub must be public. If a serialisation change ever leaked
    an xprv into storage, enrolment would be writing the seed to flash."""
    t = _template(84)
    _, xpub = d.account_xpub(M, t)
    assert xpub.startswith("xpub")
    assert "prv" not in xpub


def test_fingerprint_is_stable_and_short():
    t = _template(84)
    _, xpub = d.account_xpub(M, t)
    fp = d.xpub_fingerprint(xpub)
    assert len(fp) == 8
    assert fp == d.xpub_fingerprint(xpub)


def test_accounts_roundtrip(tmp_path):
    p = str(tmp_path / "accounts.json")
    accounts.add("Cold", "xpubAAA", 84, path=p)
    accounts.add("Hot", "xpubBBB", 44, path=p)
    got = accounts.load(p)
    assert [r["label"] for r in got] == ["Cold", "Hot"]


def test_re_enrolling_the_same_xpub_renames_rather_than_duplicates(tmp_path):
    p = str(tmp_path / "accounts.json")
    accounts.add("Cold", "xpubAAA", 84, path=p)
    accounts.add("Savings", "xpubAAA", 84, path=p)
    got = accounts.load(p)
    assert len(got) == 1 and got[0]["label"] == "Savings"


def test_corrupt_store_does_not_wedge_the_device(tmp_path):
    """A truncated write must degrade to 'no accounts', never to an exception
    out of a screen constructor."""
    p = tmp_path / "accounts.json"
    p.write_text('[{"label": "half')
    assert accounts.load(str(p)) == []


def test_records_missing_required_fields_are_dropped(tmp_path):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps([{"label": "no xpub"}, {"xpub": "x", "bip": 84}]))
    assert len(accounts.load(str(p))) == 1


def test_account_limit_is_enforced(tmp_path):
    p = str(tmp_path / "accounts.json")
    for i in range(accounts.MAX_ACCOUNTS):
        accounts.add("a%d" % i, "xpub%d" % i, 84, path=p)
    with pytest.raises(ValueError):
        accounts.add("overflow", "xpubZ", 84, path=p)
