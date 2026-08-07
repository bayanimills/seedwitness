"""Every button that commits something must be held, not tapped.

The rule is written twice in this codebase. flow_generate.py's
EntropyCapturedScreen records that it and the two screens after it "were the
only amber commit buttons in the app that fired on a plain tap, which taught
that some amber buttons commit instantly, the wrong lesson on a device where
the roll grid's hold is load-bearing". flow_info.py gives even Diagnostics'
Acknowledge a dwell because it "changes persistent state".

Three later screens quietly violated it, and they were the three that matter
most: enrolling an account (the only action in the app that persists to
flash), deleting one (destructive and persisted), and arming a passphrase
(silently changes every derivation afterwards). All three drew amber, and all
three fired on a brush.

Nothing pinned any of them, so this does. It asserts the dwell exists rather
than a specific duration, so tuning stays free.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from seedwitness.ui import flow_accounts as FA  # noqa: E402
from seedwitness.ui import flow_address as FAD  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402

# (class, why a mis-hit matters)
MUST_HOLD = [
    (FAD.EnrolScreen, "persists an xpub to flash"),
    (FAD.PassphraseWordsScreen, "arms a passphrase for every later derivation"),
    (FA.AccountDeleteScreen, "destroys a stored record"),
]


@pytest.mark.parametrize("cls,why", MUST_HOLD, ids=[c.__name__ for c, _ in MUST_HOLD])
def test_commit_screens_require_a_hold(cls, why):
    assert cls.SWEEP_MS > 0, (
        "%s commits on a plain tap, but it %s. Give it a dwell "
        "(SWEEP_MS/SWEEP_STEPS) like every other committing screen."
        % (cls.__name__, why))
    assert cls.SWEEP_STEPS > 0, cls.__name__


@pytest.mark.parametrize("cls,why", MUST_HOLD, ids=[c.__name__ for c, _ in MUST_HOLD])
def test_the_dwell_is_actually_inherited_not_shadowed(cls, why):
    """Declared on the class itself, not merely picked up from Screen's
    default of 0, which is what the bug looked like."""
    assert "SWEEP_MS" in vars(cls), (
        "%s relies on an inherited SWEEP_MS; declare it explicitly so a base "
        "class change cannot silently remove the gate." % cls.__name__)


def test_the_base_default_is_still_no_hold():
    """Guards the guard. If Screen.SWEEP_MS became positive, every test above
    would pass without any screen declaring anything."""
    assert S.Screen.SWEEP_MS == 0
