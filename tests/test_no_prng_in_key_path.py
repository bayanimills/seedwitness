"""Nothing in this project may reach a PRNG-backed key generator.

Why this is a test and not a patch to embit
-------------------------------------------
The obvious fix was to make `embit.util.key.generate_privkey()` and
`embit.misc.secure_randint()` raise in the vendored copy, so a future caller
failed loudly. That was tried and reverted, for two reasons:

1. `generate_privkey()` is called by embit itself, at `util/key.py`'s
   `ECKey.generate()`. Neutering it changes the behaviour of an audited
   library along a path this project never takes, to guard against a caller
   that does not exist.

2. The crypto trust model keeps embit vendored as-is: the same library
   SeedSigner depends on, reviewable by diffing
   against upstream. Every deviation costs some of that, and this one bought
   nothing that a test cannot buy.

So embit stays byte-identical to upstream and the guard lives here, where it
belongs. Same protection, no divergence.

What the guard is actually for
------------------------------
This device supplies its entropy wholly, from dice, never in part. It has no
trustworthy RNG to fall back on and does not want one: MicroPython's `random`
is not a CSPRNG, and the ESP32 hardware RNG is only properly seeded once a
radio is active, which this project deliberately never does.

The August 2026 Coldcard vulnerability is what this class of bug looks like
when it ships: firmware 4.0.1 through 5.0.3 used a software PRNG in place of
the hardware TRNG during seed generation, producing roughly 40-bit seeds on
Mk3 against a 128-bit standard, for five years, with no way for an owner to
tell. A key generated here would fail the same way and just as quietly.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "device" / "seedwitness"

FORBIDDEN_CALLS = {"generate_privkey", "secure_randint", "urandom", "randrange",
                   "randint", "getrandbits", "choice", "shuffle"}
FORBIDDEN_IMPORTS = {"random", "urandom"}


def _app_sources():
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_app_module_imports_a_random_source():
    """embit vendors a `random` import for helpers this project never calls.
    That is embit's business. It must not become ours."""
    offenders = []
    for path in _app_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        offenders.append("%s imports %s" % (path.name, a.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                    offenders.append("%s imports from %s" % (path.name, node.module))
    assert not offenders, (
        "this device generates keys from dice only, and has no trustworthy "
        "RNG to fall back on: " + "; ".join(offenders))


def test_no_app_module_calls_a_random_generator():
    offenders = []
    for path in _app_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in FORBIDDEN_CALLS:
                offenders.append("%s:%d calls %s" % (path.name, node.lineno, name))
    assert not offenders, (
        "a PRNG-backed key or nonce would be silently weak on this hardware: "
        + "; ".join(offenders))


def test_embit_is_vendored_unmodified_in_the_rng_helpers():
    """A tripwire on the files this project was tempted to patch.

    Not a full upstream diff, which belongs in the build manifest. This only
    asserts the two RNG entry points still look like embit's, so a future
    edit to them is a deliberate act with a failing test attached rather than
    a quiet divergence in the crypto core.
    """
    key = (ROOT / "device" / "embit" / "util" / "key.py").read_text(encoding="utf-8")
    misc = (ROOT / "device" / "embit" / "misc.py").read_text(encoding="utf-8")
    assert "def generate_privkey():" in key
    assert "def secure_randint(" in misc
    for src, where in ((key, "key.py"), (misc, "misc.py")):
        assert "NotImplementedError(\"no RNG on this device" not in src, (
            "%s has been patched to disable embit's RNG helpers. That was "
            "tried and reverted: generate_privkey() is called by embit's own "
            "ECKey.generate(), and the vendoring policy keeps embit "
            "diffable against upstream. Guard it from this side instead."
            % where)


def test_the_seed_comes_from_dice_and_nothing_else():
    """The positive statement, so this file is not only a list of denials.

    RollSession.final_entropy hashes the recorded rolls and nothing else. No
    clock, no counter, no device identifier is mixed in, which is exactly what
    makes the result reproducible on unrelated software and is the property
    that would have exposed the Coldcard substitution.
    """
    sys.path.insert(0, str(ROOT / "device"))
    from seedwitness import entropy as ent

    a = ent.RollSession(ent.D6, 12)
    b = ent.RollSession(ent.D6, 12)
    rolls = [(i % 6) + 1 for i in range(ent.required_rolls(ent.D6, 12))]
    for r in rolls:
        a.add_roll(r)
        b.add_roll(r)
    assert a.final_entropy() == b.final_entropy()
    assert len(a.final_entropy()) == 16
