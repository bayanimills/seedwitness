# Entropy sanity checks

SeedWitness checks whether the entered roll sequence contains an obvious
pattern or distribution anomaly before deriving a mnemonic. A concern opens a
**Check Your Rolls** screen with two hold controls: **Use These Rolls** keeps
the user's ceremony, while **Start Over** discards the entered rolls.

The checks live in `device/seedwitness/entropy.py` and are exercised by
`tests/test_entropy_quality.py`; their ceremony integration is exercised by
`tests/test_entropy_warning_flow.py`. The analysis is pure logic: no I/O, no
printing and no persistent state, and it runs identically on desktop CPython
and on the device.

## What the problem actually is

The extractor turns rolls into a seed by hashing them. SHA-256 whitens a
constant input exactly as convincingly as a random one, so a user who taps "1"
fifty times gets a valid 12-word mnemonic, a plausible-looking digest, and a
seed that anybody can reproduce in a second. The required roll count is
calculated from the source's face count. The advisory checks inspect the values
for obvious problems, but they cannot establish the sequence's min-entropy.

These checks look at the **input** to the extractor. They say nothing about its
output, which is uniform-looking either way and therefore useless as evidence.

## The two rules

**1. Warn, never block.** A fair d6 can legitimately roll 1,2,3,4,5,6 in order.
The ceremony belongs to the user. `assess_rolls` returns an `EntropyConcern`
object or `None`; the object carries a sentence to show and a small dict of
numbers, and deliberately has no severity, fatal or blocking field, because
there is no failure this device is entitled to declare on the user's behalf. A
caller shows the message with an explicit confirmation and keeps an advisory
**Use These Rolls** path available.

**2. No false alarms.** A warning that fires on honest dice is worse than no
warning, because it teaches the user that warnings are noise. Every threshold
below is calibrated by simulation, and bounds on the measured rate are asserted
in the test suite so threshold changes cannot silently make warnings noisy.

## The checks

Run in this order; the first one that fires is the one returned, because five
caveats about the same 50 rolls on a 240x240 screen is a list nobody reads.

| Code | Catches | Threshold |
|---|---|---|
| `ALL_SAME` | every roll the same value | exact, once a repeat that long is rarer than 1 in 10,000 (7 rolls of a d6, 15 flips of a coin) |
| `CYCLE` | a short repeating pattern: 1212.., 123123.. | period 1..8, at least 4 repetitions, tolerating one mismatch in ten so a single typo does not hide the pattern |
| `RUN` | counting up or down instead of rolling, including across the face boundary (5,6,1,2..) | run long enough that a fair source produces one less than about once in 10,000 ceremonies: 9 for a d6, 7 for a d12, 22 for a coin |
| `MISSING_FACE` | a face that never appeared | only once absence is implausible: expected number of unseen faces below 0.002 |
| `NON_UNIFORM` | a grossly lopsided distribution | chi-square goodness of fit at p=0.001, and only when at least 5 rolls per face are expected |

`ALL_SAME` is exact; a sequence that is *nearly* all one value falls through to
`CYCLE` with period 1 and is still flagged.

All arithmetic is integer. The chi-square test is computed as
`sum((sides*observed - n)**2)` compared against a critical value tabulated at
scale 100, so there is no float anywhere and every intermediate stays inside
MicroPython's 31-bit small-int range for any ceremony this device runs.

### Where a check deliberately stays silent

The thresholds mean some checks do not apply to some ceremonies. That is the
design, not a gap:

* **Missing face** needs 44 rolls (d6), 63 (d8), 100 (d12) or 10 (coin). So it
  never applies to a d12 ceremony: 36 rolls of a d12 leave some face unseen
  about half the time, and 72 rolls still leave one unseen far too often to be
  worth mentioning. Warning there would fire on every other honest ceremony.
* **Uniformity** needs 5 expected rolls per face, so it does not apply to a
  d12 12-word ceremony (36 rolls, 3 expected per face). Below that the
  chi-square approximation stops being conservative and starts inventing
  warnings.
* Nothing fires on very short sequences at all. Three identical d6 rolls is a
  1-in-36 event and saying so would be pure noise.

## Measured false-positive rate

Simulated fair sequences, seeded RNG, all four sources at both ceremony
lengths. Reproduced by `tests/test_entropy_quality.py` (5,000 trials per
configuration, seed 20260803); the wider figures below come from a 20,000-trial
run of the same code.

| Source | Words | Rolls | 20,000-trial FP rate |
|---|---|---|---|
| Coin | 12 | 128 | 0.12% |
| Coin | 24 | 256 | 0.08% |
| D6 | 12 | 50 | 0.22% |
| D6 | 24 | 100 | 0.10% |
| D8 | 12 | 43 | 0.11% |
| D8 | 24 | 86 | 0.14% |
| D12 | 12 | 36 | 0.01% |
| D12 | 24 | 72 | 0.14% |

**Overall: 0.11% (179 of 160,000).** Worst single configuration: 0.22%, d6
12-word. Almost all of it is the chi-square check firing at its nominal p=0.001
rate, which is what that number means and is the price of having the check at
all.

Put plainly: roll a d6 fifty times, honestly, every day, and you would expect
to see one of these warnings about once every 450 ceremonies.

The test suite asserts under 1% for any single configuration and under 0.5%
overall, with headroom above the measured values so the test fails on a real
regression rather than on simulation noise.

The D6 24-word simulation above uses the 100-roll full-input path. The
SeedSigner-compatible path stops at 99 rolls and runs the same checks before
the compatibility gate can finalise the mnemonic.

## What these checks cannot do

This matters more than the list above, and should be said to the user rather
than hidden in a repo.

* **They do not detect a loaded die.** A die shaved to favour one face still
  produces a sequence that passes every check here. The chi-square test would
  need far more rolls than a ceremony has to notice a small bias, and the whole
  point of the SHA-256 extractor is that a *slightly* biased source is still
  fine. A *badly* biased one is not, and this device cannot see it.
* **They do not detect a die that was never rolled.** Numbers copied off a
  page, out of a book, from a phone, or from any other sequence someone else
  also knows will look perfectly random and pass everything here. The attacker
  who knows where the numbers came from does not care that they are uniform.
* **They do not detect a human choosing "random" values.** People are bad at
  this in specific, well-studied ways: they avoid repeats and favour the middle
  of a range. Those habits are not caught here, and a hand-chosen sequence has
  drastically less entropy than its face count implies.
* **They do not verify the count.** Fifty entries is fifty entries whether or
  not fifty physical rolls happened.
* **A clean result is not a certificate.** `None` means "nothing obviously
  wrong was visible", which is a much weaker statement than "this entropy is
  good", and the code says so in the docstring.

What they do catch is accident and obvious laziness: the button held down, the
pattern typed to get through the screen faster, the ceremony finished in a
hurry. That is a real failure mode with a real cost, and it is worth catching.
It is not a substitute for the user actually rolling fairly, which remains the
single assumption the entire seed rests on. That is why every source's on-screen
prompt says the word "fair", and why these warnings never refuse: the user is
the only party here who can see the dice.
