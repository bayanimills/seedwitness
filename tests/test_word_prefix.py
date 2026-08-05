"""words_with_prefix() replaced a linear scan with a binary search, because
on-device the wordlist lives on flash and a scan would mean 2048 file reads
per keystroke. Binary search is easy to get subtly wrong at the boundaries,
and this feeds the manual seed-entry keyboard -- if it silently dropped or
misordered candidates, someone re-entering an existing mnemonic could fail to
find a legitimate word. These pin the behaviour against the real wordlist.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from seedwitness.mnemonic import bip39, words_with_prefix  # noqa: E402

WORDS = list(bip39.WORDLIST)


def _scan(prefix, limit=4):
    """The obvious implementation, as the reference to match."""
    return [w for w in WORDS if w.startswith(prefix)][:limit]


def test_matches_a_linear_scan_for_every_single_letter():
    for ch in "abcdefghijklmnopqrstuvwxyz":
        assert words_with_prefix(ch) == _scan(ch), "mismatch for %r" % ch


def test_matches_a_linear_scan_for_every_two_letter_prefix():
    seen = {w[:2] for w in WORDS}
    for prefix in sorted(seen):
        assert words_with_prefix(prefix) == _scan(prefix), "mismatch for %r" % prefix


def test_finds_every_word_by_its_full_spelling():
    """A fully typed word must always appear, including first and last."""
    for word in (WORDS[0], WORDS[1], WORDS[len(WORDS) // 2], WORDS[-2], WORDS[-1]):
        assert word in words_with_prefix(word)


def test_unknown_prefix_returns_nothing():
    assert words_with_prefix("qqq") == []
    # 'zzz' probes past the end of the list, where an off-by-one would IndexError
    assert words_with_prefix("zzz") == []


def test_respects_the_limit():
    many = _scan("a", limit=999)
    assert len(many) > 4
    assert len(words_with_prefix("a", limit=4)) == 4
    assert words_with_prefix("a", limit=2) == many[:2]


def test_empty_prefix_returns_the_head_of_the_list():
    assert words_with_prefix("", limit=3) == WORDS[:3]


def test_results_stay_in_wordlist_order():
    got = words_with_prefix("ab", limit=4)
    assert got == sorted(got)
