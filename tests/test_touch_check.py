"""The touch check has to tell three different faults apart.

This release ships one calibration profile measured on one board, so a unit
that disagrees with it produces taps landing away from where they were
pressed. From every other screen that is indistinguishable from a dead touch
controller, because raw_touch() range-checks against the calibration and
returns None outside it. This screen is the only place the device can say
which of the two it is, so what it reports is the whole point of it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui.app import App  # noqa: E402
import pytest  # noqa: E402

# TouchCheckScreen does not exist yet. This file is the surviving specification
# for it: the implementation was uncommitted work in ui/flow_info.py and
# ui/canvas.py, discarded by a hard reset to origin/main on 2026-08-27, and it
# was never committed to any branch. The spec is kept because the screen is the
# only place the device can tell a miscalibrated panel from a dead controller.
#
# This guard is self-healing. Implement the screen and the spec starts running;
# nothing here needs editing.
try:
    from seedwitness.ui.flow_info import DiagScreen, TouchCheckScreen  # noqa: E402
except ImportError:  # pragma: no cover
    pytest.skip(
        "TouchCheckScreen not implemented; spec retained",
        allow_module_level=True,
    )


class RecordingCanvas(MockCanvas):
    def __init__(self):
        super().__init__()
        self.texts = []

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        self.texts.append(s)
        return super().text(x, y, s, color, bg=bg, scale=scale, font=font)


def _press(screen, canvas, x, y):
    canvas.tap_at(x, y)
    screen.handle_tap(App(), canvas, x, y)


def test_it_reports_the_raw_pair_not_just_the_mapped_point():
    """The mapped point alone cannot distinguish 'outside the profile' from
    'controller not answering' -- both land nowhere near the target."""
    screen, canvas = TouchCheckScreen(), RecordingCanvas()
    _press(screen, canvas, 36, 86)
    screen.draw(App(), canvas)
    assert any(t.startswith("raw") for t in canvas.texts)
    assert any(t.startswith("hit") for t in canvas.texts)
    assert any(t.startswith("off") for t in canvas.texts)


def test_the_offset_is_measured_against_the_target_that_was_live():
    """Advancing to the next target must not move the goalposts for the press
    that just happened, or every reading would be nonsense."""
    screen, canvas = TouchCheckScreen(), RecordingCanvas()
    tx, ty = screen.TARGETS[0]
    _press(screen, canvas, tx + 3, ty - 2)
    screen.draw(App(), canvas)
    assert any("  +3,  -2" in t for t in canvas.texts), canvas.texts


def test_an_unpressed_screen_says_so_rather_than_showing_zeros():
    """A readout of 0,0 would look like a perfect hit nobody made."""
    screen, canvas = TouchCheckScreen(), RecordingCanvas()
    screen.draw(App(), canvas)
    assert any("no press yet" in t for t in canvas.texts)
    assert not any(t.startswith("off") for t in canvas.texts)


def test_targets_stay_clear_of_the_readout_and_the_back_button():
    """A crosshair a finger has to reach through text, or through Back, is a
    crosshair the user cannot be sure they hit."""
    screen = TouchCheckScreen()
    back = screen.buttons[0]
    for cx, cy in screen.TARGETS:
        assert cy + screen.ARM < back.y, "target overlaps Back"
        assert cy + screen.ARM < 212, "target overlaps the readout block"
        assert 0 < cx - screen.ARM and cx + screen.ARM < 240


def test_back_survives_a_badly_offset_panel():
    """Every other press on this screen is swallowed as a measurement. If Back
    were swallowed too, a unit whose taps land 40px out would strand the user
    on the one screen that diagnoses it."""
    screen, canvas = TouchCheckScreen(), MockCanvas()
    app = App()
    app.push(screen)
    depth = len(app.stack)
    b = screen.buttons[0]
    screen.handle_tap(app, canvas, b.x + b.w // 2, b.y + b.h // 2)
    assert len(app.stack) == depth - 1


def test_the_raw_pair_is_unfiltered_by_the_calibration_window():
    """raw_touch() returns None outside the calibrated bounds, which is what
    makes a mis-profiled unit and a dead controller look alike. The number
    this screen shows must come from the unfiltered read instead.

    Read as text: canvas.py imports framebuf and the drivers, so it cannot be
    imported on desktop CPython.
    """
    src = (ROOT / "device" / "seedwitness" / "ui" / "canvas.py").read_text()
    body = src.split("def raw_sample", 1)[1].split("def touch_active", 1)[0]
    assert "send_command" in body, "must read the controller directly"
    assert "raw_touch" not in body.split('"""')[-1], (
        "raw_sample must not go through raw_touch's range check: that check "
        "is the thing being diagnosed around")


def test_the_vendored_touch_driver_is_still_unmodified():
    """xpt2046.py claims to be vendored unmodified. The touch check needed an
    unfiltered read and got it in canvas.py instead of quietly making that
    claim false."""
    src = (ROOT / "device" / "seedwitness" / "drivers" / "xpt2046.py").read_text()
    assert "Vendored unmodified" in src
    assert "raw_sample" not in src


def test_it_is_reachable_from_diagnostics():
    screen = DiagScreen()
    assert any(b.label == "Touch Check" for b in screen.buttons)
