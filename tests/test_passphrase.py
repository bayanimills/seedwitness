"""Diceware passphrase generation.

The failure this guards is silent and permanent: a passphrase is not
recoverable from anything else, so a biased or non-uniform mapping produces a
passphrase weaker than the number shown on screen, and nobody finds out.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

from seedwitness import passphrase as pp  # noqa: E402

WORDS = ROOT / "_build_mpy" / "eff_words.bin"
needs_build = pytest.mark.skipif(
    not WORDS.exists(), reason="run tools/build_mpy.sh first")


def test_the_list_length_is_exactly_six_to_the_fifth():
    """The whole reason this design has no rejection sampling.

    7776 == 6**5, so five D6 rolls map onto the list one-to-one. If the list
    were any other size the mapping would need modulo (biased) or rejection
    (a ceremony that sometimes tells the user to re-roll, which they will
    shortcut). Any change to the wordlist has to keep this true.
    """
    assert pp.WORD_COUNT == 6 ** pp.ROLLS_PER_WORD == 7776


def test_word_index_is_a_bijection_over_all_possible_rolls():
    """Exhaustive: every one of the 7776 roll combinations must map to a
    distinct index, and together they must cover the list exactly. This is
    uniformity, proved rather than argued."""
    seen = set()
    for a in range(1, 7):
        for b in range(1, 7):
            for c in range(1, 7):
                for d in range(1, 7):
                    for e in range(1, 7):
                        seen.add(pp.word_index([a, b, c, d, e]))
    assert len(seen) == 7776
    assert min(seen) == 0 and max(seen) == 7775


def test_index_endpoints_follow_diceware_convention():
    assert pp.word_index([1, 1, 1, 1, 1]) == 0
    assert pp.word_index([6, 6, 6, 6, 6]) == 7775
    # most significant roll first
    assert pp.word_index([1, 1, 1, 1, 2]) == 1
    assert pp.word_index([2, 1, 1, 1, 1]) == 6 ** 4


def test_rolls_outside_a_d6_are_refused():
    """A coin or D8 would need rejection sampling to stay uniform. Rather than
    implement that, generation is D6-only and anything else is an error."""
    for bad in ([0, 1, 1, 1, 1], [1, 1, 1, 1, 7], [1, 1, 1, 1, 8]):
        with pytest.raises(ValueError):
            pp.word_index(bad)
    with pytest.raises(ValueError):
        pp.word_index([1, 1, 1, 1])


@needs_build
def test_words_read_back_match_the_source_list():
    source = [w.strip() for w in
              (ROOT / "device" / "data" / "eff_large_wordlist.txt").read_text().splitlines()
              if w.strip()]
    assert len(source) == 7776
    for i in (0, 1, 42, 3887, 7774, 7775):
        assert pp.read_word(i, str(WORDS)) == source[i]


@needs_build
def test_first_and_last_words_are_the_published_eff_endpoints():
    """Pins the identity of the list itself, not just its shape. A substituted
    wordlist with the right length would otherwise pass everything above."""
    assert pp.read_word(0, str(WORDS)) == "abacus"
    assert pp.read_word(7775, str(WORDS)) == "zoom"


@needs_build
def test_a_generated_passphrase_is_lowercase_ascii():
    """The reason generation exists at all. BIP39 requires NFKD and
    MicroPython has no unicodedata, so anything non-ASCII would derive a
    different wallet here than in every other wallet. Generated output must be
    normalisation-invariant by construction."""
    rolls = [1, 2, 3, 4, 5] * 8
    words = pp.words_from_rolls(rolls, str(WORDS))
    assert len(words) == 8
    phrase = pp.to_passphrase(words)
    assert phrase.isascii()
    assert phrase == phrase.lower()
    import unicodedata
    assert unicodedata.normalize("NFKD", phrase) == phrase


@needs_build
def test_roll_count_must_be_a_whole_number_of_words():
    with pytest.raises(ValueError):
        pp.words_from_rolls([1, 2, 3, 4], str(WORDS))


def test_entropy_is_reported_rounded_down():
    """A security figure shown to someone choosing a length must never round
    in the flattering direction."""
    assert pp.bits_for(6) == 77      # 77.55 exact
    assert pp.bits_for(8) == 103     # 103.40
    assert pp.bits_for(10) == 129    # 129.25
    for n in pp.LENGTHS:
        assert pp.bits_for(n) <= n * 12.9250


def test_rolls_needed_matches_the_offered_lengths():
    assert pp.rolls_needed(6) == 30
    assert pp.rolls_needed(8) == 40
    assert pp.rolls_needed(10) == 50


def test_check_value_is_short_stable_and_not_the_passphrase():
    a = pp.check_value("correct horse battery staple")
    assert len(a) == 4 and a == pp.check_value("correct horse battery staple")
    assert a != pp.check_value("correct horse battery stapler")
    assert "correct" not in a


def test_default_length_clears_the_kdf_weakness():
    """BIP39 stretches a passphrase with only 2048 rounds of HMAC-SHA512, so
    the passphrase has to carry its own weight. The default must exceed the
    ~100-bit mark rather than sitting at the 77-bit floor."""
    assert pp.DEFAULT_LENGTH in pp.LENGTHS
    assert pp.bits_for(pp.DEFAULT_LENGTH) >= 100
