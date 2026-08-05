#!/usr/bin/env python3
"""Generate the bitmap font data modules under device/seedwitness/ui/fonts/
and sim/font_petme128_8x8.py from their upstream sources.

Run: python tools/gen_fonts.py <dir with spleen-*.bdf and font_petme128_8x8.h>

Sources (fetched 2026-08-03):
  spleen-8x16.bdf, spleen-12x24.bdf, spleen-16x32.bdf
    from https://github.com/fcambus/spleen (v2.2.0, BSD 2-Clause,
    Copyright (c) 2018-2026 Frederic Cambus). Spleen is a monospaced
    bitmap font family drawn pixel-by-pixel at exactly these cell sizes --
    no rasterisation, no anti-aliasing, every glyph is the designer's own
    pixels. That is why it is used here instead of thresholding a TrueType
    face: at 12x24 a hinted-then-thresholded vector font is mud; a hand-
    drawn pixel font is crisp by construction.
  font_petme128_8x8.h
    from https://github.com/micropython/micropython extmod/ (MIT,
    Copyright (c) 2013-2014 Damien P. George). This is the EXACT glyph data
    compiled into MicroPython's framebuf.FrameBuffer.text() -- vendored for
    the desktop sim only, so screenshots show the same 8x8 glyphs the
    device draws instead of a TrueType stand-in that merely looks similar.
    The device itself never loads this module; framebuf has its own copy.

Output format (see device/seedwitness/ui/fonts/__init__.py for the reader):
  WIDTH, HEIGHT   fixed cell size in pixels (Spleen is strictly monospaced)
  CHARS           string of covered characters, ascending ASCII order
  ROW_BYTES       bytes per bitmap row (1 for 8px cells, 2 for 12/16px)
  DATA            HEIGHT*ROW_BYTES bytes per glyph, glyphs in CHARS order,
                  row-major top to bottom, MSB of byte 0 = leftmost pixel

The generator asserts every requested char exists in the source, that every
glyph's ink fits the fixed cell, and it records the sha256 of each source
file so the provenance of the shipped bytes is checkable.
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "device" / "seedwitness" / "ui" / "fonts"
SIM_DIR = ROOT / "sim"

PRINTABLE = "".join(chr(c) for c in range(32, 127))  # 95 glyphs
# The 16x32 face is a deliberate subset: it renders only the hero values
# (roll digits, address index, checksum word, keyboard preview letter), all
# of which are digits, lowercase, or +/-. Full ASCII would cost 6080 bytes
# of RAM; this subset costs 2496.
HERO_SUBSET = " +-0123456789abcdefghijklmnopqrstuvwxyz"

SPLEEN_LICENSE = """\
Spleen %s -- Copyright (c) 2018-2026, Frederic Cambus. BSD 2-Clause.
https://github.com/fcambus/spleen

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
 * Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE."""


def parse_bdf(path):
    """Parse a BDF into {codepoint: cell bitmap} of fixed cell size.

    Returns (cell_w, cell_h, {cp: [row_int, ...]}) where each row_int has
    bit (cell_w-1) as the leftmost pixel (i.e. left-aligned in cell_w bits).
    """
    text = path.read_text()
    m = re.search(r"^FONTBOUNDINGBOX (-?\d+) (-?\d+) (-?\d+) (-?\d+)$",
                  text, re.M)
    fw, fh, fx, fy = map(int, m.groups())
    glyphs = {}
    for block in text.split("STARTCHAR")[1:]:
        em = re.search(r"^ENCODING (-?\d+)$", block, re.M)
        cp = int(em.group(1))
        if cp < 0:
            continue
        bm = re.search(r"^BBX (-?\d+) (-?\d+) (-?\d+) (-?\d+)$", block, re.M)
        bw, bh, bx, by = map(int, bm.groups())
        bitmap = re.search(r"^BITMAP\n(.*?)^ENDCHAR", block, re.M | re.S)
        hex_rows = bitmap.group(1).split()
        assert len(hex_rows) == bh, "glyph %d: %d rows, BBX says %d" % (
            cp, len(hex_rows), bh)
        # place the BBX-sized ink into the fixed cell
        row0 = (fy + fh) - (by + bh)   # rows from cell top to ink top
        col0 = bx - fx                 # cols from cell left to ink left
        assert 0 <= row0 and row0 + bh <= fh, "glyph %d ink outside cell (y)" % cp
        assert 0 <= col0 and col0 + bw <= fw, "glyph %d ink outside cell (x)" % cp
        rows = [0] * fh
        src_bits = len(hex_rows[0]) * 4 if hex_rows else 0
        for i, hx in enumerate(hex_rows):
            v = int(hx, 16)
            # BDF rows are left-aligned in their hex bytes; realign the BBX
            # ink to the cell: shift into cell_w bits at column col0
            ink = v >> (src_bits - bw) if src_bits else 0
            rows[row0 + i] = ink << (fw - col0 - bw)
        glyphs[cp] = rows
    return fw, fh, glyphs


def pack(cell_w, cell_h, glyphs, chars):
    row_bytes = (cell_w + 7) // 8
    out = bytearray()
    for ch in chars:
        cp = ord(ch)
        assert cp in glyphs, "source font missing %r" % ch
        for row in glyphs[cp]:
            # left-align the cell_w bits into row_bytes*8
            out += (row << (row_bytes * 8 - cell_w)).to_bytes(row_bytes, "big")
    return row_bytes, bytes(out)


def emit_module(path, doc, cell_w, cell_h, row_bytes, chars, data):
    lines = [
        '"""%s"""' % doc,
        "",
        "WIDTH = %d" % cell_w,
        "HEIGHT = %d" % cell_h,
        "ROW_BYTES = %d" % row_bytes,
        "CHARS = %r" % chars,
        "DATA = (",
    ]
    per = cell_h * row_bytes
    for i in range(0, len(data), per):
        ch = chars[i // per]
        lines.append("    %r  # %r" % (data[i:i + per], ch))
    lines.append(")")
    lines.append("")
    path.write_text("\n".join(lines))
    print("wrote %s  (%d glyphs, %d data bytes)" % (path, len(chars), len(data)))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gen_spleen(src_dir, bdf_name, module_name, chars, role):
    src = src_dir / bdf_name
    cell_w, cell_h, glyphs = parse_bdf(src)
    row_bytes, data = pack(cell_w, cell_h, glyphs, chars)
    doc = (
        "%dx%d bitmap face (%s), generated by tools/gen_fonts.py. DO NOT EDIT.\n\n"
        "Source: %s from https://github.com/fcambus/spleen v2.2.0\n"
        "        sha256 %s\n"
        "Format: HEIGHT*ROW_BYTES bytes per glyph in CHARS order, row-major\n"
        "        top to bottom, MSB of each row's byte 0 = leftmost pixel.\n\n"
        "%s"
    ) % (cell_w, cell_h, role, bdf_name, sha(src),
         SPLEEN_LICENSE % ("%dx%d" % (cell_w, cell_h)))
    emit_module(FONT_DIR / (module_name + ".py"), doc,
                cell_w, cell_h, row_bytes, chars, data)


def gen_petme(src_dir):
    """MicroPython's built-in framebuf font, for the desktop sim only.

    Layout is petme128's own: 8 bytes per char for chars 32..127, each byte
    one COLUMN left to right, bit 0 = top pixel (see micropython
    extmod/font_petme128_8x8.h and framebuf's text() implementation).
    """
    src = src_dir / "font_petme128_8x8.h"
    text = src.read_text()
    # the comment for char 125 contains a literal "}", so find the array's
    # real terminator rather than the first closing brace
    body = text[text.index("{"):text.rindex("};")]
    vals = [int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{2})", body)]
    assert len(vals) == 96 * 8, "expected 96 chars * 8 bytes, got %d" % len(vals)
    data = bytes(vals)
    doc = (
        "MicroPython's built-in framebuf 8x8 font (petme128), generated by\n"
        "tools/gen_fonts.py. DO NOT EDIT. FOR THE DESKTOP SIM ONLY: the device\n"
        "renders this exact data via framebuf.FrameBuffer.text(); this copy\n"
        "exists so MockCanvas draws the same pixels instead of approximating\n"
        "with a TrueType face (which hid real layout truth from screenshots).\n\n"
        "Source: extmod/font_petme128_8x8.h, https://github.com/micropython/micropython\n"
        "        sha256 %s\n"
        "Format: 8 bytes per char for chars 32..127; each byte is one COLUMN\n"
        "        left to right; bit 0 = top pixel. Chars outside 32..127 render\n"
        "        as char 127, matching framebuf's own fallback.\n\n"
        "MIT License, Copyright (c) 2013, 2014 Damien P. George (from the\n"
        "MicroPython project, https://micropython.org)."
    ) % sha(src)
    lines = ['"""%s"""' % doc, "", "FIRST = 32", "LAST = 127", "DATA = ("]
    for i in range(0, len(data), 8):
        lines.append("    %r  # %r" % (data[i:i + 8], chr(32 + i // 8)))
    lines.append(")")
    lines.append("")
    out = SIM_DIR / "font_petme128_8x8.py"
    out.write_text("\n".join(lines))
    print("wrote %s  (%d glyphs, %d data bytes)" % (out, len(data) // 8, len(data)))


def main():
    src_dir = Path(sys.argv[1])
    FONT_DIR.mkdir(exist_ok=True)
    gen_spleen(src_dir, "spleen-8x16.bdf", "spleen_8x16", PRINTABLE,
               "captions and secondary single-line text")
    gen_spleen(src_dir, "spleen-12x24.bdf", "spleen_12x24", PRINTABLE,
               "primary content: seed words, addresses, headers, buttons")
    gen_spleen(src_dir, "spleen-16x32.bdf", "spleen_16x32", HERO_SUBSET,
               "hero values: roll digits, index value, checksum word")
    gen_petme(src_dir)


if __name__ == "__main__":
    main()
