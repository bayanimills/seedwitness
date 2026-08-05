"""The device draws glyphs from /fonts.bin through mp_shims/fonts_flash/;
the sim and every screenshot draw from the generated spleen_* modules. These
tests prove the flash path answers byte-identically to the module path, on
the desktop, before a board ever sees it.

tools/build_mpy.sh holds the second half of the guarantee: at build time it
round-trips every glyph of the fonts.bin it just wrote through the shipped
reader source, so the generator and the reader cannot drift either.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

from seedwitness.ui import fonts  # noqa: E402

READER = ROOT / "device" / "mp_shims" / "fonts_flash" / "flash_fonts.py"
FACE_MODULES = (fonts._s, fonts._m, fonts._l)  # ladder order S, M, L


def build_blob(modules):
    """The documented /fonts.bin format, from the desktop modules. Kept in
    step with tools/build_mpy.sh's generator by that script's own build-time
    round-trip through the reader below -- a format drift fails the build
    before it can fail a device."""
    blob = bytearray(b"SWF1")
    blob.append(len(modules))
    for m in modules:
        assert len(m.DATA) == len(m.CHARS) * m.HEIGHT * m.ROW_BYTES
        blob += bytes((m.WIDTH, m.HEIGHT, m.ROW_BYTES,
                       len(m.CHARS) & 0xFF, len(m.CHARS) >> 8))
        blob += m.CHARS.encode("ascii")
        blob += m.DATA
    return bytes(blob)


def load_reader(binary_path):
    """Execute the shipped reader source with its one absolute-path constant
    redirected at a temp file; everything else is the code the board runs."""
    source = READER.read_text()
    original = '_PATH = "/fonts.bin"'
    assert original in source, "flash_fonts.py's path constant changed shape"
    source = source.replace(original, "_PATH = %r" % str(binary_path))
    namespace = {}
    exec(compile(source, str(READER), "exec"), namespace)
    return namespace


class _ShimModule:
    """What the deployed spleen_* shims do: unpack face() into the names
    Font.__init__ reads."""

    def __init__(self, face_tuple):
        (self.WIDTH, self.HEIGHT, self.ROW_BYTES,
         self.CHARS, self.DATA) = face_tuple


def test_flash_reader_round_trips_every_glyph(tmp_path):
    binary = tmp_path / "fonts.bin"
    binary.write_bytes(build_blob(FACE_MODULES))
    ns = load_reader(binary)

    for i, mod in enumerate(FACE_MODULES):
        ram = fonts.Font(mod)
        flash = fonts.Font(_ShimModule(ns["face"](i)))
        assert (flash.width, flash.height, flash.row_bytes, flash.chars) == \
            (ram.width, ram.height, ram.row_bytes, ram.chars)
        for ch in mod.CHARS:
            assert bytes(flash.rows(ch)) == bytes(ram.rows(ch)), \
                "glyph %r of face %d differs between flash and RAM" % (ch, i)
        # coverage answers must agree too: has() is what _fit_font walks
        assert flash.rows("\x01") is None and ram.rows("\x01") is None
        assert flash.has("abc 123") == ram.has("abc 123")
        assert flash.has("ABC") == ram.has("ABC")


def test_reader_reuses_one_buffer_per_face(tmp_path):
    """The point of the shim beyond residency: the draw path must not
    allocate per glyph on a heap that fragments and never compacts."""
    binary = tmp_path / "fonts.bin"
    binary.write_bytes(build_blob(FACE_MODULES))
    ns = load_reader(binary)
    _, _, _, chars, data = ns["face"](1)
    gb = FACE_MODULES[1].HEIGHT * FACE_MODULES[1].ROW_BYTES
    first = data[0:gb]
    second = data[gb:2 * gb]
    assert first is second, "glyph reads must land in the shared buffer"


def test_build_script_ships_the_swap():
    """The shims only matter if build_mpy.sh actually swaps them in; a
    removed swap would quietly ship the RAM-hungry modules again and no
    desktop test would notice."""
    script = (ROOT / "tools" / "build_mpy.sh").read_text(encoding="utf-8")
    for needle in ("fonts.bin", "fonts_flash/flash_fonts.py",
                   "fonts_flash/spleen_8x16.py", "fonts_flash/spleen_12x24.py",
                   "fonts_flash/spleen_16x32.py"):
        assert needle in script, "build_mpy.sh no longer ships %s" % needle


def test_reader_avoids_constructs_micropython_cannot_run():
    import io
    import tokenize

    kept = []
    with io.open(str(READER), "r", encoding="utf-8") as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    code = " ".join(kept)
    assert "dataclass" not in code, "MicroPython has no dataclasses module"
    assert "[::" not in code, "MicroPython bytes has no extended-step slicing"
    assert "f'" not in code and 'f"' not in code, "no f-strings in this codebase"
