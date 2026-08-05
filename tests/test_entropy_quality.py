"""Advisory input sanity checks for the roll sequence.

Two halves, and the second one is the point of the exercise.

The easy half: obviously degenerate input gets flagged. All the same value, a
short repeating pattern, counting up or down, a face that never appeared, a
lopsided distribution.

The hard half: honest input does NOT get flagged. A warning that fires on
genuine dice is worse than no warning, because it teaches the user that
warnings are noise to tap through, and the next warning they tap through is a
real one. So the false-positive rate is measured here by simulation against a
seeded RNG (deterministic run to run) and asserted, not assumed.

Measured with SIM_SEED / SIM_TRIALS below, over all four sources at both
ceremony lengths: 0.11% overall, worst single configuration 0.22% (d6,
12-word, 50 rolls). Bounds asserted below are set well above those so the test
fails on a real regression, not on simulation noise.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

from seedwitness.entropy import (
    D6, D8, D12, COIN, RollSession, required_rolls,
    assess_rolls, check_all_same, check_cycle, check_run,
    check_missing_face, check_uniformity,
    CONCERN_ALL_SAME, CONCERN_CYCLE, CONCERN_RUN,
    CONCERN_MISSING_FACE, CONCERN_NON_UNIFORM, CONCERN_CODES,
)

ALL_SOURCES = (COIN, D6, D8, D12)

# Deterministic simulation. A flaky calibration test is a test people delete.
SIM_SEED = 20260803
SIM_TRIALS = 5000

# Ceiling for any single source/word-count configuration. The brief asks for
# well under 1%; measured worst case is 0.22%.
MAX_FALSE_POSITIVE_RATE = 0.01
# Ceiling across every configuration together; measured 0.11%.
MAX_OVERALL_FALSE_POSITIVE_RATE = 0.005


def random_rolls(rng, source, count):
    return [rng.randrange(1, source.sides + 1) for _ in range(count)]


# --------------------------------------------------------------------------
# Known-bad input is flagged
# --------------------------------------------------------------------------

def test_all_identical_rolls_are_flagged_for_every_source():
    # The headline failure: tap one button until the progress bar fills.
    for source in ALL_SOURCES:
        for word_count in (12, 24):
            rolls = [1] * required_rolls(source, word_count)
            concern = assess_rolls(source, rolls)
            assert concern is not None, "%s/%dw all-ones not flagged" % (source.name, word_count)
            assert concern.code == CONCERN_ALL_SAME
            assert concern.detail["value"] == 1


def test_all_identical_message_uses_the_face_label():
    concern = assess_rolls(COIN, [2] * 128)
    assert concern.code == CONCERN_ALL_SAME
    assert "Tails" in concern.message  # not "2"
    assert "flips" in concern.message  # not "rolls"


def test_nearly_all_identical_still_flagged_as_a_period_one_cycle():
    # A single mistyped entry must not buy a free pass out of the check.
    rolls = [4] * 50
    rolls[7] = 2
    rolls[31] = 6
    concern = assess_rolls(D6, rolls)
    assert concern is not None
    assert concern.code == CONCERN_CYCLE
    assert concern.detail["period"] == 1


def test_alternating_coin_flips_are_flagged_as_a_two_cycle():
    rolls = [1 + (i % 2) for i in range(128)]
    concern = assess_rolls(COIN, rolls)
    assert concern is not None
    assert concern.code == CONCERN_CYCLE
    assert concern.detail["period"] == 2


def test_three_value_cycle_is_flagged_with_its_shortest_period():
    rolls = [1 + (i % 3) for i in range(50)]  # 123123..
    concern = assess_rolls(D6, rolls)
    assert concern is not None
    assert concern.code == CONCERN_CYCLE
    assert concern.detail["period"] == 3


def test_cycle_survives_a_few_typos():
    rolls = [1 + (i % 3) for i in range(50)]
    rolls[11] = 6
    rolls[40] = 5
    concern = check_cycle(D6, rolls)
    assert concern is not None
    assert concern.detail["period"] == 3
    # Each typo breaks two comparisons (its own, and the one that looks back
    # at it), so two slips cost four mismatches out of the 4 allowed here.
    assert concern.detail["mismatches"] == 4
    assert concern.detail["allowed"] == 4


def test_full_length_ascending_sequence_is_flagged():
    # 1,2,3,4,5,6,1,2,... is both a run and a 6-cycle. Either code is a correct
    # description; what matters is that it does not pass silently.
    rolls = [1 + (i % 6) for i in range(50)]
    concern = assess_rolls(D6, rolls)
    assert concern is not None
    assert concern.code in (CONCERN_CYCLE, CONCERN_RUN)


def test_ascending_run_inside_an_otherwise_random_sequence():
    rng = random.Random(11)
    rolls = random_rolls(rng, D6, 38)
    rolls += [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6]  # 12 in a row, wrapping
    concern = check_run(D6, rolls)
    assert concern is not None
    assert concern.code == CONCERN_RUN
    assert concern.detail["direction"] == "up"
    assert concern.detail["length"] >= 12
    assert assess_rolls(D6, rolls).code == CONCERN_RUN


def test_descending_run_is_flagged_too():
    rng = random.Random(12)
    rolls = random_rolls(rng, D12, 24)
    rolls += [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    concern = check_run(D12, rolls)
    assert concern is not None
    assert concern.detail["direction"] == "down"


def test_run_detection_wraps_around_the_face_boundary():
    # Counting 5,6,1,2,3 off a d6 is the same habit as counting 1,2,3,4,5; it
    # must not escape by crossing the boundary.
    rng = random.Random(13)
    rolls = random_rolls(rng, D6, 38)
    rolls += [5, 6, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4]
    assert check_run(D6, rolls) is not None


def test_missing_face_is_flagged_once_the_ceremony_is_long_enough():
    rng = random.Random(21)
    # 50 d6 rolls that never once show a 4.
    rolls = [rng.choice([1, 2, 3, 5, 6]) for _ in range(50)]
    concern = assess_rolls(D6, rolls)
    assert concern is not None
    assert concern.code == CONCERN_MISSING_FACE
    assert concern.detail["missing"] == [4]
    assert "4" in concern.message


def test_missing_face_message_uses_face_labels_for_a_coin():
    rolls = [1] * 60 + [1] * 68  # no tails at all, but see: all-same wins first
    concern = check_missing_face(COIN, rolls)
    assert concern is not None
    assert "Tails" in concern.message


def test_lopsided_distribution_is_flagged_by_the_uniformity_check():
    rng = random.Random(31)
    # Every face appears, so this is not the missing-face case; face 1 just
    # dominates. Roughly 30 ones in 50 rolls of a d6.
    rolls = []
    for _ in range(50):
        if rng.randrange(5) < 3:
            rolls.append(1)
        else:
            rolls.append(rng.randrange(2, 7))
    assert 0 not in [rolls.count(f) for f in range(1, 7)]
    concern = assess_rolls(D6, rolls)
    assert concern is not None
    assert concern.code == CONCERN_NON_UNIFORM
    assert concern.detail["chi2_x100"] > concern.detail["critical_x100"]


def test_a_seeded_bad_ceremony_flows_through_roll_session():
    session = RollSession(D6, 12)
    for _ in range(session.target_rolls):
        session.add_roll(3)
    assert session.is_complete
    concern = session.assess()
    assert concern is not None and concern.code == CONCERN_ALL_SAME


# --------------------------------------------------------------------------
# The advisory contract
# --------------------------------------------------------------------------

def test_concern_is_advisory_and_carries_no_blocking_signal():
    concern = assess_rolls(D6, [1] * 50)
    # Deliberately no severity/fatal/blocking field: this module is not
    # entitled to declare a failure on the user's behalf.
    for forbidden in ("fatal", "blocking", "severity", "refuse", "abort"):
        assert not hasattr(concern, forbidden)
    assert concern.code in CONCERN_CODES
    assert isinstance(concern.message, str) and concern.message
    assert isinstance(concern.detail, dict)


def test_assessment_never_raises_for_a_degenerate_but_valid_ceremony():
    # A concern is returned, never thrown. Nothing downstream should have to
    # wrap this in a try block to keep the ceremony alive.
    for source in ALL_SOURCES:
        for word_count in (12, 24):
            count = required_rolls(source, word_count)
            for pattern in ([1] * count,
                            [1 + (i % 2) for i in range(count)],
                            [1 + (i % source.sides) for i in range(count)]):
                assert assess_rolls(source, pattern) is not None
    # And the session itself still produces entropy regardless of the warning.
    session = RollSession(D6, 12)
    for _ in range(session.target_rolls):
        session.add_roll(1)
    assert len(session.final_entropy()) == 16


def test_assessment_is_pure():
    rolls = [1 + (i % 3) for i in range(50)]
    before = list(rolls)
    assess_rolls(D6, rolls)
    assess_rolls(D6, rolls)
    assert rolls == before


def test_empty_and_short_sequences_say_nothing():
    assert assess_rolls(D6, []) is None
    # Three identical rolls of a d6 is a 1-in-36 event. Warning about it would
    # be noise, and noise is what trains people to ignore the real warning.
    assert assess_rolls(D6, [5, 5, 5]) is None
    assert assess_rolls(COIN, [1, 1, 1, 1, 1, 1]) is None


def test_missing_face_stays_silent_when_absence_proves_nothing():
    # 36 rolls of a d12 leave some face unseen about half the time. Flagging
    # that would fire on roughly every other honest 12-word d12 ceremony.
    rng = random.Random(41)
    rolls = [rng.choice([f for f in range(1, 13) if f != 7]) for _ in range(36)]
    assert check_missing_face(D12, rolls) is None
    # The same absence in a d6 ceremony of the same length does get mentioned.
    rolls6 = [rng.choice([1, 2, 3, 5, 6]) for _ in range(50)]
    assert check_missing_face(D6, rolls6) is not None


def test_out_of_range_values_raise_rather_than_warn():
    try:
        assess_rolls(D6, [1, 2, 9, 4])
        assert False, "expected ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------
# Honest input is NOT flagged: the measured false-positive rate
# --------------------------------------------------------------------------

def measure_false_positive_rate(source, word_count, trials=SIM_TRIALS, seed=SIM_SEED):
    """Fraction of fair simulated ceremonies that produce any concern at all."""
    rng = random.Random(seed + source.sides * 1000 + word_count)
    count = required_rolls(source, word_count)
    flagged = 0
    by_code = {}
    for _ in range(trials):
        concern = assess_rolls(source, random_rolls(rng, source, count))
        if concern is not None:
            flagged += 1
            by_code[concern.code] = by_code.get(concern.code, 0) + 1
    return flagged, by_code


def test_false_positive_rate_per_configuration_is_well_under_one_percent():
    worst = 0.0
    for source in ALL_SOURCES:
        for word_count in (12, 24):
            flagged, by_code = measure_false_positive_rate(source, word_count)
            rate = float(flagged) / SIM_TRIALS
            worst = max(worst, rate)
            assert rate < MAX_FALSE_POSITIVE_RATE, (
                "%s/%dw false positives %d/%d = %.3f%% %s"
                % (source.name, word_count, flagged, SIM_TRIALS, rate * 100, by_code)
            )
    # Documented in docs/ENTROPY_CHECKS.md; keep the two in step.
    assert worst < 0.005


def test_overall_false_positive_rate_across_all_configurations():
    flagged_total = 0
    trials_total = 0
    for source in ALL_SOURCES:
        for word_count in (12, 24):
            flagged, _ = measure_false_positive_rate(source, word_count)
            flagged_total += flagged
            trials_total += SIM_TRIALS
    rate = float(flagged_total) / trials_total
    assert rate < MAX_OVERALL_FALSE_POSITIVE_RATE, (
        "overall false positives %d/%d = %.3f%%" % (flagged_total, trials_total, rate * 100)
    )


def test_individual_checks_do_not_fire_on_fair_sequences():
    # Per-check breakdown, so a regression names the check that broke rather
    # than just moving the aggregate.
    rng = random.Random(SIM_SEED + 7)
    trials = 2000
    counts = {}
    for source in ALL_SOURCES:
        length = required_rolls(source, 12)
        for _ in range(trials):
            rolls = random_rolls(rng, source, length)
            for check in (check_all_same, check_cycle, check_run,
                          check_missing_face, check_uniformity):
                if check(source, rolls) is not None:
                    key = (source.name, check.__name__)
                    counts[key] = counts.get(key, 0) + 1
    for key, hits in counts.items():
        assert hits < trials * 0.01, "%s fired %d/%d times on fair input" % (key, hits, trials)
    # all-same and cycle are essentially impossible on fair input at ceremony
    # length; if either ever fires here, a threshold has been broken outright.
    for key in counts:
        assert key[1] not in ("check_all_same", "check_cycle"), "%s fired on fair input" % (key,)
