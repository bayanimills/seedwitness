"""Face labels are presentation only and must not touch the hash input.

A coin has sides, not numbers. Showing "1" and "2" makes the user translate
heads/tails into digits, 128 or 256 times, under exactly the conditions where
a slip is unrecoverable -- a wrong flip silently changes the seed.

But the label must stay cosmetic. The digit encoding is what gets hashed, so
if renaming a face changed the encoding it would change every seed derived
from that source. These tests hold that line.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from seedwitness import entropy as ent  # noqa: E402


def test_coin_faces_are_named_not_numbered():
    assert ent.COIN.face_label(1) == "Heads"
    assert ent.COIN.face_label(2) == "Tails"


def test_dice_faces_stay_numeric():
    for src in (ent.D6, ent.D8, ent.D12):
        for v in range(1, src.sides + 1):
            assert src.face_label(v) == str(v)


def test_labels_do_not_change_the_encoding():
    """The hash input must be identical to before face names existed."""
    assert ent.COIN.encode_roll(1) == "1"
    assert ent.COIN.encode_roll(2) == "2"
    assert ent.D12.encode_roll(3) == "03"


def test_a_known_coin_sequence_still_gives_a_known_seed():
    """Pins the actual bytes: renaming a face must not move any seed."""
    s = ent.RollSession(ent.COIN, 12)
    for i in range(s.target_rolls):
        s.add_roll(1 if i % 2 == 0 else 2)
    assert s.roll_string() == "12" * (s.target_rolls // 2)
    assert s.final_entropy().hex() == (
        __import__("hashlib").sha256(s.roll_string().encode()).digest()[:16].hex())


def test_every_source_states_a_fair_action():
    """The word 'fair' is the only place the entropy requirement is visible to
    the user, and the device cannot verify it -- see SECURITY.md."""
    for src in (ent.D6, ent.D8, ent.D12, ent.COIN):
        assert "Fair" in src.action


def test_flip_counts_match_bip39_entropy():
    """A coin gives exactly one bit, so the counts should be the entropy size:
    128 flips for 12 words, 256 for 24. The checksum is derived, not flipped."""
    assert ent.required_rolls(ent.COIN, 12) == 128
    assert ent.required_rolls(ent.COIN, 24) == 256
