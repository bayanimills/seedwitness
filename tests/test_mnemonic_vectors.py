import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from seedwitness.mnemonic import (
    checksum_breakdown,
    entropy_to_mnemonic,
    mnemonic_to_seed,
    mnemonics_match,
    validate_mnemonic,
)

VECTORS = [
    (
        bytes.fromhex("00000000000000000000000000000000000000000000000000000000000000")[:16],
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
    ),
    (
        bytes.fromhex("7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f"),
        "legal winner thank year wave sausage worth useful legal winner thank yellow",
    ),
    (
        bytes.fromhex(
            "0000000000000000000000000000000000000000000000000000000000000000000000000000"
        )[:32],
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon art",
    ),
]


def test_known_bip39_vectors():
    for entropy, expected_mnemonic in VECTORS:
        assert entropy_to_mnemonic(entropy) == expected_mnemonic
        assert validate_mnemonic(expected_mnemonic)


def test_checksum_breakdown_12_word():
    _, m = VECTORS[0]
    b = checksum_breakdown(m)
    assert b.word_index == 12
    assert b.leftover_entropy_bits == 7
    assert b.checksum_bits == 4
    assert b.word == "about"


def test_checksum_breakdown_24_word():
    _, m = VECTORS[2]
    b = checksum_breakdown(m)
    assert b.word_index == 24
    assert b.leftover_entropy_bits == 3
    assert b.checksum_bits == 8
    assert b.word == "art"


def test_checksum_breakdown_normalizes_whitespace():
    _, m = VECTORS[0]
    padded = "  " + "  ".join(m.split()) + "  "
    b = checksum_breakdown(padded)
    assert b.word_index == 12
    assert b.word == "about"


def test_validate_rejects_tampered_mnemonic():
    words = VECTORS[0][1].split()
    words[-1] = "zoo"
    tampered = " ".join(words)
    assert not validate_mnemonic(tampered)


def test_validate_rejects_wrong_length():
    assert not validate_mnemonic("abandon abandon abandon")


def test_mnemonics_match_exact_and_whitespace_normalized():
    m = VECTORS[0][1]
    assert mnemonics_match(m, m)
    padded = "  " + "  ".join(m.split()) + "  "
    assert mnemonics_match(padded, m)


def test_mnemonics_match_rejects_typo_or_reorder():
    m = VECTORS[0][1]
    words = m.split()
    words[3] = "ability"
    assert not mnemonics_match(" ".join(words), m)

    words2 = m.split()
    words2[0], words2[-1] = words2[-1], words2[0]
    assert not mnemonics_match(" ".join(words2), m)


def test_mnemonic_to_seed_matches_known_vector():
    m = VECTORS[0][1]
    seed = mnemonic_to_seed(m)
    assert seed.hex() == (
        "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc"
        "19a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
    )


def test_mnemonic_to_seed_with_passphrase_differs_and_matches_known_vector():
    # "TREZOR" is the BIP39 spec's own published test passphrase for this
    # exact mnemonic -- not an arbitrary choice.
    m = VECTORS[0][1]
    seed_no_pass = mnemonic_to_seed(m)
    seed_with_pass = mnemonic_to_seed(m, passphrase="TREZOR")
    assert seed_with_pass != seed_no_pass
    assert seed_with_pass.hex() == (
        "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e534955"
        "31f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04"
    )


def test_mnemonic_to_seed_normalizes_whitespace():
    m = VECTORS[0][1]
    padded = "  " + "  ".join(m.split()) + "  "
    assert mnemonic_to_seed(padded) == mnemonic_to_seed(m)


def test_validate_mnemonic_rejects_empty_string():
    assert not validate_mnemonic("")


def test_validate_mnemonic_rejects_non_wordlist_words():
    assert not validate_mnemonic(" ".join(["notaword"] * 12))


def test_validate_mnemonic_accepts_whitespace_variants():
    m = VECTORS[0][1]
    assert validate_mnemonic("  " + "  ".join(m.split()) + "  ")
    assert validate_mnemonic(m.replace(" ", "\t"))
