"""Regression tests for the CYD's PBKDF2 heap headroom.

The field diagnostic ``MemoryError _sha512.py:55`` was caused by loading the
embit EC chain before the pure-Python SHA-512 stretch.  Those modules consume
tens of kilobytes on the ESP32; with them resident PBKDF2 entered with about
14 KiB free and failed while expanding SHA-512's message schedule.  The fix is
an ordering constraint, not different crypto: stretch first, collect, then
load the EC modules.

Run each public seed path in a fresh interpreter.  Test-suite import order can
otherwise leave embit modules resident and make a load-on-demand regression
invisible.  The fake stretch asserts the EC chain is absent at the exact point
PBKDF2 would begin; real EC derivation then completes from a disposable seed,
proving the modules are still loaded when they are actually needed.
"""
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("operation", ["address", "account_xpub"])
def test_pbkdf2_runs_before_the_embit_ec_chain_is_loaded(operation):
    code = r'''
import sys
sys.path.insert(0, r"%s")
from seedwitness import derive

heavy = ("embit.bip32", "embit.script", "embit.ec", "secp256k1")
assert not [name for name in heavy if name in sys.modules]

def disposable_stretch(mnemonic, passphrase="", progress=None):
    loaded_too_early = [name for name in heavy if name in sys.modules]
    assert not loaded_too_early, "EC loaded before PBKDF2: %%r" %% loaded_too_early
    return bytes(range(64))

derive.mn.mnemonic_to_seed = disposable_stretch
if %r == "address":
    path, value = derive.derive_address("disposable", derive.PATH_TEMPLATES[2])
    assert path == "m/84h/0h/0h/0/0"
    assert value.startswith("bc1q")
else:
    path, value = derive.account_xpub("disposable", derive.PATH_TEMPLATES[2])
    assert path == "m/84h/0h/0h"
    assert value.startswith("xpub")

assert "embit.bip32" in sys.modules
''' % (str(ROOT / "device"), operation)
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
