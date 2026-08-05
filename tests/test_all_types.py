"""One derivation, all four address types.

Choosing BIP44/49/84/86 BEFORE the ten-minute PBKDF2 stretch meant a wrong
guess surfaced as a MISMATCH -- the most alarming possible misreading of a
correct backup. The All Types flow derives every template from the one cached
seed (the extra three cost only their EC derivation) and shows four labelled
rows, so "you must already know your address type" becomes "one of these four
will match", which anyone can answer by looking at their wallet.

Also pinned here: the home entry and About copy never describe the device as
generating/making a seed. The dice decide it; the device reads the result
out. That is the project's central security argument, and a relabel that
drifted back would quietly reverse it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import derive as d  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

M = ("abandon abandon abandon abandon abandon abandon abandon abandon "
     "abandon abandon abandon about")

# Published in the BIP texts themselves -- external reference values.
KNOWN_FIRST_ADDRESS = {
    44: "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA",
    49: "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf",
    84: "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    86: "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
}


def _tap(app, canvas, b):
    app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)


def _by_label(app, fragment):
    return next(b for b in app.screen.buttons
                if fragment.lower() in b.label.replace("\n", " ").lower())


def _run_to_all_addresses(app, canvas):
    app.push(S.DerivationPathScreen(M))
    _tap(app, canvas, _by_label(app, "All Types"))
    assert type(app.screen).__name__ == "AddressIndexScreen"
    assert app.screen.template is None
    _tap(app, canvas, app.screen.derive_button)
    assert type(app.screen).__name__ == "AllAddressesScreen"
    return app.screen


def test_path_menu_offers_all_types_and_keeps_the_per_type_entries():
    labels = [b.label for b in S.DerivationPathScreen(M).buttons]
    assert labels[0] == "All Types"
    for t in d.PATH_TEMPLATES:
        assert t.label in labels, "the direct per-type flow must survive"


def test_all_types_flow_shows_all_four_and_opens_the_full_view():
    d.clear_seed_cache()
    app, canvas = App(), MockCanvas()
    screen = _run_to_all_addresses(app, canvas)
    rows = [b for b in screen.buttons if "\n" in b.label]
    assert len(rows) == 4
    for bip, addr in KNOWN_FIRST_ADDRESS.items():
        row = next(b for b in rows if b.label.startswith("BIP%d " % bip))
        # the truncation on the row must come from the REAL address, so the
        # visible prefix is enough to recognise the wallet's own display
        assert addr[:10] in row.label and addr[-4:] in row.label
    # tapping a row opens the existing character-by-character comparison
    _tap(app, canvas, _by_label(app, "SegWit"))
    scr = app.screen
    assert type(scr).__name__ == "AddressDisplayScreen"
    assert scr.address == KNOWN_FIRST_ADDRESS[84]
    assert scr.path == "m/84h/0h/0h/0/0"
    # its pop returns to the list, and the button says so
    assert any(b.label == "Back" for b in scr.buttons)
    assert not any(b.label == "Change Index" for b in scr.buttons)
    _tap(app, canvas, _by_label(app, "Back"))
    assert app.screen is screen


def test_all_four_types_cost_one_pbkdf2_stretch(monkeypatch):
    """The economics that make the flow viable: the seed is stretched once
    (575s on the board) and the other three types ride the cache (~9s each).
    If this ever regresses to four stretches, the flow becomes ~40 minutes
    and nobody will use it."""
    d.clear_seed_cache()
    calls = []
    real = d.mn.mnemonic_to_seed

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(d.mn, "mnemonic_to_seed", counting)
    _run_to_all_addresses(App(), MockCanvas())
    assert len(calls) == 1, "all-types derived %d stretches, not 1" % len(calls)
    d.clear_seed_cache()


def test_per_type_flow_is_unchanged():
    d.clear_seed_cache()
    app, canvas = App(), MockCanvas()
    app.push(S.DerivationPathScreen(M))
    _tap(app, canvas, _by_label(app, "BIP84"))
    assert type(app.screen).__name__ == "AddressIndexScreen"
    assert app.screen.template.bip == 84
    _tap(app, canvas, app.screen.derive_button)
    scr = app.screen
    assert type(scr).__name__ == "AddressDisplayScreen"
    assert scr.address == KNOWN_FIRST_ADDRESS[84]
    assert any(b.label == "Change Index" for b in scr.buttons)
    d.clear_seed_cache()


def test_the_device_never_claims_to_generate_the_seed():
    """Labels and About copy: the dice decide the seed, the device reads it
    out. 'Generate' with the device as subject is the trust claim this
    project exists to avoid making."""
    home_labels = [b.label for b in S.HomeScreen().buttons]
    assert "Roll a Seed" in home_labels
    for label in home_labels:
        assert "generate" not in label.lower()
    assert "Generate?" not in S.AboutScreen.NAV_LABELS
    for title, paras in S.AboutScreen.PAGES:
        text = (title + " " + " ".join(paras)).lower()
        for verb in ("generate", "makes a", "creates a", "produces a"):
            assert verb not in text, "About page %r says the device %s seed" % (
                title, verb)


def test_no_flow_module_string_puts_the_device_in_the_generating_seat():
    """Every string literal in every flow module, swept for the verb family
    with the device as subject.

    The relabel pass ("roll, not generate") ran against the old monolithic
    screens.py -- "what this device generated" lived on the backup result
    screen then -- and the module split copied strings into files that were
    never re-checked as a set. Screens render whatever string is nearest, so
    the discipline has to be pinned at the source level, not per screen:
    a survivor carried into a flow file would quietly reverse the project's
    central security argument ("your dice decide the seed; this device runs
    a public calculation and reads out the words they come to").
    """
    import ast
    ui = ROOT / "device" / "seedwitness" / "ui"
    forbidden = (
        "device generate", "device generated", "device made", "device makes",
        "device create", "device created", "device creates",
        "device produce", "device produced", "device produces",
        "device gives you", "device gave you",
        "generated by this device", "made by this device",
        "created by this device", "produced by this device",
    )
    files = sorted(ui.glob("flow_*.py")) + [ui / "screens.py", ui / "app.py"]
    assert len(files) >= 7, "the sweep lost track of the UI modules"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            low = node.value.lower()
            for phrase in forbidden:
                assert phrase not in low, (
                    "%s:%d puts the device in the generating seat: %r"
                    % (path.name, node.lineno, node.value[:120]))
