#!/usr/bin/env bash
# Cross-compile device/ into a .mpy deploy tree under _build_mpy/.
#
# This step is NOT optional. Copying the raw .py tree to a CYD fails with
# "MemoryError: memory allocation failed" while compiling embit's 29KB BIP39
# wordlist on-device -- there simply isn't enough heap on an ESP32 to parse it.
# Cross-compiling moves that compile-time peak onto the build machine and
# shrinks the payload from ~285KB to ~91KB.
#
# Requires: pip install mpy-cross
# mpy-cross 1.27.0.post2 (the newest on PyPI) emits .mpy v6, which imports
# fine on MicroPython 1.28.0 firmware.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HERE/device"
BUILD="$HERE/_build_mpy"

# Provenance gate. build_mpy.sh compiles whatever is on disk, so an untracked
# or modified file under device/ ships to the board and gets attested by the
# manifest -- which makes "you can verify this device runs the code you read"
# false by however many bytes nobody committed. A reviewer cloning the repo
# cannot reproduce that manifest at all.
#
# SEEDWITNESS_ALLOW_DIRTY=1 skips this for development. Never set it for a
# build whose manifest you intend to publish.
if [ "${SEEDWITNESS_ALLOW_DIRTY:-0}" != "1" ]; then
  if ! git -C "$HERE" diff --quiet -- device/ tools/build_mpy.sh 2>/dev/null \
      || ! git -C "$HERE" diff --cached --quiet -- device/ tools/build_mpy.sh 2>/dev/null \
      || [ -n "$(git -C "$HERE" ls-files --others --exclude-standard -- device/ 2>/dev/null)" ]; then
    echo "REFUSING: device/ has uncommitted or untracked files." >&2
    echo "A published manifest must attest to code that is in the repository." >&2
    git -C "$HERE" status --short -- device/ tools/build_mpy.sh >&2
    echo "Set SEEDWITNESS_ALLOW_DIRTY=1 to build anyway (development only)." >&2
    exit 1
  fi
fi

mkdir -p "$BUILD"
rm -rf "${BUILD:?}"/* 2>/dev/null || true

cp -r "$SRC/embit" "$BUILD/embit"
cp -r "$SRC/seedwitness" "$BUILD/seedwitness"
cp -r "$SRC/mp_shims/hashlib" "$BUILD/hashlib"
cp "$SRC/mp_shims/hmac.py" "$SRC/mp_shims/pbkdf2.py" "$SRC/mp_shims/secp256k1.py" "$BUILD/"
cp "$SRC/main.py" "$BUILD/main.py"

# pytest leaves CPython bytecode inside device/; it must not reach the board
find "$BUILD" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Not used on MicroPython, and together they're ~70KB of the payload:
#   NOTICE.md            - docs
#   ctypes_secp256k1.py  - CPython-only backend; embit's dispatcher takes the
#                          `from secp256k1 import *` branch on MicroPython
#   ubip39.py            - needs a native `uembit` module from a custom
#                          firmware build, which this project deliberately avoids
rm -f "$BUILD/embit/NOTICE.md" \
      "$BUILD/embit/util/ctypes_secp256k1.py" \
      "$BUILD/embit/wordlists/ubip39.py"

# --- flash-backed wordlist -------------------------------------------------
# As a Python list the wordlist is ~65KB of RAM, the app's single largest
# allocation on a ~164KB heap. Emit it as a fixed-width binary instead and
# swap embit/wordlists/bip39.py for a reader over that file.
#
# The binary is generated FROM embit's own wordlist here, at build time, so
# the shipped words cannot drift from the vendored source. The generator also
# asserts the two properties the reader depends on: every word fits
# WORD_WIDTH, and the list is sorted (binary search assumes it).
python - "$SRC" "$BUILD" <<'PYGEN'
import sys, pathlib
src, build = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
sys.path.insert(0, str(src))
from embit.wordlists.bip39 import WORDLIST

words = list(WORDLIST)
WIDTH = 8
assert len(words) == 2048, "expected 2048 BIP39 words, got %d" % len(words)
assert all(len(w) <= WIDTH for w in words), "a word exceeds WORD_WIDTH=%d" % WIDTH
assert words == sorted(words), "wordlist is not sorted; binary search would break"
assert all(w.isascii() for w in words), "non-ASCII word would break fixed-width encoding"

blob = b"".join(w.encode("ascii").ljust(WIDTH, bytes([0])) for w in words)
assert len(blob) == 2048 * WIDTH
(build / "bip39_words.bin").write_bytes(blob)
print("  wordlist -> bip39_words.bin (%d bytes, %d words)" % (len(blob), len(words)))
PYGEN

# --- EFF Diceware wordlist, same flash-backed treatment ---------------------
# 7776 words at 10 bytes fixed width. Generated here from the checked-in source
# list so the shipped bytes cannot drift from the reviewable text file, with
# the properties passphrase.py depends on asserted rather than assumed.
python - "$SRC" "$BUILD" <<'PYEFF'
import sys, pathlib
src, build = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
words = [w.strip() for w in (src / "data" / "eff_large_wordlist.txt").read_text().splitlines() if w.strip()]
WIDTH = 10
assert len(words) == 7776, "EFF large list must be 7776 words, got %d" % len(words)
assert len(words) == 6 ** 5, "list length must equal 6**5 or the dice mapping is biased"
assert all(len(w) <= WIDTH for w in words), "a word exceeds WORD_WIDTH=%d" % WIDTH
assert all(w.isascii() and w.islower() for w in words), "non-ASCII or non-lowercase word"
assert len(set(words)) == len(words), "duplicate word: the mapping would not be uniform"
blob = b"".join(w.encode("ascii").ljust(WIDTH, bytes([0])) for w in words)
(build / "eff_words.bin").write_bytes(blob)
print("  diceware -> eff_words.bin (%d bytes, %d words)" % (len(blob), len(words)))
PYEFF

# --- brand wordmark, same flash-backed treatment ----------------------------
# Three 1-bit segments ("seed", "witness", italic motto) packed from the
# checked-in text art
# so the shipped bytes cannot drift from something a reviewer can read in a
# diff. The generator asserts every property the reader and the splash
# depend on, then unpacks its own output and compares it row-for-row with
# the source -- the fonts.bin round-trip discipline.
python - "$SRC" "$BUILD" <<'PYWM'
import sys, pathlib
src, build = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
# ';' comments, not '#': an art row whose first pixel is lit starts with '#'
lines = [l for l in (src / "data" / "wordmark.txt").read_text().splitlines()
         if not l.startswith(";")]
segs = []
i = 0
while i < len(lines):
    if not lines[i].strip():
        i += 1
        continue
    name, w, h = lines[i].split()
    w, h = int(w), int(h)
    rows = lines[i + 1:i + 1 + h]
    assert len(rows) == h, "%s: %d rows declared, %d present" % (name, h, len(rows))
    for r in rows:
        assert len(r) == w, "%s: row width %d != %d" % (name, len(r), w)
        assert set(r) <= {"#", "."}, "%s: invalid characters in art" % name
    assert any("#" in r for r in rows), "%s: segment has no ink" % name
    segs.append((name, w, h, rows))
    i += 1 + h
assert [s[0] for s in segs] == ["seed", "witness", "motto"], \
    "wordmark must be seed, witness and motto, in order"
assert segs[0][2] == segs[1][2], "wordmark segment heights differ"
total_w = sum(s[1] for s in segs[:2])
assert total_w <= 240, "wordmark is wider than the 240px panel"
assert segs[0][2] <= 80, "wordmark is implausibly tall for a splash"
assert segs[2][1] <= 224 and segs[2][2] <= 32, "motto does not fit the splash"
blob = bytearray(b"SWM1")
blob.append(len(segs))
for name, w, h, rows in segs:
    stride = (w + 7) // 8
    blob += bytes((w & 0xFF, w >> 8, h & 0xFF, h >> 8))
    for r in rows:
        packed = bytearray(stride)
        for x, ch in enumerate(r):
            if ch == "#":
                packed[x >> 3] |= 0x80 >> (x & 7)
        blob += packed
# round trip: unpack what was just packed and compare with the source art
off = 5
for name, w, h, rows in segs:
    assert (blob[off] | (blob[off+1] << 8), blob[off+2] | (blob[off+3] << 8)) == (w, h)
    off += 4
    stride = (w + 7) // 8
    for y in range(h):
        row = blob[off + y * stride: off + (y + 1) * stride]
        txt = "".join("#" if (row[x >> 3] >> (7 - (x & 7))) & 1 else "."
                      for x in range(w))
        assert txt == rows[y], "%s: round-trip mismatch at row %d" % (name, y)
    off += stride * h
(build / "wordmark.bin").write_bytes(bytes(blob))
print("  wordmark -> wordmark.bin (%d bytes, %d segments, %dx%d + motto, round-tripped)"
      % (len(blob), len(segs), total_w, segs[0][2]))
PYWM

cp "$SRC/mp_shims/wordlist_flash/flash_wordlist.py" "$BUILD/flash_wordlist.py"
cp "$SRC/mp_shims/wordlist_flash/bip39.py" "$BUILD/embit/wordlists/bip39.py"
# the staged copy of mp_shims/wordlist_flash/ itself must not ship
rm -rf "$BUILD/wordlist_flash"

# --- Spleen glyph data, same flash-backed treatment --------------------------
# The three generated font modules hold 8,576 bytes of glyph rows, but
# importing them as .mpy cost ~25KB of heap (MicroPython copies every bytes
# object into RAM and charges module overhead on top) -- the margin the
# Generate Seed OOM lived in. Emit the same bytes as one binary and swap the
# data modules for readers over it. The Font class and both rasterisers are
# untouched, and this generator PROVES the pixels cannot change: it executes
# the exact reader module the board will run against the file just written
# and compares every glyph of every face byte-for-byte with the desktop
# modules the sim renders from.
python - "$SRC" "$BUILD" <<'PYFONTS'
import sys, pathlib
src, build = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
sys.path.insert(0, str(src))
from seedwitness.ui.fonts import spleen_8x16, spleen_12x24, spleen_16x32

faces = (spleen_8x16, spleen_12x24, spleen_16x32)  # order IS the face() index
blob = bytearray(b"SWF1")
blob.append(len(faces))
for m in faces:
    assert 0 < m.WIDTH < 256 and 0 < m.HEIGHT < 256 and 0 < m.ROW_BYTES < 256
    assert m.CHARS.isascii() and len(set(m.CHARS)) == len(m.CHARS)
    assert len(m.CHARS) < 65536
    assert len(m.DATA) == len(m.CHARS) * m.HEIGHT * m.ROW_BYTES, \
        "DATA length disagrees with CHARS x cell size"
    blob += bytes((m.WIDTH, m.HEIGHT, m.ROW_BYTES,
                   len(m.CHARS) & 0xFF, len(m.CHARS) >> 8))
    blob += m.CHARS.encode("ascii")
    blob += m.DATA
out = build / "fonts.bin"
out.write_bytes(bytes(blob))

reader_path = src / "mp_shims" / "fonts_flash" / "flash_fonts.py"
reader_src = reader_path.read_text()
needle = '_PATH = "/fonts.bin"'
assert needle in reader_src, "flash_fonts.py's path constant changed shape"
ns = {}
exec(compile(reader_src.replace(needle, "_PATH = %r" % str(out)),
             str(reader_path), "exec"), ns)
for i, m in enumerate(faces):
    w, h, rb, chars, data = ns["face"](i)
    assert (w, h, rb, chars) == (m.WIDTH, m.HEIGHT, m.ROW_BYTES, m.CHARS), \
        "face %d header round-trip failed" % i
    gb = h * rb
    assert len(data) == len(m.DATA)
    for g in range(len(chars)):
        assert bytes(data[g * gb:(g + 1) * gb]) == m.DATA[g * gb:(g + 1) * gb], \
            "glyph %r of face %d differs after round-trip" % (chars[g], i)
print("  fonts -> fonts.bin (%d bytes, %d faces, round-tripped)"
      % (len(blob), len(faces)))
PYFONTS

cp "$SRC/mp_shims/fonts_flash/flash_fonts.py" "$BUILD/flash_fonts.py"
cp "$SRC/mp_shims/fonts_flash/spleen_8x16.py" \
   "$SRC/mp_shims/fonts_flash/spleen_12x24.py" \
   "$SRC/mp_shims/fonts_flash/spleen_16x32.py" \
   "$BUILD/seedwitness/ui/fonts/"

cd "$BUILD"
# main.py stays source: the ESP32 port only auto-runs a file with that exact name.
while IFS= read -r f; do
  if ! out=$(python -m mpy_cross "$f" 2>&1) || [ -n "$out" ]; then
    echo "mpy-cross FAILED on $f" >&2
    echo "$out" >&2
    exit 1
  fi
done < <(find . -name '*.py' ! -name 'main.py')
find . -name '*.py' ! -name 'main.py' -delete

# boot.py is PINNED, not exempted, and that distinction is the point.
#
# MicroPython runs boot.py on every reset, before main.py, and -- critically --
# also on the soft reset mpremote performs when it enters the raw REPL. So it
# executes DURING a verification run. While it sat on the runtime-exemption
# list it was unhashed executable code that ran first and was blessed by the
# checker: a payload written there changed no hashed byte, and both
# verify_device.py and the on-device fingerprint reported PASS.
#
# The content below is stock MicroPython's own template, byte for byte, so a
# freshly flashed board already matches. Shipping it makes any modification a
# MISMATCH instead of an invisible foothold.
#
# Rule this encodes: an exemption may never cover something that can execute.
# Runtime records (enrolled accounts and bounded diagnostics) are exempt because
# a manifest cannot contain per-unit data. Code is pinned. tests/test_attest.py
# enforces this so an executable file cannot enter the exemption list.
cat > "$BUILD/boot.py" <<'BOOTPY'
# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()
BOOTPY

echo "build OK: $(find . -type f | wc -l) files"
