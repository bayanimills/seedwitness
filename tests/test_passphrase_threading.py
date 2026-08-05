"""Standing guard: every UI derivation must thread the session passphrase.

This file exists because of a real, shipped defect class, not a hypothetical.
EnrolScreen once called account_xpub() without the session passphrase while
every address the user had just compared was derived WITH it: the device
showed MATCH, stored the account key of a wallet the user does not own, and
later reported confident mismatches against a perfectly good backup. One
missing keyword argument, no test, 442 passing tests said nothing.

test_enrol_passphrase.py pins that one call site. This file guards the CLASS:

* A structural check parses every UI source with `ast` and fails if ANY call
  to a seed-consuming engine function (derive_address, account_xpub,
  _seed_for, mnemonic_to_seed) does not visibly pass a passphrase argument.
  It fails for code that has never been run, which is exactly how the
  original bug survived. New call sites fail by default; a legitimately
  passphrase-free one must be added to a small, commented allow-list keyed
  on (file, enclosing function, callee), and stale allow-list entries fail
  too, so the list cannot silently absorb anything.

* Behavioural checks drive each user-reachable path that produces an address
  or an xpub (single-type derive, all-types derive, enrolment) with a
  non-empty session passphrase, and assert the result differs from the
  empty-passphrase run and equals the direct engine call with that
  passphrase. These catch a passphrase that is passed but ignored, which the
  structural check cannot see.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as drv  # noqa: E402
from seedwitness.ui import flow_address as FA  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

PKG_DIR = ROOT / "device" / "seedwitness"
UI_DIR = PKG_DIR / "ui"

# ---------------------------------------------------------------------------
# Part 1: the structural check
# ---------------------------------------------------------------------------

# Every engine function that turns a mnemonic into key material, mapped to the
# file that defines it. The passphrase parameter's position is read from that
# file's AST rather than hardcoded here, so a signature change breaks this
# test loudly instead of making the scan check the wrong argument slot -- and
# a rename or removal fails test_seed_consumers_still_exist below rather than
# silently shrinking the guard's coverage.
SEED_CONSUMERS = {
    "derive_address": PKG_DIR / "derive.py",
    "account_xpub": PKG_DIR / "derive.py",
    "_seed_for": PKG_DIR / "derive.py",
    "mnemonic_to_seed": PKG_DIR / "mnemonic.py",
}

# Call sites that are legitimately passphrase-free, keyed on
# (ui file name, enclosing "Class.method" or "<module>", callee name).
#
# THE DEFAULT IS FAILURE. Add an entry here only when a call site truly must
# not receive the session passphrase, say why in the comment, and expect the
# entry itself to be asserted against reality: an entry whose call site no
# longer exists (moved, renamed, fixed) fails
# test_allowlist_entries_still_match_a_real_call_site, so this list can never
# quietly accumulate dead weight or absorb a new call site added elsewhere.
#
# There are currently NO legitimate passphrase-free seed derivations in the
# UI. Think hard before adding one: the whole point of the session passphrase
# is that every derivation in one session describes the same wallet.
ALLOWED_PASSPHRASE_FREE = {
    # ("flow_example.py", "ExampleScreen._handler", "derive_address"):
    #     "reason this call site must not see the session passphrase",
}


def _ui_sources():
    files = sorted(p for p in UI_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, "no UI sources found under %s -- scan is vacuous" % UI_DIR
    return files


def _passphrase_index(func_name):
    """Zero-based positional index of the `passphrase` parameter of the named
    engine function, read from the defining file's AST."""
    path = SEED_CONSUMERS[func_name]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            names = [a.arg for a in node.args.args]
            if "passphrase" not in names:
                raise AssertionError(
                    "%s() in %s no longer has a `passphrase` parameter. If the "
                    "parameter was renamed, update this test; if it was removed, "
                    "the passphrase-threading guarantee itself is gone and that "
                    "needs a design decision, not a test edit." % (func_name, path.name))
            return names.index("passphrase")
    raise AssertionError(
        "%s() is no longer defined at module level in %s. Update SEED_CONSUMERS "
        "in this test so the structural guard keeps covering whatever replaced "
        "it -- do NOT just delete the entry." % (func_name, path.name))


class _CallCollector(ast.NodeVisitor):
    """Collects every call whose callee NAME matches a seed consumer,
    regardless of what it is called on (drv.derive_address, derive.account_xpub,
    a bare imported name, a re-export...). Matching by name alone can in
    principle over-match an unrelated local function with the same name; that
    is deliberate -- an over-match is reviewed once and allow-listed, an
    under-match is a silent hole."""

    def __init__(self, filename):
        self.filename = filename
        self.stack = []
        self.calls = []  # (enclosing, lineno, callee, Call node)

    def _walk_scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _walk_scoped
    visit_AsyncFunctionDef = _walk_scoped
    visit_ClassDef = _walk_scoped

    def visit_Call(self, node):
        f = node.func
        name = None
        if isinstance(f, ast.Attribute):
            name = f.attr
        elif isinstance(f, ast.Name):
            name = f.id
        if name in SEED_CONSUMERS:
            self.calls.append((".".join(self.stack) or "<module>",
                               node.lineno, name, node))
        self.generic_visit(node)


def _collect_ui_calls():
    out = []
    for path in _ui_sources():
        collector = _CallCollector(path.name)
        collector.visit(ast.parse(path.read_text(encoding="utf-8")))
        for enclosing, lineno, callee, node in collector.calls:
            out.append((path.name, enclosing, lineno, callee, node))
    return out


def _threading_problem(callee, node):
    """None if the call visibly threads a passphrase, else a short string
    saying what is wrong. Deliberately conservative: anything this static
    check cannot verify (a **kwargs splat, a *args splat reaching the slot,
    a hard-coded literal) counts as NOT threaded and must be allow-listed
    if it is genuinely fine."""
    idx = _passphrase_index(callee)
    kw = next((k for k in node.keywords if k.arg == "passphrase"), None)
    if kw is not None:
        if isinstance(kw.value, ast.Constant):
            return ("passes passphrase=%r, a hard-coded literal. The session "
                    "passphrase (app.passphrase) is what must be threaded; a "
                    "literal silences this guard while keeping the bug"
                    % (kw.value.value,))
        return None
    if any(k.arg is None for k in node.keywords):
        return ("passes **kwargs, so this check cannot see whether a "
                "passphrase is included. Pass passphrase=... explicitly")
    if any(isinstance(a, ast.Starred) for a in node.args):
        return ("passes *args, so this check cannot see whether a passphrase "
                "reaches the passphrase slot. Pass passphrase=... explicitly")
    if len(node.args) > idx:
        value = node.args[idx]
        if isinstance(value, ast.Constant):
            return ("passes a hard-coded literal %r in the passphrase slot. "
                    "The session passphrase (app.passphrase) is what must be "
                    "threaded" % (value.value,))
        return None
    return "does not pass a passphrase argument at all"


def test_seed_consumers_still_exist_and_take_a_passphrase():
    """Guards the guard: if an engine function is renamed, moved, or loses its
    passphrase parameter, the scan below would silently stop matching. This
    fails first, with instructions, so coverage cannot rot invisibly."""
    for func_name in SEED_CONSUMERS:
        assert _passphrase_index(func_name) >= 0


def test_every_ui_seed_derivation_threads_the_session_passphrase():
    """The structural gate. A new call to any seed-consuming function anywhere
    under ui/ that does not visibly pass a passphrase fails HERE, before the
    code has ever been executed by anything."""
    failures = []
    for filename, enclosing, lineno, callee, node in _collect_ui_calls():
        problem = _threading_problem(callee, node)
        if problem is None:
            continue
        if (filename, enclosing, callee) in ALLOWED_PASSPHRASE_FREE:
            continue
        failures.append(
            "%s:%d in %s: call to %s() %s.\n"
            "    Every derivation in a session must describe the SAME wallet "
            "as every other, so thread the session passphrase through this "
            "call -- typically passphrase=getattr(app, \"passphrase\", \"\") "
            "-- or, if this call site truly must be passphrase-free, add\n"
            "    (%r, %r, %r)\n"
            "    to ALLOWED_PASSPHRASE_FREE in tests/test_passphrase_threading.py "
            "with a comment saying why. (This is the exact defect class that "
            "once made EnrolScreen store the account key of a wallet the user "
            "does not own.)"
            % (filename, lineno, enclosing, callee, problem,
               filename, enclosing, callee))
    assert not failures, "\n\n".join(failures)


def test_allowlist_entries_still_match_a_real_call_site():
    """A stale allow-list is a hole waiting for a new call site to fall into:
    an entry for code that was fixed or deleted would silently excuse the next
    unthreaded call added to the same function. Every listed entry must
    correspond to at least one call site that is actually passphrase-free
    right now."""
    unthreaded = set()
    for filename, enclosing, lineno, callee, node in _collect_ui_calls():
        if _threading_problem(callee, node) is not None:
            unthreaded.add((filename, enclosing, callee))
    stale = [k for k in ALLOWED_PASSPHRASE_FREE if k not in unthreaded]
    assert not stale, (
        "ALLOWED_PASSPHRASE_FREE entries with no matching passphrase-free "
        "call site (fixed, moved, or renamed -- remove them): %r" % stale)


def test_the_scan_actually_finds_the_known_call_sites():
    """If the flows were refactored so the scan no longer sees the derivation
    calls (moved out of ui/, wrapped, renamed), every test above would pass
    vacuously. The UI genuinely must derive addresses and an account xpub
    somewhere, so their absence from the scan is a scan bug, not progress."""
    callees = [c[3] for c in _collect_ui_calls()]
    assert callees.count("derive_address") >= 2, (
        "expected the single-type and all-types paths to call derive_address; "
        "found %r. If the calls moved, extend this scan to cover their new "
        "home" % callees)
    assert "account_xpub" in callees, (
        "expected enrolment to call account_xpub; found %r. If the call "
        "moved, extend this scan to cover its new home" % callees)


# ---------------------------------------------------------------------------
# Part 2: the behavioural checks
# ---------------------------------------------------------------------------

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")
PHRASE = "correct horse battery staple"
T84 = [t for t in drv.PATH_TEMPLATES if t.bip == 84][0]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    drv.clear_seed_cache()
    yield
    drv.clear_seed_cache()


def _tap(app, canvas, button):
    app.handle_tap(canvas, button.x + button.w // 2, button.y + button.h // 2)


def _derive_single(passphrase):
    """User path: Address Type -> BIP84 -> Derive Address. Returns the address
    the comparison screen would show."""
    drv.clear_seed_cache()
    app, canvas = App(), MockCanvas()
    app.passphrase = passphrase
    app.last_mnemonic = M
    app.push(FA.DerivationPathScreen(M))
    _tap(app, canvas, next(b for b in app.screen.buttons if "BIP84" in b.label))
    assert type(app.screen).__name__ == "AddressIndexScreen"
    _tap(app, canvas, app.screen.derive_button)
    assert type(app.screen).__name__ == "AddressDisplayScreen"
    return app.screen.address


def _derive_all_types(passphrase):
    """User path: Address Type -> All Types -> Derive Address. Returns
    {bip: full_address} by tapping each row of the arrival list."""
    drv.clear_seed_cache()
    app, canvas = App(), MockCanvas()
    app.passphrase = passphrase
    app.last_mnemonic = M
    app.push(FA.DerivationPathScreen(M))
    _tap(app, canvas, next(b for b in app.screen.buttons if "All Types" in b.label))
    _tap(app, canvas, app.screen.derive_button)
    assert type(app.screen).__name__ == "AllAddressesScreen"
    rows = [b for b in app.screen.buttons if b.label.startswith("BIP")]
    assert len(rows) == 4
    out = {}
    for row in rows:
        bip = int(row.label.split()[0][3:])
        _tap(app, canvas, row)
        assert type(app.screen).__name__ == "AddressDisplayScreen"
        out[bip] = app.screen.address
        app.pop()  # back to the all-types list
    return out


def _enrol(passphrase):
    """User path: comparison screen -> Enrol Account -> Enrol. Returns the
    stored account records."""
    drv.clear_seed_cache()
    app, canvas = App(), MockCanvas()
    app.passphrase = passphrase
    app.last_mnemonic = M
    app.push(FA.EnrolScreen(M, T84))
    _tap(app, canvas, next(b for b in app.screen.buttons
                           if b.label == "Enrol Account"))
    return acct.load()


def test_single_type_derive_threads_the_passphrase():
    with_pp = _derive_single(PHRASE)
    without = _derive_single("")
    assert with_pp != without, (
        "the session passphrase did not change the derived address: it is "
        "not reaching the engine")
    _, engine = drv.derive_address(M, T84, index=0, passphrase=PHRASE)
    assert with_pp == engine
    _, engine_plain = drv.derive_address(M, T84, index=0, passphrase="")
    assert without == engine_plain


def test_all_types_derive_threads_the_passphrase_for_every_template():
    with_pp = _derive_all_types(PHRASE)
    without = _derive_all_types("")
    for t in drv.PATH_TEMPLATES:
        assert with_pp[t.bip] != without[t.bip], (
            "BIP%d: the session passphrase did not change the derived "
            "address on the all-types path" % t.bip)
        _, engine = drv.derive_address(M, t, index=0, passphrase=PHRASE)
        assert with_pp[t.bip] == engine, (
            "BIP%d: all-types path disagrees with the engine for the same "
            "passphrase" % t.bip)


def test_enrolment_threads_the_passphrase():
    """The original defect, re-asserted through the tap path: the stored
    account must be the passphrase wallet's, which is the wallet whose
    addresses the user just compared."""
    records = _enrol(PHRASE)
    assert len(records) == 1
    _, expected = drv.account_xpub(M, T84, passphrase=PHRASE)
    assert records[0]["xpub"] == expected
    _, wrong_wallet = drv.account_xpub(M, T84, passphrase="")
    assert records[0]["xpub"] != wrong_wallet, (
        "enrolment stored the no-passphrase wallet's account key: the exact "
        "shipped defect this file exists to prevent")


def test_the_two_wallets_really_are_different():
    """Guards the guards: if a passphrase did not change the account key at
    the engine level, every comparison above would pass against a broken
    implementation."""
    _, with_pp = drv.account_xpub(M, T84, passphrase=PHRASE)
    _, without = drv.account_xpub(M, T84, passphrase="")
    assert with_pp != without
