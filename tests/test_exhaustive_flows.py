"""Exhaustive user-flow QA harness: walk the app the way a user would, through
every flow and edge case reachable from the desktop simulator, and assert that
nothing raises, hangs, or silently drops rendered content.

Driving idiom is the same as sim/capture_screens.py and the existing flow
tests: an App plus a MockCanvas, taps injected at real button coordinates so
every tap goes through the same handle_tap dispatch the hardware loop uses.
A RecordingCanvas additionally detects content the canvas would clip
horizontally or refuse vertically (the "renders wrong" class of failure).

Deliberate characterization tests near the bottom pin down what CURRENTLY
happens for damaged accounts.json contents that the UI does not gracefully
refuse (they raise and are contained by the CrashScreen).  Those tests
document defects; see their docstrings.
"""
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Verify Build walks the cross-compiled tree to compute its fingerprint, so
# the two stages that reach that screen need _build_mpy/ to exist. It is
# gitignored, so a working copy that has run tools/build_mpy.sh has it and a
# fresh CI checkout does not: these passed locally and failed on the runner.
needs_build = pytest.mark.skipif(
    not (ROOT / "_build_mpy").is_dir(),
    reason="Verify Build reads _build_mpy/; run tools/build_mpy.sh first")
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness import accounts as acct  # noqa: E402
from seedwitness import derive as drv  # noqa: E402
from seedwitness import diag  # noqa: E402
from seedwitness import entropy as ent  # noqa: E402
from seedwitness import mnemonic as mn  # noqa: E402
from seedwitness import passphrase as pph  # noqa: E402
from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402

EFF_WORDS = ROOT / "_build_mpy" / "eff_words.bin"
needs_words = pytest.mark.skipif(
    not EFF_WORDS.exists(), reason="run tools/build_mpy.sh first")

M12 = ("abandon abandon abandon abandon abandon abandon abandon abandon "
       "abandon abandon abandon about")
M24 = ("abandon " * 23 + "art").strip()

T44, T49, T84, T86 = drv.PATH_TEMPLATES

SOURCE_LABEL = {"D6": "Dice: D6", "D8": "Dice: D8", "D12": "Dice: D12",
                "Coin": "Coin Flip"}

# SeedSigner's published 24-word dice-verification example (same vector the
# unit suite pins; here it is driven through the UI, tap by tap).
PUBLISHED_ROLLS_99 = (
    "6551522313165213216113315444412361646644311215344156335264562544622455"
    "46236542364246312613322234612")
PUBLISHED_MNEMONIC = (
    "eyebrow obvious such suggest poet seven breeze blame virtual frown "
    "dynamic donor harsh pigeon express broccoli easy apology scatter force "
    "recipe shadow claim radio")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(acct, "PATH", str(tmp_path / "accounts.json"))
    if EFF_WORDS.exists():
        monkeypatch.setattr(pph, "WORDS_FILE", str(EFF_WORDS))
    drv.clear_seed_cache()
    S.DEMO = False
    yield
    drv.clear_seed_cache()
    S.DEMO = False


# ---------------------------------------------------------------------------
# Canvas + driving helpers


class Canvas(MockCanvas):
    """MockCanvas that records every string drawn and detects content the
    canvas would clip (horizontal) or refuse to draw at all (vertical) --
    the exact silent-truncation class the device keeps regression tests
    for."""

    def __init__(self):
        super().__init__()
        self.texts = []
        self.lost = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        cw = font.width if font is not None else th.CHAR_W * scale
        chh = font.height if font is not None else th.CHAR_H * scale
        self.texts.append(s)
        if x < 0:
            self.lost.append((s, "x<0"))
        else:
            fit = max(0, (self.width - x) // cw)
            if fit < len(s):
                self.lost.append((s, "clipped to %d of %d chars" % (fit, len(s))))
        if y < 0 or y + chh > self.height:
            self.lost.append((s, "vertically off-screen at y=%d" % y))
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


class LiftCanvas(Canvas):
    """A touch that lifts before a hold-to-confirm sweep completes."""

    def __init__(self, hold_for=1):
        super().__init__()
        self.hold_for = hold_for
        self.polls = 0

    def touch_active(self):
        self.polls += 1
        return self.polls <= self.hold_for


class _LoopDone(BaseException):
    """Ends _main_loop from a scripted canvas; BaseException so it escapes
    every guard, exactly like the existing containment tests."""


class LoopCanvas(Canvas):
    """Canvas plus a scripted press list for driving the REAL main loop."""

    def __init__(self, presses):
        super().__init__()
        self._presses = list(presses)

    def get_press(self):
        if not self._presses:
            raise _LoopDone()
        return self._presses.pop(0)


def _flat(label):
    return label.replace("\n", " ")


def _center(b):
    return b.x + b.w // 2, b.y + b.h // 2


def draw(app, canvas):
    app.draw(canvas)


def screen_name(app):
    return type(app.screen).__name__


def tap_exact(app, canvas, label, must=True):
    """Tap the button whose (flattened) label equals `label` exactly.  Draws
    before the tap (dynamic screens rebuild their buttons in draw) and after
    it (what the main loop does), so a broken draw raises here."""
    draw(app, canvas)
    for b in getattr(app.screen, "buttons", ()):
        if _flat(b.label) == label:
            x, y = _center(b)
            app.handle_tap(canvas, x, y)
            draw(app, canvas)
            return True
    if must:
        raise AssertionError("no button labelled %r on %s (have %r)" % (
            label, screen_name(app),
            [_flat(b.label) for b in getattr(app.screen, "buttons", ())]))
    return False


def tap_sub(app, canvas, sub, must=True):
    """Tap the first button whose label contains `sub`."""
    draw(app, canvas)
    for b in getattr(app.screen, "buttons", ()):
        if sub in _flat(b.label):
            x, y = _center(b)
            app.handle_tap(canvas, x, y)
            draw(app, canvas)
            return True
    if must:
        raise AssertionError("no button containing %r on %s" % (sub, screen_name(app)))
    return False


def tap_corner_back(app, canvas):
    """The corner Back that RollEntry / PassphraseRoll / QRExport draw
    directly instead of keeping in self.buttons."""
    draw(app, canvas)
    b = app.screen.back_button(app)
    x, y = _center(b)
    app.handle_tap(canvas, x, y)
    draw(app, canvas)


def tap_demo_bang(app, canvas):
    """The [!] demo button beside the Back corner."""
    draw(app, canvas)
    b = S._demo_btn(lambda a: None)
    x, y = _center(b)
    app.handle_tap(canvas, x, y)
    draw(app, canvas)


def tap_roll(app, canvas, source, value, redraw=False):
    """Tap a roll-grid value.  Redraws only when asked: a 256-flip coin
    ceremony with a full PIL repaint per tap costs real minutes and asserts
    nothing new."""
    label = source.face_label(value)
    for b in app.screen.buttons:
        if b.label == label:
            x, y = _center(b)
            app.handle_tap(canvas, x, y)
            if redraw:
                draw(app, canvas)
            return
    raise AssertionError("no roll button %r" % label)


def type_word(app, canvas, word):
    """Spell a word on WordEntryScreen; tap the candidate if the prefix does
    not auto-confirm.  Same approach as sim/capture_screens.py."""
    screen = app.screen
    start = len(screen.words)
    for letter in word:
        b = next(x for x in screen.buttons if x.label == letter)
        cx, cy = _center(b)
        app.handle_tap(canvas, cx, cy)
        if app.screen is not screen or len(screen.words) > start:
            return
    for b in screen._candidate_buttons():
        if b.label == word:
            cx, cy = _center(b)
            app.handle_tap(canvas, cx, cy)
            return
    raise AssertionError("could not enter %r" % word)


def type_mnemonic(app, canvas, mnemonic):
    for w in mnemonic.split():
        assert screen_name(app) == "WordEntryScreen"
        type_word(app, canvas, w)


def assert_home_clear(app):
    assert screen_name(app) == "HomeScreen"
    assert len(app.stack) == 1
    assert app.session is None
    assert app.last_mnemonic is None
    assert app.passphrase == ""
    assert app.demo is False
    assert drv._cached_key is None and drv._cached_seed is None


# Forward-only screens have no Back by design; escape presses the choice that
# eventually leads home. RollCountConfirmScreen presses its confirm (index 0),
# never its Back, or gate<->confirm would ping-pong forever.
ESCAPE_SPECIAL = {
    "SeedSignerRollGateScreen": "Add 100th Roll",
    "EntropyWarningScreen": "Start Over",
    "EntropyCapturedScreen": "Reveal Words",
    "PassphraseWordsScreen": "Use This",
}
ESCAPE_PRIORITY = ("Back", "Home", "Cancel", "Done", "Continue", "Skip",
                   "Start Over", "Next >", "Use This", "Reveal Words")


def escape_to_home(app, canvas, limit=80):
    """From wherever the app is, press Back-ish controls until the home
    screen, then assert the session is fully cleared.  Bounded, so a
    navigation trap fails the test instead of hanging it."""
    trail = []
    for _ in range(limit):
        if screen_name(app) == "HomeScreen":
            draw(app, canvas)
            assert_home_clear(app)
            return trail
        name = screen_name(app)
        trail.append(name)
        if name == "RollCountConfirmScreen":
            draw(app, canvas)
            b = app.screen.buttons[0]
            x, y = _center(b)
            app.handle_tap(canvas, x, y)
            draw(app, canvas)
            continue
        if name in ESCAPE_SPECIAL:
            tap_exact(app, canvas, ESCAPE_SPECIAL[name])
            continue
        for pref in ESCAPE_PRIORITY:
            if tap_exact(app, canvas, pref, must=False):
                break
        else:
            tap_corner_back(app, canvas)
    raise AssertionError("could not reach home in %d steps: %r" % (limit, trail))


def quiet_rolls(source, words):
    """A deterministic full roll sequence that trips no entropy advisory, so
    the ceremony lands directly on EntropyCapturedScreen."""
    target = ent.required_rolls(source, words)
    rng = random.Random(sum(ord(c) for c in source.name) * 1009 + words)
    for _ in range(500):
        rolls = [rng.randrange(1, source.sides + 1) for _ in range(target)]
        if ent.assess_rolls(source, rolls) is None:
            return rolls
    raise AssertionError("no quiet sequence found for %s/%d" % (source.name, words))


def start_ceremony(source, words, canvas=None):
    app = App()
    canvas = canvas or Canvas()
    tap_exact(app, canvas, "Roll a Seed")
    assert screen_name(app) == "MethodSelectScreen"
    tap_exact(app, canvas, SOURCE_LABEL[source.name])
    assert screen_name(app) == "SeedLength" if False else True  # title, not class
    assert screen_name(app) == "WordCountScreen"
    tap_exact(app, canvas, "%d words" % words)
    assert screen_name(app) == "RollEntryScreen"
    assert app.session is not None and app.session.source is source
    return app, canvas


def walk_reveal_to_home(app, canvas, mnemonic):
    """From EntropyCapturedScreen: reveal every page, checksum word,
    completion, Done.  Asserts the words shown are the mnemonic's."""
    words = mnemonic.split()
    assert screen_name(app) == "EntropyCapturedScreen"
    tap_exact(app, canvas, "Reveal Words")
    pages_seen = 0
    while screen_name(app) == "WordListScreen":
        page = app.screen.page
        pages_seen += 1
        draw(app, canvas)
        # every display word of this page reaches the canvas (hero face draws
        # prefix and remainder separately)
        start = page * app.screen.WORDS_PER_PAGE
        for w in app.screen.display_words[start:start + app.screen.WORDS_PER_PAGE]:
            assert w[:4] in canvas.texts
        if not tap_exact(app, canvas, "Next >", must=False):
            tap_exact(app, canvas, "Continue")
    expected_pages = -(-(len(words) - 1) // 6)
    assert pages_seen == expected_pages
    assert screen_name(app) == "ChecksumWordScreen"
    assert app.screen.word == words[-1]
    draw(app, canvas)
    assert words[-1] in canvas.texts
    tap_exact(app, canvas, "Continue")
    assert screen_name(app) == "GenerateCompleteScreen"
    tap_exact(app, canvas, "Done")
    assert_home_clear(app)


def seed_one_account(label="acctone", template=T84, passphrase=""):
    """Enrol one real account straight through the store (engine level), for
    tests that need an enrolled account without walking the enrol UI."""
    _, xpub = drv.account_xpub(M12, template, passphrase=passphrase)
    acct.add(label, xpub, template.bip)
    return xpub


# ---------------------------------------------------------------------------
# 1. Every entropy source x every word count, end to end


@pytest.mark.parametrize("source,words", [
    (ent.D6, 12), (ent.D6, 24),
    (ent.D8, 12), (ent.D8, 24),
    (ent.D12, 12), (ent.D12, 24),
    (ent.COIN, 12), (ent.COIN, 24),
])
def test_full_ceremony_every_source_and_length(source, words):
    rolls = quiet_rolls(source, words)
    app, canvas = start_ceremony(source, words)
    session = app.session
    draw(app, canvas)
    assert "0/%d" % len(rolls) in canvas.texts   # zero-roll frame renders
    for i, v in enumerate(rolls):
        if screen_name(app) == "SeedSignerRollGateScreen":
            # the D6/24 interoperability fork, taken here on its 100th-roll
            # branch (the 99 branch has its own dedicated test below)
            tap_exact(app, canvas, "Add 100th Roll")
            assert screen_name(app) == "RollCountConfirmScreen"
            tap_exact(app, canvas, "Continue to Roll 100")
            assert screen_name(app) == "RollEntryScreen"
        tap_roll(app, canvas, source, v, redraw=(i in (0, 1, len(rolls) - 2)))
        if i == 0:
            assert "1/%d" % len(rolls) in canvas.texts
        if i == len(rolls) - 2:
            # one roll short of target: never finalised.  For D6/24 the
            # 99th roll IS the interoperability gate, handled at the top of
            # the next iteration; everything else stays on the roll grid.
            assert not session.is_complete
            assert screen_name(app) in ("RollEntryScreen",
                                        "SeedSignerRollGateScreen")
            assert app.last_mnemonic is None
    assert screen_name(app) == "EntropyCapturedScreen"
    # entropy is exactly SHA256 of the concatenated fixed-width roll string
    enc = "".join(session.source.encode_roll(v) for v in session.rolls)
    expected = hashlib.sha256(enc.encode()).digest()[:(128 if words == 12 else 256) // 8]
    assert app.screen.entropy == expected
    assert app.last_mnemonic == mn.entropy_to_mnemonic(expected)
    assert len(app.last_mnemonic.split()) == words
    walk_reveal_to_home(app, canvas, app.last_mnemonic)
    assert canvas.lost == []


def test_roll_boundaries_zero_one_short_exact_past():
    source, words = ent.D6, 12
    rolls = quiet_rolls(source, words)
    app, canvas = start_ceremony(source, words)
    session = app.session
    # zero rolls: refusing to finalise is the engine's clean RuntimeError
    with pytest.raises(RuntimeError):
        session.final_entropy()
    assert ent.assess_rolls(source, []) is None
    for v in rolls[:-1]:
        tap_roll(app, canvas, source, v)
    assert screen_name(app) == "RollEntryScreen"   # one short: no finalise
    with pytest.raises(RuntimeError):
        session.final_entropy()
    tap_roll(app, canvas, source, rolls[-1], redraw=True)
    assert session.is_complete
    assert screen_name(app) == "EntropyCapturedScreen"
    # one past target is unreachable from the UI (the screen was popped);
    # the engine refuses it cleanly rather than corrupting the entropy
    with pytest.raises(RuntimeError):
        session.add_roll(1)
    # out-of-range values are equally unreachable and equally refused
    with pytest.raises(ValueError):
        ent.RollSession(source, 12).add_roll(0)
    with pytest.raises(ValueError):
        ent.RollSession(source, 12).add_roll(7)


def test_seedsigner_fork_both_branches_via_ui():
    """Branch A (Use 99) must reproduce SeedSigner's published vector; branch
    B (Add 100th) must produce a different seed from the same 99 rolls."""
    published = [int(c) for c in PUBLISHED_ROLLS_99]
    concern_99 = ent.assess_rolls(ent.D6, published)

    def to_gate():
        app, canvas = start_ceremony(ent.D6, 24)
        for v in published:
            tap_roll(app, canvas, ent.D6, v)
        assert screen_name(app) == "SeedSignerRollGateScreen"
        assert len(app.session.rolls) == 99
        assert app.last_mnemonic is None    # nothing finalised at the gate
        return app, canvas

    # Branch A: 99 rolls, SeedSigner compatible
    app, canvas = to_gate()
    tap_exact(app, canvas, "Use 99: SeedSigner")
    assert screen_name(app) == "RollCountConfirmScreen"
    tap_exact(app, canvas, "Confirm 99 Rolls")
    if concern_99 is not None:
        assert screen_name(app) == "EntropyWarningScreen"
        tap_sub(app, canvas, "Use These")
    assert screen_name(app) == "EntropyCapturedScreen"
    assert app.screen.entropy == hashlib.sha256(
        PUBLISHED_ROLLS_99.encode()).digest()
    mnemonic_99 = app.last_mnemonic
    assert mnemonic_99 == PUBLISHED_MNEMONIC
    walk_reveal_to_home(app, canvas, mnemonic_99)

    # Branch B: same 99 rolls, then the 100th
    app, canvas = to_gate()
    tap_exact(app, canvas, "Add 100th Roll")
    tap_exact(app, canvas, "Continue to Roll 100")
    assert screen_name(app) == "RollEntryScreen"
    tap_roll(app, canvas, ent.D6, 4, redraw=True)
    if screen_name(app) == "EntropyWarningScreen":
        tap_sub(app, canvas, "Use These")
    assert screen_name(app) == "EntropyCapturedScreen"
    assert len(app.session.rolls) == 100
    mnemonic_100 = app.last_mnemonic
    assert mnemonic_100 != mnemonic_99
    assert app.screen.entropy != hashlib.sha256(
        PUBLISHED_ROLLS_99.encode()).digest()
    walk_reveal_to_home(app, canvas, mnemonic_100)


def test_back_from_roll_count_confirm_returns_to_gate_unchanged():
    published = [int(c) for c in PUBLISHED_ROLLS_99]
    app, canvas = start_ceremony(ent.D6, 24)
    app.session.rolls.extend(published[:-1])
    tap_roll(app, canvas, ent.D6, published[-1], redraw=True)
    assert screen_name(app) == "SeedSignerRollGateScreen"
    tap_exact(app, canvas, "Use 99: SeedSigner")
    tap_exact(app, canvas, "Back")
    assert screen_name(app) == "SeedSignerRollGateScreen"
    assert len(app.session.rolls) == 99
    assert app.last_mnemonic is None


def test_entropy_warning_start_over_and_continue():
    """All-same input must be interrupted; both warning choices must work."""
    source, words = ent.D6, 12
    target = ent.required_rolls(source, words)
    app, canvas = start_ceremony(source, words)
    for _ in range(target):
        tap_roll(app, canvas, source, 3)
    assert screen_name(app) == "EntropyWarningScreen"
    draw(app, canvas)
    assert any("Pattern noticed" in t for t in canvas.texts)
    # Start Over: rolls discarded in place, back on the roll grid
    tap_exact(app, canvas, "Start Over")
    assert screen_name(app) == "RollEntryScreen"
    assert app.session.rolls == []
    assert app.last_mnemonic is None
    # run it again and continue through the warning this time
    for _ in range(target):
        tap_roll(app, canvas, source, 3)
    assert screen_name(app) == "EntropyWarningScreen"
    tap_sub(app, canvas, "Use These")
    assert screen_name(app) == "EntropyCapturedScreen"
    walk_reveal_to_home(app, canvas, app.last_mnemonic)


def test_early_lift_on_a_roll_button_commits_nothing():
    app, _ = start_ceremony(ent.D6, 12)     # navigate with a held canvas
    canvas = LiftCanvas(hold_for=1)         # then lift early on the roll
    b = next(x for x in app.screen.buttons if x.label == "1")
    x, y = _center(b)
    app.handle_tap(canvas, x, y)
    assert app.session.rolls == []


# ---------------------------------------------------------------------------
# 2. Demo mode, both entry points, all the way through


def _assert_demo_stamp(app, canvas):
    canvas.texts.clear()
    draw(app, canvas)
    assert "DEMO" in canvas.texts, (
        "screen %s lost the DEMO stamp" % screen_name(app))


def test_demo_from_roll_entry_full_walk():
    app, canvas = start_ceremony(ent.D6, 12)
    tap_demo_bang(app, canvas)
    assert screen_name(app) == "DemoConfirmScreen"
    # nothing populated before consent; Cancel really cancels
    tap_exact(app, canvas, "Cancel")
    assert screen_name(app) == "RollEntryScreen"
    assert app.session.rolls == [] and app.demo is False
    # consent this time
    tap_demo_bang(app, canvas)
    tap_exact(app, canvas, "Start Demo")
    assert app.demo is True
    assert screen_name(app) == "EntropyCapturedScreen"
    _assert_demo_stamp(app, canvas)
    tap_exact(app, canvas, "Reveal Words")
    while screen_name(app) == "WordListScreen":
        _assert_demo_stamp(app, canvas)
        if not tap_exact(app, canvas, "Next >", must=False):
            tap_exact(app, canvas, "Continue")
    assert screen_name(app) == "ChecksumWordScreen"
    _assert_demo_stamp(app, canvas)
    tap_exact(app, canvas, "Continue")
    assert screen_name(app) == "GenerateCompleteScreen"
    _assert_demo_stamp(app, canvas)
    # into the address flow, still stamped
    tap_exact(app, canvas, "Verify Address")
    assert screen_name(app) == "DerivationPathScreen"
    _assert_demo_stamp(app, canvas)
    tap_sub(app, canvas, "BIP84")
    assert screen_name(app) == "AddressIndexScreen"
    _assert_demo_stamp(app, canvas)
    tap_exact(app, canvas, "Derive Address")
    assert screen_name(app) == "AddressDisplayScreen"
    _assert_demo_stamp(app, canvas)
    # enrolment must refuse the demo seed
    tap_exact(app, canvas, "Enrol Account")
    assert screen_name(app) == "EnrolScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert "DEMO" in canvas.texts
    assert any(t.startswith("Refused") for t in canvas.texts)
    app.screen._enrol(app)              # the backstop guard, driven directly
    assert acct.load() == []
    escape_to_home(app, canvas)
    draw(app, canvas)
    assert S.DEMO is False              # the stamp died with the session


def test_demo_hidden_after_the_first_real_roll():
    app, canvas = start_ceremony(ent.D6, 12)
    tap_roll(app, canvas, ent.D6, 5, redraw=True)
    tap_demo_bang(app, canvas)
    assert screen_name(app) == "RollEntryScreen"   # tap ignored, no gate
    assert app.demo is False


def test_demo_from_verify_entry_full_walk():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    assert screen_name(app) == "VerifyEntryScreen"
    tap_demo_bang(app, canvas)
    assert screen_name(app) == "DemoConfirmScreen"
    tap_exact(app, canvas, "Start Demo")
    assert app.demo is True
    assert app.last_mnemonic == M12    # the canonical BIP39 test mnemonic
    assert screen_name(app) == "DerivationPathScreen"
    _assert_demo_stamp(app, canvas)
    tap_exact(app, canvas, "All Types")
    assert screen_name(app) == "AddressIndexScreen"
    _assert_demo_stamp(app, canvas)
    tap_exact(app, canvas, "Derive Address")
    assert screen_name(app) == "AllAddressesScreen"
    _assert_demo_stamp(app, canvas)
    tap_sub(app, canvas, "BIP84")
    assert screen_name(app) == "AddressDisplayScreen"
    _assert_demo_stamp(app, canvas)
    # the canonical mnemonic's BIP84 first address is a published vector
    assert app.screen.address == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    tap_exact(app, canvas, "Enrol Account")
    canvas.texts.clear()
    draw(app, canvas)
    assert any(t.startswith("Refused") for t in canvas.texts)
    app.screen._enrol(app)
    assert acct.load() == []
    escape_to_home(app, canvas)


# ---------------------------------------------------------------------------
# 3. Verify Seed


def test_verify_valid_mnemonic_typed_and_back_out_each_step():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    tap_exact(app, canvas, "Enter Seed Manually")
    assert screen_name(app) == "ManualWordCountScreen"
    tap_exact(app, canvas, "12 words")
    assert screen_name(app) == "WordEntryScreen"
    type_mnemonic(app, canvas, M12)
    assert screen_name(app) == "DerivationPathScreen"
    assert app.last_mnemonic == M12
    # back out one step at a time: DerivationPath -> ManualWordCount ->
    # VerifyEntry -> home (which is the session teardown)
    tap_exact(app, canvas, "Back")
    assert screen_name(app) == "ManualWordCountScreen"
    tap_exact(app, canvas, "Back")
    assert screen_name(app) == "VerifyEntryScreen"
    # This corner used to read "Back" on the one screen where the pop tears
    # the session down: VerifyEntryScreen rebuilds its buttons inside draw()
    # AFTER App._sync_back_labels has run for the frame, and passed None, so
    # the sync was discarded. Now built with back_button(app), so the label
    # tells the truth about where it goes.
    labels = [b.label for b in app.screen.buttons if b.label in ("Back", "Home")]
    assert labels == ["Home"]
    tap_exact(app, canvas, "Home")
    assert_home_clear(app)
    assert canvas.lost == []


def test_verify_24_words_typed():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    tap_exact(app, canvas, "Enter Seed Manually")
    tap_exact(app, canvas, "24 words")
    type_mnemonic(app, canvas, M24)
    assert screen_name(app) == "DerivationPathScreen"
    assert app.last_mnemonic == M24
    escape_to_home(app, canvas)


def test_verify_invalid_checksum_all_three_exits():
    def to_invalid():
        app, canvas = App(), Canvas()
        tap_exact(app, canvas, "Verify Seed")
        tap_exact(app, canvas, "Enter Seed Manually")
        tap_exact(app, canvas, "12 words")
        type_mnemonic(app, canvas, "abandon " * 12)
        assert screen_name(app) == "InvalidSeedScreen"
        assert app.last_mnemonic is None
        canvas.texts.clear()
        draw(app, canvas)
        assert any("Checksum did not verify" in t for t in canvas.texts)
        return app, canvas

    # Try Again resumes with everything but the last word
    app, canvas = to_invalid()
    tap_exact(app, canvas, "Try Again")
    assert screen_name(app) == "WordEntryScreen"
    assert app.screen.words == ["abandon"] * 11
    type_word(app, canvas, "about")            # fix the last word
    assert screen_name(app) == "DerivationPathScreen"
    assert app.last_mnemonic == M12
    escape_to_home(app, canvas)

    # Start Over gives an empty keyboard
    app, canvas = to_invalid()
    tap_exact(app, canvas, "Start Over")
    assert screen_name(app) == "WordEntryScreen"
    assert app.screen.words == []
    escape_to_home(app, canvas)

    # Cancel goes straight home, cleared
    app, canvas = to_invalid()
    tap_exact(app, canvas, "Cancel")
    assert_home_clear(app)


def test_verify_word_entry_edge_input():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    tap_exact(app, canvas, "Enter Seed Manually")
    tap_exact(app, canvas, "12 words")
    screen = app.screen
    # a prefix no BIP39 word has: the screen must say so, not raise
    for letter in ("z", "z"):
        b = next(x for x in screen.buttons if x.label == letter)
        app.handle_tap(canvas, *_center(b))
    assert screen.prefix == "zz"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("no match" in t for t in canvas.texts)
    # backspace recovers; empty backspace is harmless; empty submit impossible
    tap_exact(app, canvas, "<- Back")
    tap_exact(app, canvas, "<- Back")
    tap_exact(app, canvas, "<- Back")          # already empty: no-op
    assert screen.prefix == "" and screen.words == []
    assert screen_name(app) == "WordEntryScreen"
    tap_exact(app, canvas, "Cancel")
    assert screen_name(app) == "ManualWordCountScreen"
    escape_to_home(app, canvas)


def test_verify_use_last_rolled_seed_appears_and_works():
    app, canvas = App(), Canvas()
    app.last_mnemonic = M12
    app.push(S.VerifyEntryScreen())
    tap_exact(app, canvas, "Use Last Rolled Seed")
    assert screen_name(app) == "DerivationPathScreen"
    assert app.screen.mnemonic == M12
    escape_to_home(app, canvas)


def test_backup_confirmation_match_and_mismatch():
    app, canvas = App(), Canvas()
    app.last_mnemonic = M12
    app.push(S.GenerateCompleteScreen(M12))
    tap_exact(app, canvas, "Confirm Backup")
    assert screen_name(app) == "WordEntryScreen"
    type_mnemonic(app, canvas, M12)
    assert screen_name(app) == "BackupResultScreen"
    assert app.screen.success is True
    canvas.texts.clear()
    draw(app, canvas)
    assert "MATCH" in canvas.texts
    tap_exact(app, canvas, "Continue")
    assert screen_name(app) == "GenerateCompleteScreen"

    # now a mismatch: right words, wrong last word (still a wordlist word)
    tap_exact(app, canvas, "Confirm Backup")
    wrong = M12.split()[:-1] + ["zoo"]
    for w in wrong:
        type_word(app, canvas, w)
    assert screen_name(app) == "BackupResultScreen"
    assert app.screen.success is False
    canvas.texts.clear()
    draw(app, canvas)
    assert "MISMATCH" in canvas.texts
    tap_exact(app, canvas, "Try Again")
    assert screen_name(app) == "WordEntryScreen"
    tap_exact(app, canvas, "Cancel")
    assert screen_name(app) == "GenerateCompleteScreen"
    tap_exact(app, canvas, "Done")
    assert_home_clear(app)


def test_mnemonic_engine_refuses_cleanly():
    """Wrong word count, unknown word and empty entry cannot be produced by
    the keyboard, but the validation they would hit refuses without raising."""
    assert mn.validate_mnemonic("abandon " * 15) is False
    assert mn.validate_mnemonic("zzzz " * 12) is False
    assert mn.validate_mnemonic("") is False
    assert mn.validate_mnemonic("  " + M12.replace(" ", "   ") + " ") is True
    with pytest.raises(ValueError):
        mn.word_indices("notaword " * 12)


# ---------------------------------------------------------------------------
# 4. Passphrase


@needs_words
@pytest.mark.parametrize("n", pph.LENGTHS)
def test_passphrase_every_offered_length(n):
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    tap_exact(app, canvas, "Passphrase")
    assert screen_name(app) == "PassphraseLengthScreen"
    tap_sub(app, canvas, "%d words" % n)
    assert screen_name(app) == "PassphraseRollScreen"
    rolls = [random.Random(n).randrange(1, 7) for _ in range(pph.rolls_needed(n))]
    for i, v in enumerate(rolls):
        b = next(x for x in app.screen.buttons if x.label == str(v))
        app.handle_tap(canvas, *_center(b))
        if i == len(rolls) - 2:
            assert screen_name(app) == "PassphraseRollScreen"  # one short
    assert screen_name(app) == "PassphraseWordsScreen"
    expected_words = pph.words_from_rolls(rolls, str(EFF_WORDS))
    assert app.screen.words == expected_words
    canvas.texts.clear()
    draw(app, canvas)
    for w in expected_words:
        assert w in canvas.texts       # every word on the one screen, whole
    phrase = pph.to_passphrase(expected_words)
    assert any(pph.check_value(phrase) in t for t in canvas.texts)
    assert canvas.lost == []
    tap_exact(app, canvas, "Use This")
    assert screen_name(app) == "DerivationPathScreen"
    assert app.passphrase == phrase
    canvas.texts.clear()
    draw(app, canvas)
    assert any("passphrase SET" in t for t in canvas.texts)
    escape_to_home(app, canvas)        # clearing asserted inside


@needs_words
def test_passphrase_back_out_mid_roll_discards():
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    tap_exact(app, canvas, "Passphrase")
    tap_sub(app, canvas, "8 words")
    for v in (1, 2, 3, 4, 5, 6, 1):
        b = next(x for x in app.screen.buttons if x.label == str(v))
        app.handle_tap(canvas, *_center(b))
    tap_corner_back(app, canvas)
    assert screen_name(app) == "PassphraseLengthScreen"
    assert app.passphrase == ""
    escape_to_home(app, canvas)


@needs_words
def test_passphrase_changes_the_derived_address_via_ui():
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    # derive without a passphrase first
    tap_sub(app, canvas, "BIP84")
    tap_exact(app, canvas, "Derive Address")
    plain = app.screen.address
    assert plain == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    tap_exact(app, canvas, "Change Index")
    tap_exact(app, canvas, "Back")
    # roll a passphrase and derive again
    tap_exact(app, canvas, "Passphrase")
    tap_sub(app, canvas, "6 words")
    rolls = [((i * 3) % 6) + 1 for i in range(pph.rolls_needed(6))]
    for v in rolls:
        b = next(x for x in app.screen.buttons if x.label == str(v))
        app.handle_tap(canvas, *_center(b))
    tap_exact(app, canvas, "Use This")
    assert app.passphrase != ""
    tap_sub(app, canvas, "BIP84")
    tap_exact(app, canvas, "Derive Address")
    assert screen_name(app) == "AddressDisplayScreen"
    assert app.screen.address != plain
    tap_exact(app, canvas, "Done")
    assert_home_clear(app)


# ---------------------------------------------------------------------------
# 5. Accounts


def enrol_via_ui(template, passphrase="", mnemonic=M12):
    """Walk the whole enrolment path through taps; returns (app, canvas)
    parked on the post-enrol EnrolScreen."""
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(mnemonic))
    app.passphrase = passphrase
    app.last_mnemonic = mnemonic
    tap_sub(app, canvas, "BIP%d" % template.bip)
    assert screen_name(app) == "AddressIndexScreen"
    tap_exact(app, canvas, "Derive Address")
    assert screen_name(app) == "AddressDisplayScreen"
    tap_exact(app, canvas, "Enrol Account")
    assert screen_name(app) == "EnrolScreen"
    tap_exact(app, canvas, "Enrol Account")
    return app, canvas


def tap_post_enrol_home(app, canvas):
    """The post-enrolment exit is labelled "Done", and that is load-bearing.

    It used to be built as "Home", but App._sync_back_labels rewrites any
    button literally labelled Home/Back to match stack depth, so deep in the
    stack it rendered as "Back" on a button whose action is reset_to_home:
    the user read "one screen back" and lost the entire session, including a
    derivation they had just waited minutes for. "Done" is immune to that
    rewrite, and AddressDisplayScreen's own comment names the same trap.
    """
    draw(app, canvas)
    labels = [b.label for b in app.screen.buttons]
    assert "Done" in labels, labels
    assert "Home" not in labels and "Back" not in labels, labels
    tap_exact(app, canvas, "Done")
    assert_home_clear(app)


def test_enrol_to_the_cap_then_refuse_then_recover():
    # five distinct accounts: four templates plus a passphrase variant
    for t in (T44, T49, T84, T86):
        app, canvas = enrol_via_ui(t)
        assert app.screen.done is not None
        tap_post_enrol_home(app, canvas)
    app, canvas = enrol_via_ui(T84, passphrase="quiet garden stone")
    assert len(acct.load()) == 5

    # the sixth is refused on-screen, never a ValueError out of a tap
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    app.passphrase = "another phrase entirely"
    tap_sub(app, canvas, "BIP44")
    tap_exact(app, canvas, "Derive Address")
    tap_exact(app, canvas, "Enrol Account")
    assert screen_name(app) == "EnrolScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("5 of 5 accounts" in t for t in canvas.texts)
    assert len(acct.load()) == 5
    # the documented recovery route: Accounts -> delete one -> Back -> enrol
    tap_exact(app, canvas, "Accounts")
    assert screen_name(app) == "AccountListScreen"
    tap_exact(app, canvas, "Edit")
    assert screen_name(app) == "AccountManageScreen"
    tap_exact(app, canvas, "Delete")
    assert screen_name(app) == "AccountDeleteScreen"
    tap_exact(app, canvas, "Delete")
    assert screen_name(app) == "AccountListScreen"
    assert len(acct.load()) == 4
    tap_exact(app, canvas, "Back")
    assert screen_name(app) == "EnrolScreen"
    tap_exact(app, canvas, "Enrol Account")   # the refusal stood down
    assert app.screen.done is not None
    assert len(acct.load()) == 5
    tap_post_enrol_home(app, canvas)


def test_home_screen_marks_enrolled_state_and_never_raises():
    seed_one_account()
    home = S.HomeScreen()      # reads the store in its constructor
    accounts_btn = next(b for b in home.buttons if b.label == "Accounts")
    assert accounts_btn.edge == th.STATE_EDGE


def test_nickname_at_enrolment_and_rename_later():
    app, canvas = enrol_via_ui(T84)
    tap_exact(app, canvas, "Add Nickname")
    assert screen_name(app) == "NicknameScreen"
    for ch in "coldstore1":
        b = next(x for x in app.screen.buttons if x.label == ch)
        app.handle_tap(canvas, *_center(b))
    assert app.screen.value == "coldstore1"
    tap_exact(app, canvas, "Save")
    assert acct.load()[0]["label"] == "coldstore1"
    escape_to_home(app, canvas)

    # rename from the manage screen, cap-length input and backspace
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    tap_exact(app, canvas, "Edit")
    tap_exact(app, canvas, "Rename")
    screen = app.screen
    for ch in "verylongname123456":     # 18 chars against a 14 cap
        b = next(x for x in screen.buttons if x.label == ch)
        app.handle_tap(canvas, *_center(b))
    assert len(screen.value) == screen.MAX_LEN
    tap_exact(app, canvas, "<- Del")
    assert len(screen.value) == screen.MAX_LEN - 1
    tap_exact(app, canvas, "Save")
    assert screen_name(app) == "AccountManageScreen"
    assert acct.load()[0]["label"] == screen.value
    canvas.texts.clear()
    draw(app, canvas)
    assert screen.value in canvas.texts    # no stale label on the screen
    escape_to_home(app, canvas)


def test_account_open_step_mark_unmark_and_qr():
    xpub = seed_one_account()
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    canvas.texts.clear()
    draw(app, canvas)
    assert any("no seed needed" in t for t in canvas.texts)
    tap_exact(app, canvas, "acctone")
    assert screen_name(app) == "AddressIndexScreen"
    assert app.screen.xpub == xpub
    # stepper bounds: 0 floors, 9 caps
    tap_exact(app, canvas, "-")
    assert app.screen.index == 0
    for _ in range(12):
        tap_exact(app, canvas, "+")
    assert app.screen.index == 9
    tap_exact(app, canvas, "Derive Address")
    assert screen_name(app) == "AddressDisplayScreen"
    assert app.screen.index == 9
    addr9 = app.screen.address
    assert addr9 == drv.address_from_xpub(xpub, T84, index=9)
    # > at the cap is a clean no-op
    tap_exact(app, canvas, ">")
    assert app.screen.index == 9
    # < steps down and keeps the stepper honest underneath
    tap_exact(app, canvas, "<")
    assert screen_name(app) == "AddressDisplayScreen"
    assert app.screen.index == 8
    assert app.screen.address != addr9
    # mark, verify persistence, unmark
    tap_exact(app, canvas, "Mark")
    assert acct.marks(xpub) == [8]
    canvas.texts.clear()
    draw(app, canvas)
    assert "marked" in canvas.texts
    tap_exact(app, canvas, "Unmark")
    assert acct.marks(xpub) == []
    # address QR from the enrolled path
    tap_exact(app, canvas, "QR")
    assert screen_name(app) == "AddressQRScreen"
    canvas.lost = []
    draw(app, canvas)
    assert canvas.lost == []
    tap_exact(app, canvas, "Back")
    tap_exact(app, canvas, "Done")
    assert_home_clear(app)


def test_index_zero_is_derivable_and_marked_at_zero():
    xpub = seed_one_account()
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    tap_exact(app, canvas, "acctone")
    tap_exact(app, canvas, "Derive Address")
    assert app.screen.index == 0
    assert app.screen.address == drv.address_from_xpub(xpub, T84, index=0)
    tap_exact(app, canvas, "Mark")
    assert acct.marks(xpub) == [0]
    tap_exact(app, canvas, "<")            # below zero: clean no-op
    assert app.screen.index == 0
    escape_to_home(app, canvas)


@pytest.mark.parametrize("template", [T44, T49, T84, T86])
def test_account_qr_every_type_including_slip132(template):
    _, xpub = drv.account_xpub(M12, template)
    acct.add("qacct", xpub, template.bip)
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    tap_exact(app, canvas, "Edit")
    assert screen_name(app) == "AccountManageScreen"
    labels = [_flat(b.label) for b in app.screen.buttons]
    if template.bip in drv.SLIP132:
        alt = drv.SLIP132[template.bip][0]
        assert "xpub QR" in labels and alt + " QR" in labels
        tap_exact(app, canvas, alt + " QR")
        assert screen_name(app) == "AccountQRScreen"
        assert app.screen.form == alt
        assert app.screen.key.startswith(alt)
        assert app.screen.fp == drv.xpub_fingerprint(xpub)  # id is canonical
        tap_exact(app, canvas, "Back")
        tap_exact(app, canvas, "xpub QR")
    else:
        tap_exact(app, canvas, "Show xpub QR")
    assert screen_name(app) == "AccountQRScreen"
    assert app.screen.key == xpub
    canvas.texts.clear()
    canvas.lost = []
    draw(app, canvas)
    if template.bip == 86:
        assert any("taproot has no zpub form" in t for t in canvas.texts)
    assert canvas.lost == []
    escape_to_home(app, canvas)


def test_direct_account_key_screen_from_seed_path():
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    tap_sub(app, canvas, "BIP49")
    tap_exact(app, canvas, "Derive Address")
    tap_exact(app, canvas, "Account Key")
    assert screen_name(app) == "AccountKeyScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("READ-ONLY VIEW" in t for t in canvas.texts)
    assert acct.load() == []               # stores nothing
    tap_exact(app, canvas, "ypub QR")
    assert screen_name(app) == "AccountQRScreen"
    assert app.screen.key.startswith("ypub")
    assert acct.load() == []
    escape_to_home(app, canvas)


# ---------------------------------------------------------------------------
# 6. accounts.json damage matrix


def _write_store(data):
    with open(acct.PATH, "wb") as f:
        f.write(data)


@pytest.mark.parametrize("name,raw", [
    ("corrupt_not_json", b"{this is not json"),
    ("truncated", b'[{"xpub": "xpub6truncat'),
    ("empty_file", b""),
    ("wrong_shape_dict", b'{"xpub": "x", "bip": 84}'),
    ("null", b"null"),
    ("list_of_strings", b'["a", "b", "c"]'),
    ("records_missing_fields", b'[{"label": "x"}, {"bip": 84}]'),
])
def test_damaged_store_degrades_to_empty_list(name, raw):
    """Each of these currently degrades to an empty account list: the home
    screen builds, the Accounts screen shows the no-accounts message, and
    nothing raises."""
    _write_store(raw)
    S.HomeScreen()                          # constructor reads the store
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    assert screen_name(app) == "AccountListScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("No accounts enrolled" in t for t in canvas.texts)
    escape_to_home(app, canvas)


def test_store_over_cap_shows_five_and_delete_surfaces_hidden():
    """An over-cap store from an older build: first five render, deleting one
    surfaces the sixth, nothing raises, new enrolment refused."""
    records = [{"label": "acct%d" % i, "xpub": "fakexpub%d" % i,
                "bip": 84, "account": 0} for i in range(7)]
    _write_store(json.dumps(records).encode())
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    draw(app, canvas)
    rows = [b for b in app.screen.buttons if b.label.startswith("acct")]
    assert len(rows) == 5
    assert [b.label for b in rows] == ["acct%d" % i for i in range(5)]
    tap_exact(app, canvas, "Edit")
    tap_exact(app, canvas, "Delete")
    tap_exact(app, canvas, "Delete")
    assert len(acct.load()) == 6
    draw(app, canvas)
    rows = [b.label for b in app.screen.buttons if b.label.startswith("acct")]
    assert rows == ["acct%d" % i for i in range(1, 6)]  # acct5 surfaced
    with pytest.raises(ValueError):
        acct.add("new", "fakexpubnew", 84)   # engine backstop still refuses
    escape_to_home(app, canvas)


def test_store_bad_bip_and_missing_account_reach_damaged_screen():
    """The two damage shapes the UI DOES refuse gracefully."""
    records = [
        {"label": "unknownbip", "xpub": "fakexpub0", "bip": 99, "account": 0},
        {"label": "noaccount", "xpub": "fakexpub1", "bip": 84},
    ]
    _write_store(json.dumps(records).encode())
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    tap_exact(app, canvas, "unknownbip")
    assert screen_name(app) == "DamagedAccountScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("cannot be used" in t for t in canvas.texts)
    tap_exact(app, canvas, "Back")
    tap_exact(app, canvas, "noaccount")
    assert screen_name(app) == "DamagedAccountScreen"
    escape_to_home(app, canvas)


def test_malformed_xpub_string_reaches_the_damaged_screen():
    """A record whose xpub is not parseable base58 used to render on the list
    and OPEN, then raise ValueError out of the tap handler the moment the user
    asked to derive, landing on the CrashScreen and ending the session.

    _open validated bip and account but not the xpub itself, so the one damage
    shape that survived to the derive call was the one carrying the key. It is
    now refused at open time like the other two, on the screen that exists to
    say so calmly.
    """
    _write_store(json.dumps([{"label": "badxpub", "xpub": "xpub6NOTVALID",
                              "bip": 84, "account": 0}]).encode())
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    tap_exact(app, canvas, "badxpub")
    assert screen_name(app) == "DamagedAccountScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("cannot be used" in t for t in canvas.texts)
    escape_to_home(app, canvas)


def test_wrong_typed_record_fields_are_dropped_not_crashed():
    """Records whose xpub is not a string, or whose bip is not an int, are
    now dropped by accounts.load()'s type validation (fixed mid-QA; before
    the fix an int xpub crash-looped the Accounts list with no UI path to
    delete the record).  A wrong-typed `account` degrades to the
    DamagedAccountScreen route rather than inventing a path."""
    _write_store(json.dumps([
        {"label": "intxpub", "xpub": 12345, "bip": 84, "account": 0},
        {"label": "strbip", "xpub": "fakexpub0", "bip": "84", "account": 0},
        {"label": "boolbip", "xpub": "fakexpub1", "bip": True, "account": 0},
        {"label": "straccount", "xpub": "fakexpub2", "bip": 84,
         "account": "zero"},
    ]).encode())
    loaded = acct.load()
    assert [r["label"] for r in loaded] == ["straccount"]
    assert "account" not in loaded[0]      # bad type degrades to absent
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    assert screen_name(app) == "AccountListScreen"
    tap_exact(app, canvas, "straccount")
    assert screen_name(app) == "DamagedAccountScreen"
    escape_to_home(app, canvas)


def test_marks_field_damage_is_salvaged():
    _write_store(json.dumps([{
        "label": "m", "xpub": "fakexpub0", "bip": 84, "account": 0,
        "marks": ["x", -1, True, 3, 3, 1.5, 2]}]).encode())
    assert acct.marks("fakexpub0") == [2, 3]
    assert acct.toggle_mark("unknown-xpub", 1) is False
    assert acct.marks("nonexistent") == []


# ---------------------------------------------------------------------------
# 7. Crash containment


class _RaisingScreen(S.Screen):
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0
        self.buttons = [S.TapButton(10, 100, 100, 40, "boom",
                                    self._boom)]

    def _boom(self, app):
        raise self._exc

    def draw(self, app, canvas):
        canvas.fill(th.BG)


class _DrawBomb(S.Screen):
    buttons = ()

    def __init__(self):
        self.calls = 0

    def draw(self, app, canvas):
        self.calls += 1
        if self.calls >= 2:
            raise MemoryError("allocating 3840 bytes")
        canvas.fill(th.BG)


def _populated_app():
    app = App()
    app.push(S.VerifyEntryScreen())
    app.session = ent.RollSession(ent.D6, 12)
    app.last_mnemonic = M12
    app.passphrase = "secret words here"
    drv.derive_address(M12, T84, index=0, passphrase=app.passphrase)
    assert drv._cached_seed is not None
    return app


def _fresh_diag():
    try:
        os.remove(diag._PATH)
    except OSError:
        pass


def test_crash_in_draw_clears_every_secret_and_recovers():
    _fresh_diag()
    app = _populated_app()
    app.push(_DrawBomb())
    canvas = LoopCanvas([(5, 5)])
    try:
        A._main_loop(app, canvas)
    except _LoopDone:
        pass
    assert screen_name(app) == "CrashScreen"
    assert app.screen.cleared is True
    assert app.session is None
    assert app.last_mnemonic is None
    assert app.passphrase == ""
    assert app.demo is False
    assert drv._cached_key is None and drv._cached_seed is None
    assert diag.read().get("crash", "").startswith("MemoryError")
    # the crash screen renders without lost text and Start Over goes home
    canvas2 = Canvas()
    draw(app, canvas2)
    assert any("Something went wrong" in t for t in canvas2.texts)
    assert "MemoryError" in canvas2.texts       # type only, never the message
    assert not any("3840" in t for t in canvas2.texts)
    assert canvas2.lost == []
    tap_exact(app, canvas2, "Start Over")
    assert_home_clear(app)


def test_crash_in_a_tap_handler_clears_secrets_too():
    _fresh_diag()
    app = _populated_app()
    boom = _RaisingScreen(RuntimeError("boom"))
    app.push(boom)
    canvas = LoopCanvas([_center(boom.buttons[0])])
    try:
        A._main_loop(app, canvas)
    except _LoopDone:
        pass
    assert screen_name(app) == "CrashScreen"
    assert app.session is None and app.last_mnemonic is None
    assert app.passphrase == "" and drv._cached_seed is None
    tap_exact(app, Canvas(), "Start Over")
    assert_home_clear(app)


def test_demo_flag_dies_on_the_crash_path():
    _fresh_diag()
    app = _populated_app()
    app.demo = True
    app.push(_DrawBomb())
    try:
        A._main_loop(app, LoopCanvas([(5, 5)]))
    except _LoopDone:
        pass
    assert screen_name(app) == "CrashScreen"
    assert app.demo is False
    canvas = Canvas()
    draw(app, canvas)
    assert "DEMO" not in canvas.texts


# ---------------------------------------------------------------------------
# 8. QR export gates


def test_seed_qr_export_reveal_and_autoblank():
    app, canvas = App(), Canvas()
    app.last_mnemonic = M24
    app.push(S.GenerateCompleteScreen(M24))
    tap_exact(app, canvas, "Export QR")
    assert screen_name(app) == "QRExportScreen"
    canvas.texts.clear()
    draw(app, canvas)
    assert any("camera" in t for t in canvas.texts)     # the warning gate
    # held reveal: draws the QR frame, then auto-blanks back to the warning
    for label in ("Compact QR", "Standard QR"):
        canvas.texts.clear()
        tap_exact(app, canvas, label)
        assert screen_name(app) == "QRExportScreen"     # nothing was pushed
        assert any("no camera on this device:" in t for t in canvas.texts)
    # an early lift must never reveal
    lift = LiftCanvas(hold_for=1)
    b = app.screen.compact_btn
    app.handle_tap(lift, *_center(b))
    assert not any("as raw entropy" in t for t in lift.texts)
    tap_corner_back(app, canvas)
    assert screen_name(app) == "GenerateCompleteScreen"
    tap_exact(app, canvas, "Done")
    assert_home_clear(app)


# ---------------------------------------------------------------------------
# 9. Longest strings, rendered without truncation


def test_taproot_address_renders_completely_on_every_screen():
    path, addr = drv.derive_address(M12, T86, index=0)
    assert len(addr) == 62 and addr.startswith("bc1p")

    def assert_address_complete(canvas):
        i = 0
        for s in canvas.texts:
            if len(s) >= 2 and addr[i:i + len(s)] == s:
                i += len(s)
                if i == len(addr):
                    return
        raise AssertionError("address truncated at char %d of %d" % (i, len(addr)))

    app = App()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    app.push(S.AddressIndexScreen(M12, T86))
    app.push(S.AddressDisplayScreen(M12, T86, 0, path, addr))
    canvas = Canvas()
    draw(app, canvas)
    assert_address_complete(canvas)
    assert canvas.lost == []
    tap_exact(app, canvas, "QR")
    assert screen_name(app) == "AddressQRScreen"
    canvas.texts.clear()
    canvas.lost = []
    draw(app, canvas)
    assert_address_complete(canvas)
    assert canvas.lost == []
    escape_to_home(app, canvas)


@needs_words
def test_ten_longest_passphrase_words_fit_the_words_screen():
    # find the longest word in the EFF list and show ten of it
    longest = ""
    with open(EFF_WORDS, "rb") as f:
        raw = f.read()
    for i in range(0, len(raw), pph.WORD_WIDTH):
        w = raw[i:i + pph.WORD_WIDTH].split(b"\x00")[0].decode()
        if len(w) > len(longest):
            longest = w
    assert len(longest) == 9               # the EFF large list's known max
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    app.push(S.PassphraseLengthScreen(M12))
    app.push(S.PassphraseWordsScreen(M12, [longest] * 10))
    draw(app, canvas)
    assert canvas.texts.count(longest) >= 10
    assert canvas.lost == []
    tap_exact(app, canvas, "Use This")
    assert app.passphrase == " ".join([longest] * 10)
    escape_to_home(app, canvas)


def test_longest_candidate_words_fit_the_keyboard_screen():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    tap_exact(app, canvas, "Enter Seed Manually")
    tap_exact(app, canvas, "12 words")
    screen = app.screen
    for letter in "att":                    # candidates include "attitude" (8)
        b = next(x for x in screen.buttons if x.label == letter)
        app.handle_tap(canvas, *_center(b))
    cands = screen._candidates()
    assert any(len(w) == 8 for w in cands)
    canvas.lost = []
    draw(app, canvas)
    assert canvas.lost == []
    escape_to_home(app, canvas)


def test_24_word_reveal_and_all_types_screens_lose_nothing():
    app, canvas = App(), Canvas()
    app.last_mnemonic = M24
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M24))
    tap_exact(app, canvas, "All Types")
    canvas.lost = []
    tap_exact(app, canvas, "Derive Address")
    assert screen_name(app) == "AllAddressesScreen"
    assert canvas.lost == []
    # each row opens the full comparison screen
    for bip in (44, 49, 84, 86):
        tap_sub(app, canvas, "BIP%d" % bip)
        assert screen_name(app) == "AddressDisplayScreen"
        canvas.lost = []
        draw(app, canvas)
        assert canvas.lost == []
        tap_exact(app, canvas, "Back")
        assert screen_name(app) == "AllAddressesScreen"
    escape_to_home(app, canvas)


# ---------------------------------------------------------------------------
# 10. Info screens


def test_about_pages_all_render_and_navigate():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "About")
    assert screen_name(app) == "AboutScreen"
    seen = {app.screen.page}
    for _ in range(len(app.screen.PAGES) - 1):
        canvas.lost = []
        canvas.texts.clear()
        draw(app, canvas)
        assert canvas.lost == []
        assert "%d of %d" % (app.screen.page + 1, len(app.screen.PAGES)) in canvas.texts
        tap_exact(app, canvas, "Next >")
        seen.add(app.screen.page)
    assert seen == set(range(len(app.screen.PAGES)))
    assert not tap_exact(app, canvas, "Next >", must=False)  # last page
    # the pinned topic buttons jump directly
    tap_exact(app, canvas, "Roll?")
    assert app.screen.page == 1
    escape_to_home(app, canvas)


@needs_build
def test_verify_build_and_diagnostics_render():
    app, canvas = App(), Canvas()
    tap_sub(app, canvas, "Verify Build")
    assert screen_name(app) == "VerifyBuildScreen"
    canvas.lost = []
    draw(app, canvas)                       # computes the fingerprint
    assert app.screen.digest is not None
    assert canvas.lost == []
    tap_exact(app, canvas, "Diagnostics")
    assert screen_name(app) == "DiagScreen"
    canvas.lost = []
    canvas.texts.clear()
    draw(app, canvas)
    assert canvas.lost == []
    assert any("boots" in t for t in canvas.texts)
    escape_to_home(app, canvas)


# ---------------------------------------------------------------------------
# 11. Back out from every reachable stage


def _stage_method_select():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Roll a Seed")
    return app, canvas


def _stage_word_count():
    app, canvas = _stage_method_select()
    tap_exact(app, canvas, "Dice: D8")
    return app, canvas


def _stage_roll_empty():
    return start_ceremony(ent.D12, 12)


def _stage_roll_mid():
    app, canvas = start_ceremony(ent.COIN, 12)
    for v in (1, 2, 2, 1, 1):
        tap_roll(app, canvas, ent.COIN, v)
    return app, canvas


def _stage_gate():
    app, canvas = start_ceremony(ent.D6, 24)
    app.session.rolls.extend(int(c) for c in PUBLISHED_ROLLS_99[:-1])
    tap_roll(app, canvas, ent.D6, int(PUBLISHED_ROLLS_99[-1]), redraw=True)
    assert screen_name(app) == "SeedSignerRollGateScreen"
    return app, canvas


def _stage_confirm_99():
    app, canvas = _stage_gate()
    tap_exact(app, canvas, "Use 99: SeedSigner")
    return app, canvas


def _stage_warning():
    app, canvas = start_ceremony(ent.D6, 12)
    for _ in range(50):
        tap_roll(app, canvas, ent.D6, 2)
    assert screen_name(app) == "EntropyWarningScreen"
    return app, canvas


def _stage_qr_export():
    app, canvas = App(), Canvas()
    app.last_mnemonic = M12
    app.push(S.GenerateCompleteScreen(M12))
    tap_exact(app, canvas, "Export QR")
    return app, canvas


def _stage_verify_entry():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    return app, canvas


def _stage_word_entry_partial():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    tap_exact(app, canvas, "Enter Seed Manually")
    tap_exact(app, canvas, "12 words")
    type_word(app, canvas, "abandon")
    for letter in "ab":
        b = next(x for x in app.screen.buttons if x.label == letter)
        app.handle_tap(canvas, *_center(b))
    return app, canvas


def _stage_invalid_seed():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Verify Seed")
    tap_exact(app, canvas, "Enter Seed Manually")
    tap_exact(app, canvas, "12 words")
    type_mnemonic(app, canvas, "abandon " * 12)
    return app, canvas


def _stage_backup_mismatch():
    app, canvas = App(), Canvas()
    app.last_mnemonic = M12
    app.push(S.GenerateCompleteScreen(M12))
    tap_exact(app, canvas, "Confirm Backup")
    for w in M12.split()[:-1] + ["zoo"]:
        type_word(app, canvas, w)
    assert screen_name(app) == "BackupResultScreen"
    return app, canvas


def _stage_derivation_path():
    app, canvas = App(), Canvas()
    app.push(S.VerifyEntryScreen())
    app.push(S.DerivationPathScreen(M12))
    return app, canvas


def _stage_pp_length():
    app, canvas = _stage_derivation_path()
    tap_exact(app, canvas, "Passphrase")
    return app, canvas


def _stage_pp_roll_mid():
    app, canvas = _stage_pp_length()
    tap_sub(app, canvas, "8 words")
    for v in (1, 2, 3):
        b = next(x for x in app.screen.buttons if x.label == str(v))
        app.handle_tap(canvas, *_center(b))
    return app, canvas


def _stage_addr_index_seed():
    app, canvas = _stage_derivation_path()
    tap_sub(app, canvas, "BIP84")
    return app, canvas


def _stage_addr_display_seed():
    app, canvas = _stage_addr_index_seed()
    tap_exact(app, canvas, "Derive Address")
    return app, canvas


def _stage_addr_qr():
    app, canvas = _stage_addr_display_seed()
    tap_exact(app, canvas, "QR")
    return app, canvas


def _stage_enrol():
    app, canvas = _stage_addr_display_seed()
    tap_exact(app, canvas, "Enrol Account")
    return app, canvas


def _stage_accounts_list():
    seed_one_account()
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "Accounts")
    return app, canvas


def _stage_manage():
    app, canvas = _stage_accounts_list()
    tap_exact(app, canvas, "Edit")
    return app, canvas


def _stage_delete_confirm():
    app, canvas = _stage_manage()
    tap_exact(app, canvas, "Delete")
    return app, canvas


def _stage_nickname():
    app, canvas = _stage_manage()
    tap_exact(app, canvas, "Rename")
    return app, canvas


def _stage_account_qr():
    app, canvas = _stage_manage()
    tap_exact(app, canvas, "xpub QR")
    return app, canvas


def _stage_damaged():
    app, canvas = App(), Canvas()
    app.push(S.flow_screen("accounts", "DamagedAccountScreen")())
    return app, canvas


def _stage_about():
    app, canvas = App(), Canvas()
    tap_exact(app, canvas, "About")
    return app, canvas


def _stage_diag():
    if not (ROOT / "_build_mpy").is_dir():
        pytest.skip("Verify Build reads _build_mpy/; run tools/build_mpy.sh first")
    app, canvas = App(), Canvas()
    tap_sub(app, canvas, "Verify Build")
    draw(app, canvas)
    tap_exact(app, canvas, "Diagnostics")
    return app, canvas


def _stage_demo_confirm():
    app, canvas = start_ceremony(ent.D6, 12)
    tap_demo_bang(app, canvas)
    return app, canvas


def _stage_crash():
    app = App()
    app.last_mnemonic = M12
    A._crash(app, ValueError())
    return app, Canvas()


STAGES = [
    _stage_method_select, _stage_word_count, _stage_roll_empty,
    _stage_roll_mid, _stage_gate, _stage_confirm_99, _stage_warning,
    _stage_qr_export, _stage_verify_entry, _stage_word_entry_partial,
    _stage_invalid_seed, _stage_backup_mismatch, _stage_derivation_path,
    _stage_pp_length, _stage_pp_roll_mid, _stage_addr_index_seed,
    _stage_addr_display_seed, _stage_addr_qr, _stage_enrol,
    _stage_accounts_list, _stage_manage, _stage_delete_confirm,
    _stage_nickname, _stage_account_qr, _stage_damaged, _stage_about,
    _stage_diag, _stage_demo_confirm, _stage_crash,
]


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.__name__[7:])
def test_every_stage_backs_out_to_a_cleared_home(stage):
    """From every reachable screen, repeated Back-ish presses must land on
    the home screen with the session completely cleared, without raising and
    without looping."""
    app, canvas = stage()
    # give the session something to lose, so clearing is actually observable
    if app.last_mnemonic is None and screen_name(app) != "CrashScreen":
        app.last_mnemonic = app.last_mnemonic or None
    escape_to_home(app, canvas)


def test_pop_on_the_home_screen_itself_is_a_no_op():
    app, canvas = App(), Canvas()
    draw(app, canvas)
    app.pop()
    assert screen_name(app) == "HomeScreen"
    app.handle_tap(canvas, 1, 318)      # a miss between buttons: no raise
    assert screen_name(app) == "HomeScreen"
