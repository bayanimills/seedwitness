"""The splash wordmark: a flash-backed 1-bit brand bitmap, blitted through
the one shared span decoder (theme.mask_row_spans) so the sim and the panel
cannot disagree about its shape, with a text fallback so a board missing
the asset still boots and still shows the import-boundary splash.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

import pytest  # noqa: E402
from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui import app as A  # noqa: E402
from seedwitness.ui import theme as th  # noqa: E402

BIN = ROOT / "_build_mpy" / "wordmark.bin"
TXT = ROOT / "device" / "data" / "wordmark.txt"
needs_build = pytest.mark.skipif(
    not BIN.exists(), reason="run tools/build_mpy.sh first")

AMBER = th.ACCENT
WHITE = th.FG


def _segments_from_text():
    lines = [l for l in TXT.read_text().splitlines() if not l.startswith(";")]
    segs = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        name, w, h = lines[i].split()
        w, h = int(w), int(h)
        segs.append((name, w, h, lines[i + 1:i + 1 + h]))
        i += 1 + h
    return segs


# ---------------------------------------------------------------------------
# The span decoder and the blit

def test_mask_row_spans_ignores_row_padding_bits():
    # one 10px row: bits 0..9 from the first two bytes, MSB first; the six
    # padding bits of byte 1 are set and must not become pixels
    data = bytes([0b10110000, 0b11111111])
    assert list(th.mask_row_spans(data, 10, 0)) == [(0, 1), (2, 2), (8, 2)]


def test_mask_row_spans_is_row_major():
    data = bytes([0b11000000, 0b00110000])   # two 8px rows
    assert list(th.mask_row_spans(data, 8, 0)) == [(0, 2)]
    assert list(th.mask_row_spans(data, 8, 1)) == [(2, 2)]


def test_blit_mask_paints_exactly_the_set_bits():
    data = bytes([0b10100000, 0b01010000])   # 4px wide, 2 rows
    c = MockCanvas()
    c.blit_mask(10, 20, 4, 2, data, WHITE, (10, 20, 30))
    expect = {(10, 20): WHITE, (12, 20): WHITE, (11, 21): WHITE, (13, 21): WHITE}
    for yy in range(2):
        for xx in range(4):
            want = expect.get((10 + xx, 20 + yy), (10, 20, 30))
            assert c.image.getpixel((10 + xx, 20 + yy)) == want


def test_blit_mask_refuses_offscreen_like_the_hardware():
    c = MockCanvas()
    c.blit_mask(th.WIDTH - 2, 0, 4, 2, bytes(2), WHITE, (0, 0, 0))
    assert c.image.getpixel((th.WIDTH - 1, 0)) == th.BG


# ---------------------------------------------------------------------------
# The built asset

@needs_build
def test_the_binary_matches_the_checked_in_art():
    """wordmark.bin must be exactly the packed form of wordmark.txt: the
    text file is the reviewable source of truth, and drift between the two
    is the failure the build-time round trip exists to prevent."""
    old = A.WORDMARK_FILE
    try:
        A.WORDMARK_FILE = str(BIN)
        segs = A._read_wordmark(A.WORDMARK_FILE)
    finally:
        A.WORDMARK_FILE = old
    src = _segments_from_text()
    assert [s[0] for s in src] == ["seed", "witness", "motto"]
    assert len(segs) == len(src)
    for (w, h, data), (name, sw, sh, rows) in zip(segs, src):
        assert (w, h) == (sw, sh), "%s: header disagrees with the art" % name
        for y in range(h):
            spans = list(th.mask_row_spans(data, w, y))
            txt = ["."] * w
            for x, run in spans:
                for i in range(run):
                    txt[x + i] = "#"
            assert "".join(txt) == rows[y], "%s row %d differs" % (name, y)


@needs_build
def test_the_splash_draws_the_wordmark_in_the_brand_colours(monkeypatch):
    monkeypatch.setattr(A, "WORDMARK_FILE", str(BIN))
    c = MockCanvas()
    A._splash_frame(c)
    px = c.image.load()
    amber = white = 0
    for y in range(th.HEIGHT):
        for x in range(th.WIDTH):
            if px[x, y] == AMBER:
                amber += 1
            elif px[x, y] == WHITE:
                white += 1
    # seed (amber) is 4 letters, witness (white) is 7: both present, white
    # carrying more ink
    assert amber > 200, "the amber seed segment is missing"
    assert white > amber, "the white witness segment is missing or short"


@needs_build
def test_the_wordmark_fits_the_panel_with_margin():
    segs = _segments_from_text()
    total_w = sum(s[1] for s in segs[:2])
    assert total_w <= th.WIDTH - 2 * 8
    assert segs[1][2] == segs[0][2]
    assert segs[0][2] <= 60
    assert segs[2][1] <= th.WIDTH - 2 * 8
    assert segs[2][2] <= 32


@needs_build
def test_the_splash_draws_the_italic_motto_at_the_bottom(monkeypatch):
    monkeypatch.setattr(A, "WORDMARK_FILE", str(BIN))
    c = MockCanvas()
    A._splash_frame(c)
    # The motto is the only ink in the bottom 30px, centred and muted.
    ink = [(x, y) for y in range(th.HEIGHT - 30, th.HEIGHT)
           for x in range(th.WIDTH)
           if c.image.getpixel((x, y)) == th.MUTED]
    assert ink
    assert min(x for x, _ in ink) > 40
    assert max(x for x, _ in ink) < th.WIDTH - 40


def test_a_missing_asset_falls_back_to_the_text_wordmark(monkeypatch):
    """The splash is a diagnostic boundary first and branding second: a
    board without wordmark.bin must still draw it, as text."""
    monkeypatch.setattr(A, "WORDMARK_FILE", "/nowhere/wordmark.bin")
    c = MockCanvas()
    A._splash_frame(c)
    ink = sum(1 for y in range(th.HEIGHT) for x in range(th.WIDTH)
              if c.image.getpixel((x, y)) != th.BG)
    assert ink > 500, "the fallback splash drew nothing"


def test_a_damaged_asset_falls_back_not_raises(tmp_path, monkeypatch):
    bad = tmp_path / "wordmark.bin"
    bad.write_bytes(b"SWM1\x02" + b"\xff" * 10)   # truncated garbage
    monkeypatch.setattr(A, "WORDMARK_FILE", str(bad))
    c = MockCanvas()
    A._splash_frame(c)   # must not raise
    bad.write_bytes(b"NOPE")
    A._splash_frame(c)   # must not raise


def test_golden_frame_mask_case_agrees_between_the_two_scripts():
    """The MASK case bytes are generated by expression on both sides; if the
    expressions drift the comparison silently tests different pictures."""
    sys.path.insert(0, str(ROOT / "tools"))
    import golden_frame as host
    host_data = host.mask_case_data()
    dev_src = (ROOT / "device_tests" / "golden_frame.py").read_text()
    assert "(i * 73 + 41) & 0xFF" in dev_src
    assert host_data == bytes((i * 73 + 41) & 0xFF
                              for i in range(((host.MASK_W + 7) // 8) * host.MASK_H))
