"""Entropy concerns are visible in the real roll ceremony, but advisory."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402
from seedwitness import entropy as ent  # noqa: E402
from seedwitness.ui import screens as S  # noqa: E402
from seedwitness.ui.app import App  # noqa: E402


def app_at_roll_screen(session):
    app = App()
    app.session = session
    app.push(S.MethodSelectScreen())
    app.push(S.WordCountScreen(session.source))
    screen = S.RollEntryScreen(session)
    app.push(screen)
    return app, screen


def test_obvious_pattern_warns_before_any_mnemonic_is_created():
    session = ent.RollSession(ent.D6, 12)
    session.rolls = [1] * 49
    app, screen = app_at_roll_screen(session)

    screen._make_roll_handler(1)(app)

    assert type(app.screen).__name__ == "EntropyWarningScreen"
    assert app.screen.concern.code == ent.CONCERN_ALL_SAME
    assert app.last_mnemonic is None
    assert len(session.rolls) == 50


def test_continue_is_explicit_but_does_not_block_the_users_ceremony():
    session = ent.RollSession(ent.D6, 12)
    session.rolls = [2] * 49
    app, screen = app_at_roll_screen(session)
    screen._make_roll_handler(2)(app)
    warning = app.screen

    warning._continue(app)

    assert type(app.screen).__name__ == "EntropyCapturedScreen"
    assert app.last_mnemonic == app.screen.mnemonic
    assert len(app.screen.entropy) == 16


def test_start_over_discards_every_roll_and_returns_to_entry():
    session = ent.RollSession(ent.D6, 12)
    session.rolls = [3] * 49
    app, screen = app_at_roll_screen(session)
    screen._make_roll_handler(3)(app)

    app.screen._restart(app)

    assert app.screen is screen
    assert session.rolls == []
    assert app.last_mnemonic is None
    assert not session.is_complete


def test_non_concerning_ceremony_keeps_the_direct_completion_path():
    # SeedSigner's published 99-roll vector is a useful fixed, non-patterned
    # input. Use its first 49 faces plus one more for a complete 12-word test.
    rolls = "65515223131652132161133154444123616466443112153441"
    assert len(rolls) == 50
    session = ent.RollSession(ent.D6, 12)
    session.rolls = [int(x) for x in rolls[:-1]]
    assert session.assess() is None
    app, screen = app_at_roll_screen(session)

    screen._make_roll_handler(int(rolls[-1]))(app)

    assert type(app.screen).__name__ == "EntropyCapturedScreen"


def test_warning_copy_is_advisory_and_controls_require_holds():
    session = ent.RollSession(ent.D6, 12)
    session.rolls = [1] * 50
    warning = S.EntropyWarningScreen(
        session, session.final_entropy(), session.assess())

    class RecordingCanvas(MockCanvas):
        def __init__(self):
            super().__init__()
            self.strings = []

        def text(self, x, y, value, color, **kwargs):
            self.strings.append(value)
            return super().text(x, y, value, color, **kwargs)

    canvas = RecordingCanvas()
    warning.draw(App(), canvas)
    copy = " ".join(canvas.strings)
    assert "Pattern noticed" in copy
    assert "does not prove bad dice" in copy
    assert [b.label for b in warning.buttons] == ["Use These Rolls", "Start Over"]
    assert warning.SWEEP_MS == S.MenuScreen.SWEEP_MS > 0
