"""The QR encoder is verified against an INDEPENDENT decoder, not itself.

A QR that scans but decodes to the wrong bytes is a lost wallet, and QR
encoding is full of places to be subtly wrong (bit order, mask application,
format-info placement, pad codewords). So the load-bearing tests here render
every symbol to pixels and hand it to zxing-cpp -- a decoder that shares no
code with device/seedwitness/qr.py -- and require byte-exact round trips
over many random entropies. The byte-level tests then pin the payload format
to SeedSigner's SeedQR spec directly, so a future decoder-tolerated drift
(say, a different mode indicator that still decodes) cannot creep in.
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

import pytest  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

import zxingcpp  # noqa: E402

from embit import bip39  # noqa: E402
from seedwitness import mnemonic as mn  # noqa: E402
from seedwitness import qr  # noqa: E402


def render(size, mat, scale=8, quiet=4):
    """Rasterise a matrix exactly as the device does conceptually: light
    background, dark modules, a real quiet zone."""
    img = Image.frombytes("L", (size, size), bytes(0 if v else 255 for v in mat))
    img = ImageOps.expand(img, border=quiet, fill=255)
    n = size + 2 * quiet
    return img.resize((n * scale, n * scale), Image.NEAREST)


def zxing_decode(size, mat):
    res = zxingcpp.read_barcode(render(size, mat))
    assert res is not None, "zxing-cpp could not decode the symbol at all"
    assert res.format == zxingcpp.BarcodeFormat.QRCode
    return res


# ---------------------------------------------------------------------------
# CompactSeedQR: byte-exact round trips through the independent decoder

def test_compact_round_trip_many_random_entropies():
    rng = random.Random(0xC0FFEE)
    for trial in range(150):
        n = 16 if trial % 2 == 0 else 32
        entropy = bytes(rng.randrange(256) for _ in range(n))
        size, mat = qr.compact_seedqr(entropy)
        got = zxing_decode(size, mat).bytes
        assert got == entropy, (
            "round-trip mismatch: encoded %s, zxing decoded %s"
            % (entropy.hex(), got.hex()))


def test_compact_round_trip_adversarial_entropies():
    """Byte patterns that historically shake out charset/pad bugs: all
    zeros, all 0xFF, ASCII-looking, UTF-8-looking lead bytes, 0xEC/0x11
    pad-codeword lookalikes."""
    cases = [
        bytes(16), bytes(32),
        b"\xff" * 16, b"\xff" * 32,
        b"seedwitness 0123",
        b"\xec\x11" * 8, b"\xec\x11" * 16,
        b"\xc3\xa9" * 8, b"\xe2\x82\xac\x00" * 8,
        bytes(range(16)), bytes(range(32)),
        b"\x80" + bytes(15), b"\x00" * 31 + b"\x80",
    ]
    for entropy in cases:
        size, mat = qr.compact_seedqr(entropy)
        assert zxing_decode(size, mat).bytes == entropy, entropy.hex()


def test_compact_symbol_sizes_match_the_seedqr_spec():
    """SeedSigner's spec: 12-word CompactSeedQR is a 21x21 (version 1),
    24-word is a 25x25 (version 2)."""
    size16, _ = qr.compact_seedqr(bytes(16))
    size32, _ = qr.compact_seedqr(bytes(32))
    assert size16 == 21
    assert size32 == 25


def test_compact_full_ceremony_equivalence():
    """entropy -> mnemonic (what the ceremony shows) -> mnemonic_to_entropy
    (what the export screen encodes) -> QR -> independent decode must land
    back on the same wallet, via embit, not via this codebase's encoder."""
    rng = random.Random(42)
    for n in (16, 32):
        for _ in range(20):
            entropy = bytes(rng.randrange(256) for _ in range(n))
            mnemonic = mn.entropy_to_mnemonic(entropy)
            size, mat = qr.compact_seedqr(mn.mnemonic_to_entropy(mnemonic))
            decoded = zxing_decode(size, mat).bytes
            assert decoded == entropy
            assert bip39.mnemonic_from_bytes(decoded) == mnemonic


def test_compact_rejects_non_seed_lengths():
    for n in (0, 1, 15, 17, 20, 24, 31, 33, 64):
        with pytest.raises(ValueError):
            qr.compact_seedqr(bytes(n))


# ---------------------------------------------------------------------------
# Standard SeedQR

def test_standard_round_trip_many_random_seeds():
    rng = random.Random(0x5EED)
    for trial in range(60):
        words = 12 if trial % 2 == 0 else 24
        indices = [rng.randrange(2048) for _ in range(words)]
        size, mat = qr.standard_seedqr(indices)
        text = zxing_decode(size, mat).text
        assert text == "".join("%04d" % i for i in indices)


def test_standard_symbol_sizes_match_the_seedqr_spec():
    """12 words = 48 digits -> 25x25 (version 2); 24 words = 96 digits ->
    29x29 (version 3)."""
    size12, _ = qr.standard_seedqr([0] * 12)
    size24, _ = qr.standard_seedqr([2047] * 24)
    assert size12 == 25
    assert size24 == 29


def test_standard_reconstructs_the_mnemonic_independently():
    """Decode the digits with zxing, look the indices up in embit's wordlist
    directly -- not through word_indices() -- and require the original
    mnemonic back. This closes the loop without trusting any code under
    test for the reverse direction."""
    rng = random.Random(99)
    for n in (16, 32):
        entropy = bytes(rng.randrange(256) for _ in range(n))
        mnemonic = mn.entropy_to_mnemonic(entropy)
        size, mat = qr.standard_seedqr(mn.word_indices(mnemonic))
        digits = zxing_decode(size, mat).text
        words = [bip39.WORDLIST[int(digits[i:i + 4])]
                 for i in range(0, len(digits), 4)]
        assert " ".join(words) == mnemonic


def test_standard_rejects_bad_input():
    with pytest.raises(ValueError):
        qr.standard_seedqr([0] * 11)
    with pytest.raises(ValueError):
        qr.standard_seedqr([0] * 13)
    with pytest.raises(ValueError):
        qr.standard_seedqr([2048] + [0] * 11)
    with pytest.raises(ValueError):
        qr.standard_seedqr([-1] + [0] * 11)


def test_word_indices_agree_with_the_wordlist():
    wl = list(bip39.WORDLIST)
    mnemonic = mn.entropy_to_mnemonic(bytes(range(16)))
    assert mn.word_indices(mnemonic) == [wl.index(w) for w in mnemonic.split()]
    with pytest.raises(ValueError):
        mn.word_indices("notaword " * 12)


# ---------------------------------------------------------------------------
# Byte-level format, pinned to the spec rather than to what decoders accept

def _stream_bits(buf, start, length):
    v = 0
    for i in range(start, start + length):
        v = (v << 1) | ((buf[i >> 3] >> (7 - (i & 7))) & 1)
    return v


def test_compact_bitstream_is_byte_mode_count_then_raw_bytes():
    """CompactSeedQR is: mode 0100, 8-bit count, the entropy bytes, nothing
    else before the terminator. This is the SeedSigner interop contract at
    the bit level."""
    for n in (16, 32):
        entropy = bytes(range(n))
        bits = qr.compact_bits(entropy)
        assert _stream_bits(bits.buf, 0, 4) == 0b0100, "not byte mode"
        assert _stream_bits(bits.buf, 4, 8) == n, "wrong count indicator"
        for i in range(n):
            assert _stream_bits(bits.buf, 12 + 8 * i, 8) == entropy[i]
        assert bits.n == 12 + 8 * n, "trailing bits before the terminator"


def test_standard_bitstream_is_numeric_mode_over_the_digits():
    """Numeric mode 0001, 10-bit count, digits packed 3-at-a-time into 10
    bits (48 and 96 both divide by 3, so no partial group ever occurs)."""
    indices = [1815, 0, 2047, 1, 1024, 7, 99, 500, 1000, 1500, 2000, 3]
    digits = qr.seedqr_digits(indices)
    assert digits == "".join("%04d" % i for i in indices)
    bits = qr.standard_bits(digits)
    assert _stream_bits(bits.buf, 0, 4) == 0b0001, "not numeric mode"
    assert _stream_bits(bits.buf, 4, 10) == len(digits)
    pos = 14
    for i in range(0, len(digits), 3):
        assert _stream_bits(bits.buf, pos, 10) == int(digits[i:i + 3])
        pos += 10


def test_padding_is_terminator_then_ec_11_codewords():
    """After the terminator and byte alignment, the spec's alternating pad
    codewords 0xEC 0x11 fill the data capacity. 16 bytes in version 1-L:
    19 data codewords, payload occupies 17.5, so codeword 18 onward is
    padding."""
    entropy = bytes(range(16))
    cw = qr.data_codewords(1, qr.compact_bits(entropy))
    assert len(cw) == 19
    # mode+count nibble boundary: 0x40 | n>>4, then the shifted payload
    assert cw[0] == 0x40 | (16 >> 4)
    # rebuild the whole codeword stream independently, bit by bit
    expect = bytearray()
    acc = 0
    nbits = 0
    for chunk, ln in [(0b0100, 4), (16, 8)] + [(b, 8) for b in entropy]:
        acc = (acc << ln) | chunk
        nbits += ln
        while nbits >= 8:
            expect.append((acc >> (nbits - 8)) & 0xFF)
            nbits -= 8
    if nbits:
        expect.append((acc << (8 - nbits)) & 0xFF)  # terminator zero-fill
    pads = (0xEC, 0x11)
    i = 0
    while len(expect) < 19:
        expect.append(pads[i & 1])
        i += 1
    assert cw == bytes(expect)


def test_symbols_decode_with_every_seed_length_and_extreme_indices():
    for indices in ([0] * 12, [2047] * 12, [0] * 24, [2047] * 24):
        size, mat = qr.standard_seedqr(indices)
        assert zxing_decode(size, mat).text == "".join("%04d" % i for i in indices)


def test_function_patterns_survive_format_placement():
    """Structural spec checks a decoder round trip can quietly forgive:
    format info is BCH-protected and duplicated, so a decoder still reads a
    symbol whose first copy has a wrong bit or whose timing pattern lost a
    module. This pins what the spec actually says: timing modules alternate,
    and the two format-information copies carry identical bits. (Bit 8 of
    copy 1 belongs at (8,7), not in the timing column at (8,6) -- a bug the
    decoder tests genuinely did not catch.)"""
    for size, mat in (qr.compact_seedqr(bytes(range(16))),
                      qr.compact_seedqr(bytes(range(32))),
                      qr.standard_seedqr(list(range(24)))):
        # timing row 6 and column 6 alternate, dark on even coordinates
        for i in range(8, size - 8):
            assert mat[6 * size + i] == (1 if i % 2 == 0 else 0), "timing row broken"
            assert mat[i * size + 6] == (1 if i % 2 == 0 else 0), "timing col broken"
        # dark module
        assert mat[(size - 8) * size + 8] == 1
        # extract both format copies at their spec positions, LSB first
        copy1 = []
        for i in range(15):
            if i < 6:
                copy1.append(mat[i * size + 8])
            elif i < 8:
                copy1.append(mat[(i + 1) * size + 8])
            elif i < 9:
                copy1.append(mat[8 * size + 7])
            else:
                copy1.append(mat[8 * size + 14 - i])
        copy2 = []
        for i in range(15):
            if i < 8:
                copy2.append(mat[8 * size + size - 1 - i])
            else:
                copy2.append(mat[(size - 15 + i) * size + 8])
        assert copy1 == copy2, "the two format-information copies disagree"
        # and the value must be a valid level-L format word for some mask
        v = 0
        for i, bit in enumerate(copy1):
            v |= bit << i
        assert v in [qr._format_bits(m) for m in range(8)], \
            "format info is not a valid level-L word"


def test_matrix_is_binary_and_the_right_shape():
    for size, mat in (qr.compact_seedqr(bytes(16)),
                      qr.compact_seedqr(bytes(32)),
                      qr.standard_seedqr([3] * 24)):
        assert len(mat) == size * size
        assert set(mat) <= {0, 1}
        # finder pattern corners: dark module at each of the three corners
        assert mat[0] == 1
        assert mat[size - 1] == 1
        assert mat[(size - 1) * size] == 1


# ---------------------------------------------------------------------------
# The public-value encoders (receive addresses, xpubs): alphanumeric mode and
# versions 4-6, including version 6's two-block interleave -- all held to the
# same standard as the seed formats: byte-exact round trips through zxing-cpp.

BECH32 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
TAPROOT = "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"
LEGACY = "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"


def _xpub():
    from seedwitness import derive as drv
    mnemonic = ("abandon abandon abandon abandon abandon abandon abandon "
                "abandon abandon abandon abandon about")
    t = [t for t in drv.PATH_TEMPLATES if t.bip == 84][0]
    _, xpub = drv.account_xpub(mnemonic, t)
    return xpub


def test_alphanumeric_addresses_round_trip():
    """Uppercased bech32 through the independent decoder, exactly."""
    for addr in (BECH32, TAPROOT):
        size, mat = qr.alphanumeric_qr(addr.upper())
        assert zxing_decode(size, mat).text == addr.upper()


def test_alphanumeric_buys_a_smaller_symbol_than_byte_mode():
    """The point of adding the mode: a 42-char address is version 2 in
    alphanumeric against version 3 in byte mode. If this stops holding, the
    mode is dead weight."""
    alnum_size, _ = qr.alphanumeric_qr(BECH32.upper())
    byte_size, _ = qr.byte_qr(BECH32.encode())
    assert alnum_size == 25
    assert byte_size == 29
    taproot_size, _ = qr.alphanumeric_qr(TAPROOT.upper())
    assert taproot_size == 29


def test_alphanumeric_rejects_anything_outside_the_set():
    """No silent byte-mode fallback: a lowercase slip must raise, because the
    caller -- not the encoder -- knows whether the value survives
    case-folding (bech32 does, base58 does not)."""
    for bad in ("bc1qlowercase", "MIXeDCASE", "UNDER_SCORE", "at@sign"):
        with pytest.raises(ValueError):
            qr.alphanumeric_qr(bad)


def test_alphanumeric_bitstream_matches_the_spec():
    """Mode 0010, 9-bit count, 45*c1+c2 in 11 bits per pair, odd tail in 6.
    Pinned at the bit level so decoder tolerance cannot mask drift."""
    s = "AC-42"
    bits = qr.alnum_bits(s)
    assert _stream_bits(bits.buf, 0, 4) == 0b0010
    assert _stream_bits(bits.buf, 4, 9) == 5
    tbl = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
    assert _stream_bits(bits.buf, 13, 11) == tbl.index("A") * 45 + tbl.index("C")
    assert _stream_bits(bits.buf, 24, 11) == tbl.index("-") * 45 + tbl.index("4")
    assert _stream_bits(bits.buf, 35, 6) == tbl.index("2")
    assert bits.n == 41


def test_byte_qr_round_trips_every_version_boundary():
    """Payload sizes straddling each version's capacity, so every version
    1-6 is exercised -- 107+ bytes lands in version 6, the only TWO-BLOCK
    symbol, whose interleave order only an independent decoder can vouch
    for."""
    expected_size = {1: 21, 17: 21, 18: 25, 32: 25, 33: 29, 53: 29,
                     54: 33, 78: 33, 79: 37, 106: 37, 107: 41, 134: 41}
    rng = random.Random(0xB17E)
    for n, want in expected_size.items():
        data = bytes(rng.randrange(256) for _ in range(n))
        size, mat = qr.byte_qr(data)
        assert size == want, "%d bytes -> %dx%d, expected %d" % (n, size, size, want)
        assert zxing_decode(size, mat).bytes == data, "%d bytes" % n


def test_byte_qr_too_large_raises():
    with pytest.raises(ValueError):
        qr.byte_qr(bytes(135))


def test_xpub_fits_version_six_and_round_trips():
    """The reason versions 4-6 exist at all: an account xpub is 111 base58
    chars (113 data codewords), which no single-block level-L version
    holds."""
    xpub = _xpub()
    assert len(xpub) == 111
    size, mat = qr.byte_qr(xpub.encode())
    assert size == 41                      # version 6
    assert zxing_decode(size, mat).text == xpub
