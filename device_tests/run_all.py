"""Runs under the REAL MicroPython interpreter (not CPython/pytest) --
`micropython device_tests/run_all.py`.

This exists because CPython testing (tests/, via pytest) categorically
cannot catch MicroPython-specific runtime gaps: it was exactly this second
test path, run against a real `micropython` unix-port binary, that surfaced
(a) MicroPython's bytes type not supporting extended-step slicing, breaking
embit's pure-Python secp256k1 fallback, (b) the missing hashlib.pbkdf2_hmac /
sha512, and (c) micropython-lib's own hmac.HMAC.copy() being broken under
this interpreter (calls __new__, unsupported for plain user classes). None
of those would show up in a CPython-only test suite. No pytest under
MicroPython, so this is a minimal hand-rolled runner: each check() call
prints PASS/FAIL and a final summary; a nonzero exit code means a real
failure, for CI/scripting.
"""
import sys

sys.path.insert(0, "device/mp_shims")
sys.path.insert(0, "device")

import seedwitness  # noqa: E402  (applies compat patches)
from seedwitness import derive as drv  # noqa: E402
from seedwitness import entropy as ent  # noqa: E402
from seedwitness import mnemonic as mn  # noqa: E402

import hashlib  # noqa: E402
from pbkdf2 import pbkdf2_hmac as shim_pbkdf2_hmac  # noqa: E402

_failures = []


def check(name, condition):
    if condition:
        print("PASS", name)
    else:
        print("FAIL", name)
        _failures.append(name)


# --- compat.py's patch actually landed, not just "didn't crash" ---
check(
    "hashlib.pbkdf2_hmac was patched to mp_shims' implementation",
    hashlib.pbkdf2_hmac is shim_pbkdf2_hmac,
)

# --- entropy / roll-count math ---
check("required_rolls d6/12 == 50", ent.required_rolls(ent.D6, 12) == 50)
check("required_rolls d6/24 == 100 (ceil, not SeedSigner's 99)", ent.required_rolls(ent.D6, 24) == 100)
check("required_rolls coin/12 == 128", ent.required_rolls(ent.COIN, 12) == 128)
check("D12 digit_width == 2", ent.D12.digit_width == 2)
check("D12 encode_roll(3) == '03'", ent.D12.encode_roll(3) == "03")

session = ent.RollSession(ent.D6, 12)
for i in range(session.target_rolls):
    session.add_roll((i % 6) + 1)
check("roll session completes at target count", session.is_complete)
entropy = session.final_entropy()
check("final_entropy is 16 bytes for 12-word", len(entropy) == 16)

try:
    session.add_roll(1)
    check("add_roll past completion raises", False)
except RuntimeError:
    check("add_roll past completion raises", True)

try:
    ent.RollSession(ent.D6, 12).add_roll(7)
    check("out-of-range roll raises ValueError", False)
except ValueError:
    check("out-of-range roll raises ValueError", True)

# --- BIP39 mnemonic + checksum breakdown (known test vector) ---
KNOWN_ENTROPY_12 = bytes(16)  # all zero bytes -> canonical BIP39 vector
mnemonic12 = mn.entropy_to_mnemonic(KNOWN_ENTROPY_12)
check(
    "known 12-word BIP39 test vector",
    mnemonic12 == "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about",
)
check("mnemonic passes validate_mnemonic", mn.validate_mnemonic(mnemonic12))

b = mn.checksum_breakdown(mnemonic12)
check("checksum breakdown: word_index == 12", b.word_index == 12)
check("checksum breakdown: 7 entropy + 4 checksum bits", b.leftover_entropy_bits == 7 and b.checksum_bits == 4)
check("checksum breakdown: final word == 'about'", b.word == "about")

tampered_words = mnemonic12.split()
tampered_words[-1] = "zoo"
check("tampered mnemonic fails validation", not mn.validate_mnemonic(" ".join(tampered_words)))

# --- backup-confirmation match logic ---
check("mnemonics_match: exact", mn.mnemonics_match(mnemonic12, mnemonic12))
mismatch_words = mnemonic12.split()
mismatch_words[0] = "ability"
check("mnemonics_match: typo detected", not mn.mnemonics_match(" ".join(mismatch_words), mnemonic12))

# --- full derivation pipeline: mnemonic -> seed (PBKDF2) -> BIP32 -> address ---
# This is the path that specifically exercises mp_shims/pbkdf2.py and
# mp_shims/secp256k1.py's _reverse64 patch -- the two hardest-won fixes.
by_bip = {t.bip: t for t in drv.PATH_TEMPLATES}
KNOWN_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)
EXPECTED_ADDRESSES = {
    44: "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA",
    49: "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf",
    84: "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    86: "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
}
for bip, expected_addr in EXPECTED_ADDRESSES.items():
    path, addr = drv.derive_address(KNOWN_MNEMONIC, by_bip[bip])
    check("BIP%d derives correct known-vector address" % bip, addr == expected_addr)
    check("BIP%d derivation path string" % bip, path == "m/%dh/0h/0h/0/0" % bip)

_, addr_idx1 = drv.derive_address(KNOWN_MNEMONIC, by_bip[84], index=1)
check("different index gives different address", addr_idx1 != EXPECTED_ADDRESSES[84])

_, testnet_addr = drv.derive_address(KNOWN_MNEMONIC, by_bip[84], network="test")
check("testnet address has tb1q prefix", testnet_addr.startswith("tb1q"))

print()
if _failures:
    print("%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
else:
    print("ALL CHECKS PASSED under real MicroPython")
