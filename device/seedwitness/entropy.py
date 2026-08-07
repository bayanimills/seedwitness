"""Physical-roll -> BIP39 entropy extraction.

Method matches SeedSigner's own dice algorithm (verified against their
docs/source): each roll is encoded as a fixed-width digit string, all rolls
for the ceremony are concatenated, and SHA256 is used as a randomness
extractor to whiten and compress the (possibly biased) physical source down
to exactly the required entropy length. Using a hash extractor means the
result is uniform even if the die/coin itself is imperfectly balanced, as
long as enough rolls (enough min-entropy) were collected.

12-word mnemonic -> 128 bits entropy -> first 16 bytes of SHA256(rolls)
24-word mnemonic -> 256 bits entropy -> full 32 bytes of SHA256(rolls)

Plain classes, not @dataclass: MicroPython has no dataclasses module
(confirmed: `import dataclasses` raises ImportError on a real MicroPython
1.22 interpreter). This module is imported unchanged on both desktop CPython
(tests/, sim/) and on-device MicroPython.
"""
import hashlib


class RollSource:
    def __init__(self, name, sides, action, faces=None, unit="Roll",
                 prompt="tap the value you rolled:", use_pips=False,
                 gerund="Rolling"):
        self.name = name
        self.sides = sides  # number of faces; coin = 2
        # A coin is flipped, not rolled, and lands on a side rather than
        # showing a value. Carrying the wording on the source keeps the screen
        # free of per-source special cases.
        self.unit = unit
        self.prompt = prompt
        # Spelled out rather than derived from `unit`, because English doubles
        # the consonant for Flip -> Flipping but not for Roll -> Rolling. A
        # rule clever enough to get both right is a rule that will get the
        # next source wrong.
        self.gerund = gerund
        # What the user is told to do, said in full on the entry screen. The
        # word "fair" is doing real work: this device cannot detect a biased
        # die or a trick coin, and the security of the whole seed rests on the
        # source actually being unbiased (see SECURITY.md). Saying it every
        # time is the only place that requirement is visible.
        self.action = action
        # Optional per-face names. A coin has sides, not numbers -- showing
        # "1" and "2" makes the user translate, and a translation done under
        # pressure 128 times is a place to make a mistake. The stored value is
        # still the number; only the label changes.
        self._faces = faces
        # Draw die faces as pips rather than digits. True only for the D6,
        # whose physical faces really are pips; a D12 is numbered, so pips
        # there would add the translation step this removes.
        self.use_pips = use_pips

    def face_label(self, value):
        if self._faces:
            return self._faces[value - 1]
        return str(value)

    @property
    def digit_width(self):
        """Zero-padded width needed so concatenated rolls are unambiguous to parse."""
        return len(str(self.sides))

    def encode_roll(self, value):
        if not (1 <= value <= self.sides):
            raise ValueError("%s roll must be 1..%d, got %d" % (self.name, self.sides, value))
        s = str(value)
        return "0" * (self.digit_width - len(s)) + s


D6 = RollSource("D6", 6, "Roll Fair D6", use_pips=True)
D8 = RollSource("D8", 8, "Roll Fair D8")
D12 = RollSource("D12", 12, "Roll Fair D12")
# 1=heads, 2=tails -- the encoding stays numeric so the hash input is
# unchanged; only what the user sees differs.
COIN = RollSource("Coin", 2, "Flip Fair Coin", faces=("Heads", "Tails"),
                  unit="Flip", prompt="tap the side it landed on:",
                  gerund="Flipping")

SOURCES = {"D6": D6, "D8": D8, "D12": D12, "COIN": COIN}

WORD_COUNT_TO_ENTROPY_BITS = {12: 128, 24: 256}
SEEDSIGNER_D6_24_ROLLS = 99


def required_rolls(source, word_count):
    """Smallest n such that source.sides**n >= 2**entropy_bits, i.e. enough
    rolls to cover the target entropy exactly. Pure integer math -- no
    float/log2 involved, so there's no precision-boundary case to worry
    about for any current or future dice source, however many sides."""
    entropy_bits = WORD_COUNT_TO_ENTROPY_BITS[word_count]
    target = 1 << entropy_bits  # 2**entropy_bits
    n = 0
    value = 1
    while value < target:
        value *= source.sides
        n += 1
    return n


class RollSession:
    """Accumulates rolls for one ceremony and derives final entropy on completion."""

    def __init__(self, source, word_count):
        self.source = source
        self.word_count = word_count
        self.rolls = []

    @property
    def target_rolls(self):
        return required_rolls(self.source, self.word_count)

    @property
    def entropy_bits(self):
        return WORD_COUNT_TO_ENTROPY_BITS[self.word_count]

    @property
    def is_complete(self):
        return len(self.rolls) >= self.target_rolls

    @property
    def is_seedsigner_compatible(self):
        """Exactly SeedSigner's 24-word D6 input boundary.

        SeedSigner hashes the ASCII string of 99 D6 faces. This deliberately
        narrow predicate must not make any other short ceremony finalisable.
        """
        return (self.source is D6 and self.word_count == 24 and
                len(self.rolls) == SEEDSIGNER_D6_24_ROLLS)

    def add_roll(self, value):
        if self.is_complete:
            raise RuntimeError("roll session already has enough rolls")
        self.source.encode_roll(value)  # validates range
        self.rolls.append(value)

    def roll_string(self):
        """Concatenated digit encoding of every roll so far (deterministic, unambiguous)."""
        return "".join(self.source.encode_roll(v) for v in self.rolls)

    def live_preview_hash(self):
        """SHA256 of rolls-so-far, for the on-screen avalanche-effect visualization only.
        NOT the final entropy -- purely pedagogical, recomputed fresh on every roll so the
        user visibly sees the whole digest change from a single new roll."""
        digest = hashlib.sha256(self.roll_string().encode("ascii")).digest()
        return "".join("%02x" % b for b in digest)

    def final_entropy(self):
        if not self.is_complete:
            raise RuntimeError(
                "only %d/%d rolls collected" % (len(self.rolls), self.target_rolls)
            )
        digest = hashlib.sha256(self.roll_string().encode("ascii")).digest()
        return digest[: self.entropy_bits // 8]

    def seedsigner_entropy(self):
        """The exact 99-roll SeedSigner-compatible 24-word entropy."""
        if not self.is_seedsigner_compatible:
            raise RuntimeError("SeedSigner compatibility requires 99 D6 rolls for 24 words")
        return hashlib.sha256(self.roll_string().encode("ascii")).digest()

    def assess(self):
        """Advisory sanity check of the rolls entered so far. See assess_rolls."""
        return assess_rolls(self.source, self.rolls)


# ---------------------------------------------------------------------------
# Advisory input sanity checks (see docs/ENTROPY_CHECKS.md)
#
# Everything above treats min-entropy as a property of the source's face count
# alone. That assumption is only true if the user actually rolled. Tapping "1"
# fifty times produces a perfectly valid, perfectly predictable seed, and the
# extractor above cannot tell the difference: SHA256 whitens a constant input
# just as happily as a random one, which is exactly why the digest looks
# convincing either way. These checks look at the INPUT to the extractor, never
# at its output.
#
# Two rules govern the design.
#
# 1. Advisory only. A fair d6 can legitimately produce a sequence that looks
#    patterned, and the ceremony belongs to the user, not to the device. Every
#    result here is something to show alongside a Continue option. Nothing in
#    this module refuses, and nothing raises to signal a concern.
#
# 2. No false alarms. A warning that fires on honest dice is worse than no
#    warning at all, because it teaches the user to tap past warnings. Every
#    threshold below is set so a fair source trips it far less than once in a
#    hundred ceremonies, and the combined rate is measured by simulation in
#    tests/test_entropy_quality.py.
# ---------------------------------------------------------------------------

CONCERN_ALL_SAME = "ALL_SAME"
CONCERN_CYCLE = "CYCLE"
CONCERN_RUN = "RUN"
CONCERN_MISSING_FACE = "MISSING_FACE"
CONCERN_NON_UNIFORM = "NON_UNIFORM"

# Severity order, most alarming first. assess_rolls reports one concern only:
# the screen is 240x240 and a list of five caveats is a list nobody reads.
CONCERN_CODES = (
    CONCERN_ALL_SAME,
    CONCERN_CYCLE,
    CONCERN_RUN,
    CONCERN_MISSING_FACE,
    CONCERN_NON_UNIFORM,
)


class EntropyConcern:
    """One advisory observation about the rolls that were entered.

    Plain class, not @dataclass, for the same MicroPython reason as RollSource.

    A concern is never a verdict. It carries a short human sentence to show and
    a small detail dict for anything that wants the numbers (tests, logs, a
    future screen). Callers must present it with an explicit confirm and must
    keep the ceremony continuable; there is deliberately no severity field and
    no "fatal" flag, because there is no failure this module is entitled to
    declare on the user's behalf.
    """

    def __init__(self, code, message, detail=None):
        self.code = code
        self.message = message
        self.detail = detail if detail is not None else {}

    def __repr__(self):
        return "<EntropyConcern %s: %s>" % (self.code, self.message)


# Longest repeating pattern worth looking for. A user padding the ceremony by
# hand types 1212.. or 123123..; nobody hand-types a period-9 cycle, and every
# extra period costs a full pass over the rolls on a 240MHz chip.
MAX_CYCLE_PERIOD = 8

# A pattern is only a pattern once it has repeated a few times. Three
# repetitions of a 6-cycle in 18 rolls is weak evidence; four is not.
MIN_CYCLE_REPEATS = 4

# How much hand-entry slop the cycle check tolerates, as a divisor: one
# mismatch in ten. Without this, a user who taps 1,2,1,2.. fifty times and
# fumbles once escapes the check entirely, which is the opposite of useful.
CYCLE_MISMATCH_DIVISOR = 10

# Minimum rolls before the cycle check runs at all, by face count. The
# tolerance above is safe only once a random sequence cannot match a short
# period 90% of the time by luck, and the shorter the source the longer that
# takes: a coin already matches any period half the time by chance, a d6 only
# one time in six. Derived from a Hoeffding bound at 1e-5 per period, rounded
# up: 2 faces needs 36 comparisons, 3 needs 18, 4 needs 14, 6 or more needs 11
# (the 12 below). Every real ceremony is longer than this, so it only ever
# matters if something calls the check part-way through.
_CYCLE_MIN_ROLLS = {2: 36, 3: 18, 4: 14}
_CYCLE_MIN_ROLLS_DEFAULT = 12

# Chi-square upper-tail critical values at p=0.001, scaled by 100 so the test
# is pure integer arithmetic. Indexed by degrees of freedom (faces - 1). Only
# df 1..15 are tabulated; a source outside that range simply skips the check
# rather than guess, because a wrong critical value here means false alarms.
_CHI2_P001_X100 = {
    1: 1083, 2: 1382, 3: 1627, 4: 1847, 5: 2052,
    6: 2246, 7: 2433, 8: 2613, 9: 2788, 10: 2959,
    11: 3127, 12: 3291, 13: 3453, 14: 3613, 15: 3770,
}

# Classical rule for the chi-square approximation to mean anything: at least
# five expected observations per face. Below that the test is not conservative
# and would start inventing warnings, so it stays silent instead.
_CHI2_MIN_EXPECTED_PER_FACE = 5


def _plural_unit(source):
    """Lower-case plural of the source's unit: rolls, or flips for a coin.
    The messages here are sentences shown to a person, not UI labels."""
    return source.unit.lower() + "s"


def plural_unit_title(source):
    """Title-case plural for UI labels: "Rolls", or "Flips" for a coin.

    Sliced by hand rather than with str.capitalize(), which DOES NOT EXIST in
    MicroPython. This is not a style preference: three screens called
    .capitalize() and every one of them raised AttributeError on the board
    while all 624 desktop tests passed, because CPython has the method. The
    board caught it, the suite could not, which is the exact gap PLAN.md
    section 7 and SECURITY.md R5 describe.
    """
    unit = _plural_unit(source)
    return unit[:1].upper() + unit[1:]


def _face_counts(source, rolls):
    counts = [0] * source.sides
    for value in rolls:
        if not (1 <= value <= source.sides):
            raise ValueError("%s roll must be 1..%d, got %d" % (source.name, source.sides, value))
        counts[value - 1] += 1
    return counts


def _min_rolls_for_repeat(sides, odds):
    """Smallest n where sides**(n-1) >= odds, i.e. where a run of n identical
    faces is rarer than 1 in `odds`. Integer loop, no logarithms, no floats."""
    n = 1
    reach = 1
    while reach < odds:
        reach *= sides
        n += 1
    return n


def _min_rolls_for_cycle(sides):
    return _CYCLE_MIN_ROLLS.get(sides, _CYCLE_MIN_ROLLS_DEFAULT)


def _min_run_length(sides, count):
    """Shortest step-by-one run worth mentioning, given how many rolls there
    are to hide it in. Set so a fair source produces one less than about once
    in 10,000 ceremonies: a longer sequence gets more chances, so it needs a
    longer run, and a coin (which "steps by one" half the time) needs a much
    longer one than a d12."""
    length = 2
    reach = sides  # sides ** (length - 1)
    while reach < count * 10000 and length < 64:
        reach *= sides
        length += 1
    return length


def _min_rolls_for_missing_face(sides):
    """Smallest roll count at which a face that never appeared is worth
    mentioning: the point where a fair source leaves some face unseen in fewer
    than about 1 ceremony in 500.

    Fixed-point integers at scale 10**7, not floats. Every intermediate stays
    under 2**31 so MicroPython never promotes to a heap integer, and the result
    does not depend on a float implementation being present at all.

    This is why the check is silent for a d12 12-word ceremony: 36 rolls of a
    d12 leave a face unseen about half the time, so absence there means nothing
    and saying so would be pure noise."""
    scale = 10000000
    unseen = scale  # P(one specific face still unseen), scaled
    count = 0
    # sides * unseen is the expected number of unseen faces; 20000 == 0.002.
    while sides * unseen > 20000 and count < 1000:
        unseen = unseen * (sides - 1) // sides
        count += 1
    return count


def check_all_same(source, rolls):
    """Every roll the same value. The headline failure: it is what tapping one
    button repeatedly produces, and it yields a seed anyone can reproduce."""
    count = len(rolls)
    if count < _min_rolls_for_repeat(source.sides, 10000):
        return None
    first = rolls[0]
    for value in rolls:
        if value != first:
            return None
    return EntropyConcern(
        CONCERN_ALL_SAME,
        "All %d %s are %s." % (count, _plural_unit(source), source.face_label(first)),
        {"value": first, "count": count},
    )


def check_cycle(source, rolls):
    """A short pattern repeating: 121212.., 123123.., or near enough that a
    single mistyped entry does not hide it. Reports the shortest period that
    fits, since a period-6 match is usually a period-2 pattern seen twice."""
    count = len(rolls)
    if count < _min_rolls_for_cycle(source.sides):
        return None
    max_period = MAX_CYCLE_PERIOD
    if max_period > count // MIN_CYCLE_REPEATS:
        max_period = count // MIN_CYCLE_REPEATS
    for period in range(1, max_period + 1):
        compared = count - period
        allowed = compared // CYCLE_MISMATCH_DIVISOR
        mismatches = 0
        for i in range(period, count):
            if rolls[i] != rolls[i - period]:
                mismatches += 1
                if mismatches > allowed:
                    break
        if mismatches <= allowed:
            unit = _plural_unit(source)
            if period == 1:
                message = "Nearly every one of the %d %s is the same value." % (count, unit)
            else:
                message = "The %s repeat a %d-value pattern." % (unit, period)
            return EntropyConcern(
                CONCERN_CYCLE, message,
                {"period": period, "mismatches": mismatches, "allowed": allowed},
            )
    return None


def _longest_step_run(sides, rolls, step):
    """Longest stretch where each value is the previous one plus `step`,
    wrapping at the ends. Wrapping matters: 5,6,1,2 off a d6 is the same
    counting habit as 1,2,3,4 and should not escape by crossing the boundary."""
    longest = 1
    current = 1
    for i in range(1, len(rolls)):
        expected = rolls[i - 1] + step
        if expected > sides:
            expected = 1
        elif expected < 1:
            expected = sides
        if rolls[i] == expected:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 1
    return longest


def check_run(source, rolls):
    """A strict ascending or descending run: counting up (or down) instead of
    rolling. On a two-face source stepping by one is just alternating, so the
    threshold there is much longer and the wording says alternating."""
    count = len(rolls)
    threshold = _min_run_length(source.sides, count)
    if count < threshold:
        return None
    up = _longest_step_run(source.sides, rolls, 1)
    down = _longest_step_run(source.sides, rolls, -1)
    if up >= down:
        longest, direction = up, "up"
    else:
        longest, direction = down, "down"
    if longest < threshold:
        return None
    unit = _plural_unit(source)
    if source.sides == 2:
        # For a coin, +1 and -1 are the same move, and "up" would be nonsense.
        message = "%d %s in a row simply alternated." % (longest, unit)
    elif direction == "up":
        message = "%d %s in a row each went up by one." % (longest, unit)
    else:
        message = "%d %s in a row each went down by one." % (longest, unit)
    return EntropyConcern(
        CONCERN_RUN, message,
        {"length": longest, "direction": direction, "threshold": threshold},
    )


def check_missing_face(source, rolls):
    """A face that never appeared, once enough rolls have been collected that
    its absence is implausible. Silent before that point by design: an unseen
    face early in a ceremony is ordinary, and saying so would be the kind of
    warning that trains people to ignore warnings."""
    count = len(rolls)
    if count < _min_rolls_for_missing_face(source.sides):
        return None
    counts = _face_counts(source, rolls)
    missing = []
    for face in range(1, source.sides + 1):
        if counts[face - 1] == 0:
            missing.append(face)
    if not missing:
        return None
    unit = _plural_unit(source)
    if len(missing) == 1:
        message = "%s never came up in %d %s." % (source.face_label(missing[0]), count, unit)
    else:
        message = "%d values never came up in %d %s." % (len(missing), count, unit)
    return EntropyConcern(
        CONCERN_MISSING_FACE, message,
        {"missing": missing, "count": count},
    )


def check_uniformity(source, rolls):
    """Chi-square goodness of fit against a flat distribution, at p=0.001.

    Integer arithmetic throughout. With expected count e = n/sides,

        X2 = sum((o_i - e)**2) / e  ==  sum((sides*o_i - n)**2) / (n*sides)

    so the whole test is one integer sum compared against a tabulated critical
    value scaled by 100. Every intermediate stays well inside 2**31 for any
    ceremony this device runs.

    This is the weakest of the checks on purpose. It catches a source that is
    grossly lopsided, not a die shaved by a percent or two, and it says "worth
    a look", never "this is wrong"."""
    count = len(rolls)
    sides = source.sides
    if count < _CHI2_MIN_EXPECTED_PER_FACE * sides:
        return None
    critical = _CHI2_P001_X100.get(sides - 1)
    if critical is None:
        # Not "skip the check". Adding a source whose degrees of freedom are
        # missing from the table would silently drop one of the five checks
        # and leave the other four looking like full coverage. A missing
        # constant is a programmer error, same as an out-of-range roll above,
        # so it is raised rather than absorbed.
        raise ValueError(
            "no chi-square critical value for a %d-sided source; add it to "
            "_CHI2_P001_X100 rather than shipping a source with this check "
            "silently disabled" % sides)
    counts = _face_counts(source, rolls)
    total = 0
    for observed in counts:
        deviation = sides * observed - count
        total += deviation * deviation
    if total * 100 <= critical * count * sides:
        return None
    return EntropyConcern(
        CONCERN_NON_UNIFORM,
        "Some values came up far more often than others.",
        # chi-square scaled by 100, so 2137 means X2 = 21.37.
        {"chi2_x100": total * 100 // (count * sides),
         "critical_x100": critical, "counts": counts},
    )


_CHECKS = (check_all_same, check_cycle, check_run, check_missing_face, check_uniformity)


def assess_rolls(source, rolls):
    """Look at the rolls a user entered and return an EntropyConcern worth
    showing them, or None if there is nothing to say.

    Pure and side-effect free: no I/O, no printing, no mutation of `rolls`.

    ADVISORY ONLY. A None result is not a certificate that the entropy is good,
    and a concern is not a reason to refuse. This device cannot see the physical
    world: it cannot tell a fair d6 from a loaded one, and it cannot tell a user
    who rolled honestly from one who read numbers off a page. All it can catch
    is the input that no physical process would plausibly have produced. The
    caller shows the message, offers an explicit confirm, and lets the user
    decide, because a fair die really can roll 1,2,3,4,5,6 in order.

    Only the first concern in severity order is returned; the rest would be
    saying the same thing about the same rolls.

    Raises ValueError if any value is outside 1..source.sides. That is a caller
    bug, not a user one -- RollSession.add_roll already rejects those -- so it
    is signalled the same way encode_roll signals it.
    """
    if not rolls:
        return None
    # One validating pass up front, so a bad value fails the same way whichever
    # check would have reached it first. The counts are cheap enough to
    # recompute inside the two checks that need them (a few hundred small ints)
    # and keeping every check self-contained makes them testable in isolation.
    _face_counts(source, rolls)
    for check in _CHECKS:
        concern = check(source, rolls)
        if concern is not None:
            return concern
    return None
