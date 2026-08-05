"""Deployed over seedwitness/ui/fonts/spleen_12x24.py on the device only.

Upstream that module is 4,560 bytes of glyph rows as a bytes literal; here
the same names resolve to a flash-backed reader over /fonts.bin. Same
surface, so ui/fonts/__init__.py is untouched. See
mp_shims/fonts_flash/flash_fonts.py.
"""
from flash_fonts import face as _face

WIDTH, HEIGHT, ROW_BYTES, CHARS, DATA = _face(1)
