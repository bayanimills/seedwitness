import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from seedwitness.derive import PATH_TEMPLATES, PathTemplate, derive_address

MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)

# Expected addresses for m/{44,49,84,86}'/0'/0'/0/0 from this mnemonic (empty
# passphrase). BIP84's own spec test vector for this exact mnemonic publishes
# bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu independently of this codebase.
EXPECTED = {
    44: "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA",
    49: "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf",
    84: "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    86: "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
}


def test_all_path_templates_produce_expected_addresses():
    by_bip = {t.bip: t for t in PATH_TEMPLATES}
    for bip, expected_addr in EXPECTED.items():
        path, addr = derive_address(MNEMONIC, by_bip[bip])
        assert addr == expected_addr
        assert path == "m/%dh/0h/0h/0/0" % bip


def test_different_index_gives_different_address():
    by_bip = {t.bip: t for t in PATH_TEMPLATES}
    _, addr0 = derive_address(MNEMONIC, by_bip[84], index=0)
    _, addr1 = derive_address(MNEMONIC, by_bip[84], index=1)
    assert addr0 != addr1


def test_testnet_network_changes_address_prefix():
    by_bip = {t.bip: t for t in PATH_TEMPLATES}
    path, addr = derive_address(MNEMONIC, by_bip[84], network="test")
    assert path == "m/84h/1h/0h/0/0"
    assert addr.startswith("tb1q")


def test_unknown_script_kind_raises():
    bogus = PathTemplate("Bogus", 999, "not-a-real-kind")
    try:
        derive_address(MNEMONIC, bogus)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_non_default_account_and_change_produce_valid_distinct_paths():
    by_bip = {t.bip: t for t in PATH_TEMPLATES}
    path_a, addr_a = derive_address(MNEMONIC, by_bip[84], account=0, change=0, index=0)
    path_b, addr_b = derive_address(MNEMONIC, by_bip[84], account=1, change=0, index=0)
    path_c, addr_c = derive_address(MNEMONIC, by_bip[84], account=0, change=1, index=0)
    assert path_a == "m/84h/0h/0h/0/0"
    assert path_b == "m/84h/0h/1h/0/0"
    assert path_c == "m/84h/0h/0h/1/0"
    assert len({addr_a, addr_b, addr_c}) == 3  # all distinct
    for addr in (addr_a, addr_b, addr_c):
        assert addr.startswith("bc1q")
