"""SeedSigner-compatible SeedQR encoding, self-contained.

Two formats, both defined by SeedSigner's SeedQR spec
(https://github.com/SeedSigner/seedsigner/blob/dev/docs/seed_qr/README.md):

  * CompactSeedQR -- the raw BIP39 entropy bytes, nothing else, as a
    binary-mode QR: 16 bytes (12 words) -> version 1 (21x21), 32 bytes
    (24 words) -> version 2 (25x25). This is the primary format here: it is
    the densest encoding, and it is exactly the bytes the dice ceremony
    produces, so nothing about the wordlist has to be in memory to build it.
  * Standard SeedQR -- each word's wordlist index as a zero-padded 4-digit
    decimal, concatenated, as a numeric-mode QR: 48 digits (12 words) ->
    version 2, 96 digits (24 words) -> version 3 (29x29). Kept because some
    readers (and humans punching indices into a wordlist card) only speak it.

Both use error-correction level L, as SeedSigner does: a seed QR is read
once, close up, from a lit screen -- burst-error tolerance buys nothing and
costs symbol size.

No third-party QR library exists for stock MicroPython, so this implements
the sliver of ISO/IEC 18004 the device actually needs: byte, numeric and
alphanumeric modes, versions 1-6, level L. Versions 1-5 at level L are each
a SINGLE Reed-Solomon block; version 6 (two equal blocks) is the one place
codeword interleaving exists, added because an account xpub is 111 base58
characters and byte mode needs 113 data codewords -- past version 5's 108.
Versions 7+ and levels M/Q/H are deliberately not supported; the version
table is the contract, and asking for anything else raises.

Beyond the two SeedQR formats, two general encoders exist for the PUBLIC
values the UI shows as QRs (receive addresses, account xpubs):
alphanumeric_qr() for uppercase-safe strings (bech32 addresses, uppercased:
~45% fewer bits than byte mode, so a smaller symbol on a 240px panel) and
byte_qr() for anything mixed-case (base58 addresses, xpubs). Both pick the
smallest version that fits.

Correctness note: a QR that scans but decodes to the wrong bytes is a lost
wallet. Every path through here is verified against independent decoders on
the desktop (tests/test_qr_encoder.py drives zxing-cpp and zbar over many
random entropies); do not modify this file without re-running those tests.
"""

# ---------------------------------------------------------------------------
# GF(256) arithmetic (primitive polynomial 0x11D), for Reed-Solomon.
# Tables cost 768 bytes of RAM, built once at import.

_EXP = bytearray(512)
_LOG = bytearray(256)


def _init_tables():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    # doubled so _EXP[_LOG[a] + _LOG[b]] never needs a % 255
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_ecc(data, degree):
    """`degree` Reed-Solomon error-correction codewords over `data`.

    Nayuki's formulation: build the monic divisor polynomial (leading term
    implicit), then run polynomial division keeping only the remainder.
    """
    divisor = bytearray(degree)
    divisor[-1] = 1
    root = 1
    for _ in range(degree):
        for j in range(degree):
            divisor[j] = _gf_mul(divisor[j], root)
            if j + 1 < degree:
                divisor[j] ^= divisor[j + 1]
        root = _gf_mul(root, 2)
    result = bytearray(degree)
    for b in data:
        factor = b ^ result[0]
        for j in range(degree - 1):
            result[j] = result[j + 1]
        result[degree - 1] = 0
        for j in range(degree):
            result[j] ^= _gf_mul(divisor[j], factor)
    return result


# ---------------------------------------------------------------------------
# Version table: the whole contract. size, total data codewords, ecc
# codewords PER BLOCK, block count -- all at level L. Blocks are equal-sized
# at every version here (136/2 = 68 for V6), which _interleave relies on.

_VER = {
    1: (21, 19, 7, 1),
    2: (25, 34, 10, 1),
    3: (29, 55, 15, 1),
    4: (33, 80, 20, 1),
    5: (37, 108, 26, 1),
    6: (41, 136, 18, 2),
}

# The single alignment-pattern centre for V2-V6 (V1 has none). Versions 2-6
# have exactly one pattern that does not collide with a finder.
_ALIGN = {1: 0, 2: 18, 3: 22, 4: 26, 5: 30, 6: 34}


class _Bits:
    """MSB-first bit accumulator, byte-backed."""

    def __init__(self):
        self.buf = bytearray()
        self.n = 0

    def put(self, val, length):
        for i in range(length - 1, -1, -1):
            if self.n & 7 == 0:
                self.buf.append(0)
            if (val >> i) & 1:
                self.buf[self.n >> 3] |= 0x80 >> (self.n & 7)
            self.n += 1


def _interleave(version, data):
    """Data codewords + ECC, in transmission order.

    For a single block (versions 1-5 at L) this is simply data followed by
    its ECC -- exactly what the code always emitted. Version 6 is two equal
    blocks, and the spec transmits them column-wise: data codeword i of every
    block in turn, then ECC codeword i of every block in turn.
    """
    _, ncw, necc, nblk = _VER[version]
    if nblk == 1:
        return bytes(data) + bytes(_rs_ecc(data, necc))
    k = ncw // nblk                 # equal blocks at every supported version
    blocks = [data[i * k:(i + 1) * k] for i in range(nblk)]
    eccs = [_rs_ecc(b, necc) for b in blocks]
    out = bytearray()
    for i in range(k):
        for b in blocks:
            out.append(b[i])
    for i in range(necc):
        for e in eccs:
            out.append(e[i])
    return bytes(out)


def _finish(version, bits):
    """Terminator, byte padding, pad codewords, ECC. Returns (size, matrix)
    with matrix a flat bytearray of size*size modules, 1 = dark."""
    size, ncw, necc, _ = _VER[version]
    cap = ncw * 8
    if bits.n > cap:
        raise ValueError("payload does not fit version %d" % version)
    take = cap - bits.n
    bits.put(0, 4 if take > 4 else take)     # terminator
    if bits.n & 7:
        bits.put(0, 8 - (bits.n & 7))        # align to codeword boundary
    i = 0
    while bits.n < cap:                      # alternating pad codewords
        bits.put(0x11 if i & 1 else 0xEC, 8)
        i += 1
    return _build(version, _interleave(version, bits.buf))


def _smallest_version(nbits):
    """Smallest supported version whose data capacity holds `nbits`.

    Valid only for bitstreams whose count-indicator width is the version 1-9
    one -- true of every mode here, since nothing past version 6 exists.
    """
    for v in range(1, 7):
        if nbits <= _VER[v][1] * 8:
            return v
    raise ValueError("payload too large for the supported versions (1-6)")


def data_codewords(version, bits):
    """The padded data-codeword stream, exposed for the byte-level format
    tests -- the matrix hides it, and the spec is about these bytes."""
    size, ncw, necc, _ = _VER[version]
    cap = ncw * 8
    take = cap - bits.n
    bits.put(0, 4 if take > 4 else take)
    if bits.n & 7:
        bits.put(0, 8 - (bits.n & 7))
    i = 0
    while bits.n < cap:
        bits.put(0x11 if i & 1 else 0xEC, 8)
        i += 1
    return bytes(bits.buf)


# ---------------------------------------------------------------------------
# Public API

def compact_bits(entropy):
    """The CompactSeedQR bitstream: byte mode, count, raw entropy bytes."""
    b = _Bits()
    b.put(4, 4)                  # mode: byte
    b.put(len(entropy), 8)       # count indicator is 8 bits for versions 1-9
    for byte in entropy:
        b.put(byte, 8)
    return b


def compact_seedqr(entropy):
    """CompactSeedQR: the raw entropy bytes as a binary-mode QR.

    16 bytes -> (21, matrix), 32 bytes -> (25, matrix). Anything else is a
    hard error: entropy of the wrong length is not a seed, and padding or
    truncating it here would encode a DIFFERENT wallet that still scans.
    """
    if len(entropy) not in (16, 32):
        raise ValueError("entropy must be 16 or 32 bytes, got %d" % len(entropy))
    return _finish(1 if len(entropy) == 16 else 2, compact_bits(entropy))


def seedqr_digits(indices):
    """Standard SeedQR payload: each index as a zero-padded 4-digit decimal."""
    out = ""
    for i in indices:
        if not 0 <= i <= 2047:
            raise ValueError("word index %d out of range" % i)
        out += "%04d" % i
    return out


def standard_bits(digits):
    """The Standard SeedQR bitstream: numeric mode over the digit string."""
    b = _Bits()
    b.put(1, 4)                  # mode: numeric
    b.put(len(digits), 10)       # count indicator is 10 bits for versions 1-9
    i = 0
    while i + 3 <= len(digits):
        b.put(int(digits[i:i + 3]), 10)
        i += 3
    rem = len(digits) - i
    if rem == 2:
        b.put(int(digits[i:]), 7)
    elif rem == 1:
        b.put(int(digits[i:]), 4)
    return b


def standard_seedqr(indices):
    """Standard SeedQR: BIP39 word indices as 4-digit decimals, numeric mode.

    12 words -> (25, matrix), 24 words -> (29, matrix).
    """
    if len(indices) not in (12, 24):
        raise ValueError("need 12 or 24 word indices, got %d" % len(indices))
    return _finish(2 if len(indices) == 12 else 3, standard_bits(seedqr_digits(indices)))


# The ISO 18004 alphanumeric charset, index = value. Uppercase only, by
# design of the spec -- which is why bech32 addresses (case-insensitive,
# usually shown lowercase) are uppercased before encoding and base58
# (case-significant) can never use this mode.
_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def alnum_bits(s):
    """Alphanumeric-mode bitstream: pairs of chars in 11 bits, odd tail in 6."""
    b = _Bits()
    b.put(2, 4)                  # mode: alphanumeric
    b.put(len(s), 9)             # count indicator is 9 bits for versions 1-9
    vals = []
    for ch in s:
        v = _ALNUM.find(ch)
        if v < 0:
            raise ValueError("char not in the QR alphanumeric set")
        vals.append(v)
    i = 0
    while i + 2 <= len(vals):
        b.put(vals[i] * 45 + vals[i + 1], 11)
        i += 2
    if i < len(vals):
        b.put(vals[i], 6)
    return b


def alphanumeric_qr(s):
    """`s` as an alphanumeric-mode QR, smallest version (1-6) that fits.

    Raises on any char outside the 45-char uppercase set rather than falling
    back to byte mode: a silent fallback would let a lowercase slip produce a
    symbol that scans fine but is a different string, and the caller -- not
    this function -- knows whether the value survives case-folding.
    """
    bits = alnum_bits(s)
    return _finish(_smallest_version(bits.n), bits)


def byte_qr(data):
    """`data` (bytes) as a byte-mode QR, smallest version (1-6) that fits.

    This is the mixed-case path: base58 addresses and xpubs, where case is
    payload. An xpub (111 chars, 113 data codewords) is what forced version
    6 into the table.
    """
    return _finish(_smallest_version(4 + 8 + 8 * len(data)), compact_bits(data))


# ---------------------------------------------------------------------------
# Matrix construction

def _function_patterns(val, fun, size, version):
    def setf(r, c, dark):
        i = r * size + c
        val[i] = 1 if dark else 0
        fun[i] = 1

    # finders with their light separators (the -1..7 overscan is the separator)
    for fr, fc in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(-1, 8):
            rr = fr + r
            if rr < 0 or rr >= size:
                continue
            for c in range(-1, 8):
                cc = fc + c
                if cc < 0 or cc >= size:
                    continue
                dark = (0 <= r <= 6 and (c == 0 or c == 6)) or \
                       (0 <= c <= 6 and (r == 0 or r == 6)) or \
                       (2 <= r <= 4 and 2 <= c <= 4)
                setf(rr, cc, dark)
    # timing
    for i in range(8, size - 8):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    # the one alignment pattern these versions have
    ac = _ALIGN[version]
    if ac:
        for r in range(-2, 3):
            for c in range(-2, 3):
                m = max(r if r >= 0 else -r, c if c >= 0 else -c)
                setf(ac + r, ac + c, m != 1)
    # reserve the format-information modules (written per-mask later) and
    # place the always-dark module
    for i in range(9):
        if not fun[8 * size + i]:
            setf(8, i, False)
        if not fun[i * size + 8]:
            setf(i, 8, False)
    for i in range(1, 8):
        setf(size - i, 8, False)
    for i in range(8):
        setf(8, size - 1 - i, False)
    setf(size - 8, 8, True)


def _place_data(val, fun, size, cw):
    """ISO 18004 zigzag: column pairs from the right, skipping the timing
    column, alternating up/down. Codeword bits MSB first; leftover modules
    (the spec's remainder bits) stay light."""
    total = len(cw) * 8
    bit_i = 0
    inc = -1
    row = size - 1
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        while True:
            for c in (col, col - 1):
                i = row * size + c
                if not fun[i]:
                    dark = 0
                    if bit_i < total:
                        dark = (cw[bit_i >> 3] >> (7 - (bit_i & 7))) & 1
                        bit_i += 1
                    val[i] = dark
            row += inc
            if row < 0 or row >= size:
                row -= inc
                inc = -inc
                break
        col -= 2


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _format_bits(mask):
    """15-bit format information for level L and this mask: 5 data bits,
    10-bit BCH remainder, XORed with the fixed spec mask."""
    data = (1 << 3) | mask       # level L is 0b01
    rem = data << 10
    for i in range(14, 9, -1):
        if (rem >> i) & 1:
            rem ^= 0x537 << (i - 10)
    return ((data << 10) | rem) ^ 0x5412


def _place_format(val, size, bits):
    for i in range(15):
        mod = (bits >> i) & 1
        # copy 1, around the top-left finder
        if i < 6:
            val[i * size + 8] = mod
        elif i < 8:
            val[(i + 1) * size + 8] = mod
        else:
            val[(size - 15 + i) * size + 8] = mod
        # copy 2 (i < 8): under the top-right finder. The rest is copy 1's
        # horizontal run: (8,7) for bit 8 -- NOT (8,6), which is a timing
        # module -- then columns 5..0 for bits 9..14.
        if i < 8:
            val[8 * size + size - 1 - i] = mod
        elif i < 9:
            val[8 * size + 7] = mod
        else:
            val[8 * size + 14 - i] = mod


def _penalty(val, size):
    """The four ISO mask-evaluation penalty rules. Only relative order
    between masks matters here; any mask decodes."""
    p = 0
    # N1: runs of 5+ same-colour modules, rows and columns
    for r in range(size):
        run_r = 1
        run_c = 1
        prev_r = val[r * size]
        prev_c = val[r]
        for i in range(1, size):
            v = val[r * size + i]
            if v == prev_r:
                run_r += 1
            else:
                if run_r >= 5:
                    p += run_r - 2
                run_r = 1
                prev_r = v
            v = val[i * size + r]
            if v == prev_c:
                run_c += 1
            else:
                if run_c >= 5:
                    p += run_c - 2
                run_c = 1
                prev_c = v
        if run_r >= 5:
            p += run_r - 2
        if run_c >= 5:
            p += run_c - 2
    # N2: 2x2 blocks of one colour
    for r in range(size - 1):
        for c in range(size - 1):
            v = val[r * size + c]
            if (v == val[r * size + c + 1]
                    and v == val[(r + 1) * size + c]
                    and v == val[(r + 1) * size + c + 1]):
                p += 3
    # N3: finder-lookalike 1011101 with 0000 on either side
    a = (1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0)
    b = (0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1)
    for r in range(size):
        for c in range(size - 10):
            row_a = row_b = col_a = col_b = True
            for k in range(11):
                vr = val[r * size + c + k]
                vc = val[(c + k) * size + r]
                if vr != a[k]:
                    row_a = False
                if vr != b[k]:
                    row_b = False
                if vc != a[k]:
                    col_a = False
                if vc != b[k]:
                    col_b = False
            if row_a or row_b:
                p += 40
            if col_a or col_b:
                p += 40
    # N4: dark-module proportion distance from 50%
    dark = 0
    for v in val:
        dark += v
    p += 10 * (abs(dark * 100 - 50 * size * size) // (5 * size * size))
    return p


def _build(version, cw):
    size = _VER[version][0]
    base = bytearray(size * size)
    fun = bytearray(size * size)
    _function_patterns(base, fun, size, version)
    _place_data(base, fun, size, cw)
    best = None
    best_pen = -1
    for m in range(8):
        cand = bytearray(base)
        pred = _MASKS[m]
        for r in range(size):
            off = r * size
            for c in range(size):
                if not fun[off + c] and pred(r, c):
                    cand[off + c] ^= 1
        _place_format(cand, size, _format_bits(m))
        pen = _penalty(cand, size)
        if best_pen < 0 or pen < best_pen:
            best = cand
            best_pen = pen
    return size, best
