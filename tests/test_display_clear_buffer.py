"""Display.clear() must reuse one cached line buffer, not allocate per call.

The vendored driver allocated w*2*hlines bytes (3840 at 240x8) fresh on
every clear, and a full-screen clear opens every redraw in the app. On the
device's ~20KB post-import heap, whose largest contiguous run measures only
1-5KB, that recurring demand is the allocation every observed MemoryError
died on: the boot-time first draw, mid-session redraws, and the crash
screen itself (diag.txt from the field: "crash MemoryError" followed by
"boot HARD_RESET" -- the fail-safe reset, because even the CrashScreen
could not get its 3840 bytes). The local modification allocates once, at
__init__ time, and refills in place on colour change.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

# ili9341.py imports MicroPython-only modules at top level; give it inert
# stand-ins so the pure-Python clear() logic is testable on CPython.
sys.modules.setdefault("framebuf", types.SimpleNamespace(
    FrameBuffer=object, RGB565=1))
sys.modules.setdefault("micropython", types.SimpleNamespace(
    const=lambda x: x))

from seedwitness.drivers.ili9341 import Display, color565  # noqa: E402


def _display(width=240, height=320):
    d = Display.__new__(Display)  # skip __init__: no SPI on the desktop
    d.width = width
    d.height = height
    d.blocks = []
    d.block = lambda x0, y0, x1, y1, data: d.blocks.append(
        (x0, y0, x1, y1, data))
    return d


def test_clear_allocates_its_line_buffer_exactly_once():
    d = _display()
    d.clear()
    buf = d._clear_line
    assert len(buf) == 240 * 2 * 8
    d.clear()
    d.clear(color565(255, 176, 0))
    d.clear()
    assert d._clear_line is buf, (
        "clear() must reuse its one cached buffer, never reallocate: the "
        "per-call 3840-byte allocation is what every field MemoryError "
        "died on")


def test_clear_hands_the_cached_buffer_to_every_block_write():
    d = _display()
    d.clear()
    assert len(d.blocks) == 320 // 8
    assert all(b[4] is d._clear_line for b in d.blocks)


def test_colour_change_refills_the_buffer_in_place():
    d = _display()
    d.clear()  # black first, like Display.__init__
    c = color565(255, 176, 0)
    d.clear(c)
    hi, lo = (c >> 8) & 0xFF, c & 0xFF
    buf = d._clear_line
    assert all(buf[i] == hi and buf[i + 1] == lo
               for i in range(0, len(buf), 2)), "colour pattern wrong"
    # and back to black: the same buffer, zeroed again
    d.clear(0)
    assert d._clear_line is buf
    assert all(v == 0 for v in buf)


def test_repeat_clears_with_the_same_colour_skip_the_refill():
    d = _display()
    c = color565(10, 20, 30)
    d.clear(c)
    snapshot = bytes(d._clear_line)
    d.clear(c)
    assert bytes(d._clear_line) == snapshot


def test_prewarm_allocates_every_font_scratch_cell_at_boot():
    """run_on_hardware pre-warms the three faces' lazy RGB565 cells while
    boot still has contiguous heap; lazily they allocate at the first drawn
    glyph, which on the measured post-boot heap (largest run < 1280 bytes)
    is a MemoryError waiting for the worst moment."""
    sys.path.insert(0, str(ROOT / "sim"))
    from seedwitness.ui import app as A
    from seedwitness.ui import fonts
    A._prewarm_fonts()
    for f in (fonts.S, fonts.M, fonts.L):
        assert f._buf is not None, "face %dx%d cell not pre-warmed" % (
            f.width, f.height)
        assert len(f._buf) == f.width * f.height * 2


def test_run_on_hardware_wires_the_prewarm_before_the_loop():
    """Hardware-bound, so pin the wiring in source the way test_diag pins
    record_crash: the prewarm must run, and before the loop starts."""
    src = (ROOT / "device" / "seedwitness" / "ui" / "app.py").read_text(
        encoding="utf-8")
    body = src[src.index("def run_on_hardware"):]
    assert "_prewarm_fonts()" in body
    assert body.index("_prewarm_fonts()") < body.index("_main_loop(")
