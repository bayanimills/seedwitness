"""Colors as plain (r, g, b) tuples -- platform-agnostic. HardwareCanvas
converts to RGB565 for the ili9341 driver; the desktop mock (sim/) hands
these straight to PIL. Same palette as this project's first (Pi/Waveshare)
iteration, carried over since it read well on a small screen.

CHAR_W/CHAR_H describe MicroPython framebuf's built-in 8x8 font, the XS rung
of the type scale (dense secondary text: digests, About paragraphs,
footnotes). Everything a user actually reads carefully renders in the real
bitmap faces in ui/fonts/ (8x16, 12x24, 16x32 -- see that package's
docstring for the full scale and the RAM costings); those faces are also
strictly monospaced, so all layout stays "width = len(s) * cell" either way.
"""

# Portrait, 240 wide x 320 tall.
#
# The CYD's USB/power connector sits on a SHORT edge of the panel, so holding
# the device with the cable pointing up -- and therefore out of the way of the
# hands holding it -- means the screen is taller than it is wide. The UI was
# originally laid out landscape (320x240), which put the cable out of a side
# and, worse, meant the whole layout was rotated 90 degrees from how the
# device is actually held. Portrait is also the panel's native orientation.
#
# Nearly all layout maths below and in screens.py derives from these two
# constants, so most of it adapted automatically; the exceptions (the on-screen
# keyboard's key size, address line wrapping) are noted at their call sites.
WIDTH = 240
HEIGHT = 320
CHAR_W = 8
CHAR_H = 8

# Black framework: everything sits on unlit pixels, and controls are defined
# by their OUTLINE rather than by a filled panel.
#
# The previous scheme put mid-tone text on mid-tone grey buttons -- amber
# (247,147,26) on (30,30,34) -- which is legible on a desktop mockup and poor
# on a 2.8in panel at arm's length. Filling a button grey costs contrast for
# every character inside it and buys only a rectangle you could draw with a
# border instead. So buttons are black too, and the border does the work.
#
# Everything below is chosen for luminance separation against black, not for
# how the palette looks as a set.
BG = (0, 0, 0)              # unlit pixels, the darkest the panel can go
BUTTON_BG = (0, 0, 0)       # buttons are black; the border defines them
BUTTON_BORDER = (96, 96, 104)   # bright enough to read as an edge on black

FG = (255, 255, 255)        # pure white: maximum contrast for body text
MUTED = (150, 150, 158)     # secondary text. Raised from (120,120,128) --
                            # dim grey that reads fine on a light mock
                            # disappears on a black panel in daylight.
ACCENT = (255, 176, 0)      # amber. Brighter than the old bitcoin orange
                            # (247,147,26) and further from red, so it stays
                            # legible as TEXT and not just as a fill.
GOOD = (60, 220, 130)
WARN = (255, 90, 70)
ENTROPY_BAR = (80, 150, 255)
CHECKSUM_BAR = (255, 176, 0)

# Alternating tint for chunked strings (the address, read four characters at a
# time). Deliberately NOT GOOD: green is this app's verdict colour, and the
# one screen where a wrong answer costs real money is the address comparison.
# Striping it in the same green that means MATCH two screens later trains the
# eye to read "green = verified" on a string nobody has verified yet. This is
# a neutral blue, far enough from both FG white and GOOD green to chunk the
# text without asserting anything about it.
GROUP_ALT = (110, 175, 255)

# Standing-state edge: something is stored on this device right now.
# Deliberately NOT WARN. WARN is an alarm and means "you are about to get this
# wrong"; an enrolled account is not a mistake, it is a condition the owner
# chose and should keep in mind, because every enrolled xpub is a permanent
# privacy exposure sitting in flash. Muted enough to read as a state rather
# than an error, distinct enough from BUTTON_BORDER grey to notice at a
# glance without looking for it.
STATE_EDGE = (170, 80, 80)


def wrap_text(text, max_chars_per_line):
    words = text.split(" ")
    lines = []
    cur = ""
    for w in words:
        # a single token longer than the whole line budget (e.g. a long
        # derivation path or hex string with no spaces) can't be wrapped by
        # word-splitting -- hard-break it, else it would silently overflow
        # the canvas the same way the RollEntryScreen preview text did
        while len(w) > max_chars_per_line:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:max_chars_per_line])
            w = w[max_chars_per_line:]
        trial = (cur + " " + w).strip()
        if len(trial) <= max_chars_per_line:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def mask_row_spans(data, w, y):
    """Horizontal (x, run_w) spans of set bits in row y of a packed 1-bit
    bitmap: row-major, MSB first, each row padded to a whole byte. Padding
    bits beyond w are ignored.

    Lives here for the same reason circle_spans does: both canvases must
    rasterise a mask IDENTICALLY, so the one definition of which bits are
    set sits in the module they both already import, and each canvas only
    decides how to paint the spans it is handed (fill_rect rows in the sim,
    an RGB565 row pushed through block() on the device).
    """
    stride = (w + 7) >> 3
    base = y * stride
    x = 0
    while x < w:
        if (data[base + (x >> 3)] >> (7 - (x & 7))) & 1:
            run = 1
            while x + run < w and (
                    data[base + ((x + run) >> 3)] >> (7 - ((x + run) & 7))) & 1:
                run += 1
            yield (x, run)
            x += run
        else:
            x += 1


def circle_spans(cx, cy, r):
    """Horizontal (x, y, w) spans covering a filled circle.

    Lives here, in the one module both canvases already import, because the
    two of them must rasterise a circle IDENTICALLY. Writing the loop twice --
    PIL's ellipse on the desktop, the driver's Bresenham fill_circle on the
    board -- would give two different pixel patterns for the same call, and
    the golden-frame test would (correctly) start failing with no way to make
    the two agree short of reimplementing one of them anyway.

    Integer arithmetic only, no sqrt: float rounding is exactly the kind of
    thing that differs between CPython and MicroPython, and this has to be
    bit-identical, not approximately equal. r is single digits here, so the
    incremental widening costs nothing worth measuring.
    """
    r2 = r * r
    for dy in range(-r, r + 1):
        w = 0
        while (w + 1) * (w + 1) + dy * dy <= r2:
            w += 1
        yield (cx - w, cy + dy, 2 * w + 1)
