"""The 99th D6 roll is an explicit interoperability decision."""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402
from seedwitness import entropy as ent  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


ROLLS_99 = ("123456" * 16) + "123"
SEEDSIGNER_ENTROPY = bytes.fromhex(
    "5588d3630bd19f6375b7bd922457af34"
    "ea9c74f00807566a1cf808e445dc8c20"
)
SEEDSIGNER_MNEMONIC = (
    "few educate sugar bless boring random strategy waste mutual cargo type "
    "hawk prefer denial scan abstract filter extend dignity balcony dust "
    "unusual correct bubble"
)

# SeedSigner's own published 24-word dice-verification example. Keeping their
# external vector beside the synthetic one above means compatibility is not
# asserted only against an expected value produced from our own implementation.
# https://github.com/SeedSigner/seedsigner/blob/1fb2956322ea978428a6a96b955baa93e965c877/docs/dice_verification.md
PUBLISHED_ROLLS_99 = (
    "6551522313165213216113315444412361646644311215344156335264562544622455"
    "46236542364246312613322234612"
)
PUBLISHED_MNEMONIC = (
    "eyebrow obvious such suggest poet seven breeze blame virtual frown "
    "dynamic donor harsh pigeon express broccoli easy apology scatter force "
    "recipe shadow claim radio"
)


def session_at_99():
    session = ent.RollSession(ent.D6, 24)
    for face in ROLLS_99:
        session.add_roll(int(face))
    return session


def app_with_roll_screen(session):
    app = App()
    app.push(S.MethodSelectScreen())
    app.push(S.WordCountScreen(ent.D6))
    roll_screen = S.RollEntryScreen(session)
    app.push(roll_screen)
    return app, roll_screen


class HeldCanvas(MockCanvas):
    """A touch that can lift before the standard sweep completes."""

    def __init__(self, hold_for=99):
        super().__init__()
        self.hold_for = hold_for
        self.polls = 0

    def touch_active(self):
        self.polls += 1
        return self.polls <= self.hold_for


def tap(screen, app, canvas, button):
    screen.handle_tap(app, canvas,
                      button.x + button.w // 2,
                      button.y + button.h // 2)


def test_fixed_vector_matches_seedsigners_sha256_bip39_algorithm():
    session = session_at_99()
    assert session.is_seedsigner_compatible
    assert session.seedsigner_entropy() == SEEDSIGNER_ENTROPY
    assert hashlib.sha256(ROLLS_99.encode()).digest() == SEEDSIGNER_ENTROPY

    from seedwitness import mnemonic as mn
    assert mn.entropy_to_mnemonic(session.seedsigner_entropy()) == SEEDSIGNER_MNEMONIC


def test_matches_seedsigners_published_99_roll_example():
    session = ent.RollSession(ent.D6, 24)
    for face in PUBLISHED_ROLLS_99:
        session.add_roll(int(face))

    assert len(PUBLISHED_ROLLS_99) == 99
    assert session.is_seedsigner_compatible
    from seedwitness import mnemonic as mn
    assert mn.entropy_to_mnemonic(session.seedsigner_entropy()) == PUBLISHED_MNEMONIC


@pytest.mark.parametrize("source,words,count", [
    (ent.D6, 24, 98),
    (ent.D6, 24, 100),
    (ent.D6, 12, 99),
    (ent.D8, 24, 99),
])
def test_compatibility_bypass_is_available_only_at_exact_boundary(source, words, count):
    session = ent.RollSession(source, words)
    session.rolls = [1] * count
    assert not session.is_seedsigner_compatible
    with pytest.raises(RuntimeError):
        session.seedsigner_entropy()


def test_roll_99_opens_gate_without_finalising():
    session = ent.RollSession(ent.D6, 24)
    session.rolls = [int(x) for x in ROLLS_99[:-1]]
    app, roll_screen = app_with_roll_screen(session)

    roll_screen._make_roll_handler(int(ROLLS_99[-1]))(app)

    assert type(app.screen).__name__ == "SeedSignerRollGateScreen"
    assert app.last_mnemonic is None
    assert len(session.rolls) == 99


def test_use_99_finishes_with_the_seedsigner_vector():
    session = session_at_99()
    app, roll_screen = app_with_roll_screen(session)
    gate = S.SeedSignerRollGateScreen(session)
    app.push(gate)

    gate._choose_99(app)
    assert type(app.screen).__name__ == "RollCountConfirmScreen"
    assert app.last_mnemonic is None
    app.screen._confirm(app)

    # This synthetic vector is deliberately periodic, so the newly wired
    # entropy advisory must interrupt before the known mnemonic is revealed.
    assert type(app.screen).__name__ == "EntropyWarningScreen"
    app.screen._continue(app)

    assert type(app.screen).__name__ == "EntropyCapturedScreen"
    assert app.screen.entropy == SEEDSIGNER_ENTROPY
    assert app.last_mnemonic == SEEDSIGNER_MNEMONIC
    assert len(session.rolls) == 99


def test_add_100th_returns_to_roll_entry_then_makes_a_different_seed():
    session = session_at_99()
    app, roll_screen = app_with_roll_screen(session)
    gate = S.SeedSignerRollGateScreen(session)
    app.push(gate)

    gate._choose_100th(app)
    assert type(app.screen).__name__ == "RollCountConfirmScreen"
    assert app.screen.use_99 is False
    app.screen._confirm(app)
    assert app.screen is roll_screen
    roll_screen._make_roll_handler(6)(app)

    assert type(app.screen).__name__ == "EntropyWarningScreen"
    app.screen._continue(app)

    assert type(app.screen).__name__ == "EntropyCapturedScreen"
    assert len(session.rolls) == 100
    assert app.screen.entropy != SEEDSIGNER_ENTROPY
    assert app.last_mnemonic != SEEDSIGNER_MNEMONIC


def test_gate_copy_states_the_irreversible_choice():
    session = session_at_99()
    gate = S.SeedSignerRollGateScreen(session)

    class RecordingCanvas(MockCanvas):
        def __init__(self):
            super().__init__()
            self.strings = []

        def text(self, x, y, value, color, **kwargs):
            self.strings.append(value)
            return super().text(x, y, value, color, **kwargs)

    canvas = RecordingCanvas()
    gate.draw(App(), canvas)
    copy = " ".join(canvas.strings)
    assert "SeedSigner stops here" in copy
    assert "Roll 100 makes a new seed" in copy
    assert [b.label for b in gate.buttons] == [
        "Use 99: SeedSigner", "Add 100th Roll"]


def test_both_gate_choices_and_the_second_confirmation_require_a_hold():
    session = session_at_99()
    gate = S.SeedSignerRollGateScreen(session)
    confirm_99 = S.RollCountConfirmScreen(session, use_99=True)
    confirm_100 = S.RollCountConfirmScreen(session, use_99=False)

    assert gate.SWEEP_MS == S.MenuScreen.SWEEP_MS > 0
    assert confirm_99.SWEEP_MS == S.MenuScreen.SWEEP_MS > 0
    assert confirm_100.SWEEP_MS == S.MenuScreen.SWEEP_MS > 0
    assert all(button.on_tap is not None for button in gate.buttons)


@pytest.mark.parametrize("choice", [0, 1])
def test_releasing_either_gate_choice_early_does_not_select_it(choice):
    session = session_at_99()
    app, _ = app_with_roll_screen(session)
    gate = S.SeedSignerRollGateScreen(session)
    app.push(gate)

    tap(gate, app, HeldCanvas(hold_for=1), gate.buttons[choice])

    assert app.screen is gate
    assert len(session.rolls) == 99
    assert app.last_mnemonic is None


@pytest.mark.parametrize("use_99", [True, False])
def test_releasing_second_confirmation_early_changes_nothing(use_99):
    session = session_at_99()
    app, _ = app_with_roll_screen(session)
    gate = S.SeedSignerRollGateScreen(session)
    app.push(gate)
    confirmation = S.RollCountConfirmScreen(session, use_99=use_99)
    app.push(confirmation)

    tap(confirmation, app, HeldCanvas(hold_for=1), confirmation.buttons[0])

    assert app.screen is confirmation
    assert len(session.rolls) == 99
    assert app.last_mnemonic is None


def test_back_from_confirmation_changes_nothing():
    session = session_at_99()
    app, _ = app_with_roll_screen(session)
    gate = S.SeedSignerRollGateScreen(session)
    app.push(gate)
    gate._choose_99(app)

    confirmation = app.screen
    confirmation.buttons[1].on_tap(app)

    assert app.screen is gate
    assert len(session.rolls) == 99
    assert app.last_mnemonic is None
