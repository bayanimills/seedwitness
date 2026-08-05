"""Deployed over seedwitness/ui/fonts/spleen_8x16.py on the device only.

Upstream that module is 1,520 bytes of glyph rows as a bytes literal; here
the same names resolve to a flash-backed reader over /fonts.bin, so the
rows live on the filesystem rather than the heap. Same WIDTH/HEIGHT/
ROW_BYTES/CHARS/DATA surface, so ui/fonts/__init__.py and everything above
it are untouched. See mp_shims/fonts_flash/flash_fonts.py.
"""
from flash_fonts import face as _face

WIDTH, HEIGHT, ROW_BYTES, CHARS, DATA = _face(0)
