import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from seedwitness.entropy import D6, D8, D12, COIN, RollSession, required_rolls


def test_required_rolls_d6_meets_or_exceeds_seedsigner_convention():
    # SeedSigner publishes 50 rolls (12w) / 99 rolls (24w) for d6. 50*log2(6)=129.2
    # clears 128 bits fine, but 99*log2(6)=255.91 bits -- just *under* the 256-bit
    # nominal target (SeedSigner floors). We deliberately ceil instead, so the
    # nominal entropy bound is always met even if it costs one extra roll (100,
    # not 99, for d6/24-word).
    assert required_rolls(D6, 12) == 50
    assert required_rolls(D6, 24) == 100


def test_required_rolls_coin_is_exact_bit_count():
    assert required_rolls(COIN, 12) == 128
    assert required_rolls(COIN, 24) == 256


def test_required_rolls_d8_exact_division():
    assert required_rolls(D8, 12) == math.ceil(128 / 3)
    assert required_rolls(D8, 24) == math.ceil(256 / 3)


def test_digit_width_avoids_ambiguous_concatenation():
    assert D6.digit_width == 1
    assert D8.digit_width == 1
    assert D12.digit_width == 2  # values up to 12 need 2 digits, else "1"+"2" vs "12" collide
    assert D12.encode_roll(3) == "03"
    assert D12.encode_roll(12) == "12"


def test_roll_session_rejects_out_of_range():
    session = RollSession(D6, 12)
    try:
        session.add_roll(7)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_roll_session_final_entropy_length_and_determinism():
    session = RollSession(D6, 12)
    for i in range(session.target_rolls):
        session.add_roll((i % 6) + 1)
    assert session.is_complete
    ent = session.final_entropy()
    assert len(ent) == 16  # 128 bits

    session2 = RollSession(D6, 12)
    for i in range(session2.target_rolls):
        session2.add_roll((i % 6) + 1)
    assert session2.final_entropy() == ent


def test_live_preview_hash_changes_with_each_roll_avalanche():
    session = RollSession(D6, 24)
    session.add_roll(1)
    h1 = session.live_preview_hash()
    session.add_roll(2)
    h2 = session.live_preview_hash()
    assert h1 != h2
    diff = sum(a != b for a, b in zip(h1, h2))
    assert diff > len(h1) * 0.25


def test_final_entropy_raises_before_session_complete():
    session = RollSession(D6, 12)
    session.add_roll(1)  # nowhere near target_rolls=50
    try:
        session.final_entropy()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_add_roll_raises_once_already_complete():
    session = RollSession(D6, 12)
    for i in range(session.target_rolls):
        session.add_roll((i % 6) + 1)
    try:
        session.add_roll(1)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_coin_digit_width_and_encoding():
    assert COIN.digit_width == 1
    assert COIN.encode_roll(1) == "1"
    assert COIN.encode_roll(2) == "2"
    try:
        COIN.encode_roll(3)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_entropy_bits_property_matches_word_count():
    assert RollSession(D6, 12).entropy_bits == 128
    assert RollSession(D6, 24).entropy_bits == 256


def test_roll_string_content_is_exact_concatenation():
    session = RollSession(D12, 12)
    session.add_roll(3)
    session.add_roll(12)
    session.add_roll(1)
    # zero-padded to 2 digits each, per D12.digit_width -- this is exactly
    # what prevents "3","12" and "31","2" style ambiguity on parse
    assert session.roll_string() == "031201"
