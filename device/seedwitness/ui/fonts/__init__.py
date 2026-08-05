"""The device's type scale: three bitmap faces from the Spleen family
(BSD 2-Clause, see each generated module's header for full provenance),
plus MicroPython's built-in framebuf 8x8 which needs no asset at all.

    XS   8x8   framebuf built-in   dense secondary text: digests, About
                                   paragraphs, page indicators, footnotes
    S    8x16  Spleen              captions, hints, one-line secondary text
    M    12x24 Spleen              primary content: seed words, addresses,
                                   headers, button labels
    L    16x32 Spleen (subset)     hero values: roll digits, address index,
                                   checksum word, keyboard preview letter

Why bitmap faces instead of scaling the 8x8: integer-scaling an 8x8 glyph
adds size but no detail -- a 2x scale is 16x16 of the same 64 pixels, which
is exactly the "heavy blocky pixels" look. Spleen is hand-drawn at each of
these cell sizes, so a 12x24 glyph carries 4.5x the detail of a scaled-up
8x8. All faces are strictly monospaced, which keeps every piece of layout
math in this codebase the same shape it always was: width = len(s) * cell.

The L face deliberately covers only " +-0123456789a-z" (see
tools/gen_fonts.py): every hero value on this device is digits, lowercase,
or a sign. Full ASCII at 16x32 would cost 6080 bytes of RAM against the
subset's 2496, on a board with ~30KB free. Font.has() is how call sites
check coverage before choosing it.

Data size, calculated from the generated modules (len(DATA)):
    S 1520 B + M 4560 B + L 2496 B = 8576 B of glyph data,
plus one lazily-allocated RGB565 scratch cell per face on the device
(S 256 B, M 576 B, L 1024 B = 1856 B when all three have drawn).

ON THE DEVICE THE GLYPH DATA LIVES ON FLASH, NOT THE HEAP. Loading the
three modules as .mpy cost ~25KB of RAM for those 8,576 bytes (MicroPython
copies every bytes object into the heap and charges import overhead on
top), and that was the margin the Generate Seed OOM lived in. So
tools/build_mpy.sh swaps the three data modules for readers over a single
/fonts.bin (see mp_shims/fonts_flash/) exactly as it swaps the BIP39
wordlist. This file -- the Font class and the rasteriser -- ships
unchanged: the shims expose the same WIDTH/HEIGHT/ROW_BYTES/CHARS/DATA
names, with DATA answering rows()'s one-glyph slices from flash via a
preallocated buffer. The build round-trips every glyph through the shipped
reader against these modules, so the two sources cannot drift.

The same packed data drives BOTH renderers: the device rasterises it to
RGB565 in Font.get_letter() below, and sim/mock_canvas.py rasterises the
identical bytes to PIL pixels. There is no TrueType stand-in anywhere, so a
screenshot glyph and a panel glyph are the same pixels by construction.
"""

try:  # device: MicroPython
    from framebuf import FrameBuffer, RGB565
except ImportError:  # desktop CPython: get_letter() is never called there
    FrameBuffer = None

from . import spleen_8x16 as _s
from . import spleen_12x24 as _m
from . import spleen_16x32 as _l


def _swap(color):
    """RGB565 byte-swap: FrameBuffer stores 16-bit pixels little-endian but
    the panel wants big-endian -- same trick as canvas.py's _swap565."""
    return ((color & 0xFF) << 8) | ((color & 0xFF00) >> 8)


class Font:
    """Reader over a generated font data module.

    Doubles as the `font` object device/drivers/ili9341.py's draw_text()
    expects: get_letter() returns (buf, w, h) with buf an RGB565 big-endian
    row-major bitmap of the full fixed cell, exactly what Display.block()
    takes. The scratch buffer is allocated once per face and reused for
    every glyph -- block() writes synchronously over SPI, so the buffer is
    free again by the time the next letter needs it. Per-glyph allocation
    (what rdagger's XglcdFont does) would churn ~576 B per letter on a heap
    where Display.clear()'s own 5KB buffer has been seen to fail.
    """

    def __init__(self, mod):
        self.width = mod.WIDTH
        self.height = mod.HEIGHT
        self.chars = mod.CHARS
        self.row_bytes = mod.ROW_BYTES
        self.data = mod.DATA
        self.glyph_bytes = self.height * self.row_bytes
        self._buf = None
        self._fb = None

    def has(self, s):
        """Every char of `s` (newlines aside) covered by this face?"""
        for ch in s:
            if ch != "\n" and self.chars.find(ch) < 0:
                return False
        return True

    def rows(self, ch):
        """Packed row bytes for one glyph, or None if not covered.
        Row y is bytes [y*row_bytes : (y+1)*row_bytes], MSB of the first
        byte = leftmost pixel."""
        i = self.chars.find(ch)
        if i < 0:
            return None
        o = i * self.glyph_bytes
        return self.data[o:o + self.glyph_bytes]

    def get_letter(self, letter, color, background=0, landscape=False):
        """ili9341.Display.draw_text()-compatible glyph rasteriser (device
        only). A char outside the face renders as a blank cell rather than
        aborting: draw_text() stops the whole string on a zero-width
        return, and a visible gap is a far better failure than half a seed
        word silently missing."""
        w, h = self.width, self.height
        if self._buf is None:
            self._buf = bytearray(w * h * 2)
            self._fb = FrameBuffer(self._buf, w, h, RGB565)
        fb = self._fb
        fb.fill(_swap(background))
        g = self.rows(letter)
        if g is not None:
            fg = _swap(color)
            rb = self.row_bytes
            shift = rb * 8 - 1
            for y in range(h):
                bits = 0
                base = y * rb
                for b in range(rb):
                    bits = (bits << 8) | g[base + b]
                if not bits:
                    continue
                for x in range(w):
                    if (bits >> (shift - x)) & 1:
                        fb.pixel(x, y, fg)
        return self._buf, w, h


S = Font(_s)
M = Font(_m)
L = Font(_l)
