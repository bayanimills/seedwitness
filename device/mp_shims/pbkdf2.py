"""PBKDF2-HMAC-SHA512, hand-implemented for MicroPython.

embit.bip39.mnemonic_to_seed() calls hashlib.pbkdf2_hmac("sha512", ...) --
a CPython standard-library convenience function with no MicroPython
equivalent (confirmed: stock hashlib has no pbkdf2_hmac at all, on any port).
There's also no ready-made pbkdf2 package in micropython-lib to vendor.

This implementation was verified, not assumed:
  - Output matches Python's real hashlib.pbkdf2_hmac() bit-for-bit for two
    independent BIP39 spec test vectors (see device_tests/).
  - It does NOT use micropython-lib's hmac.HMAC.copy() -- that method calls
    `self.__class__.__new__(self.__class__)`, and MicroPython does not
    support __new__ on plain user classes (confirmed directly: `Foo.__new__`
    raises AttributeError even for a trivial class). So HMAC's per-round key
    setup would otherwise be redone from scratch every single PBKDF2 round.
    Instead this precomputes the HMAC inner/outer padded-key hash state once
    via hashlib.sha512().copy() (which *does* work -- it's implemented with
    an ordinary constructor call, not __new__) and reuses it every round.
    Measured effect: this optimization roughly halved wall-clock time in
    testing (naive: ~10.5s, this version: ~5.5s, for 2048 rounds on a modern
    desktop CPU running this same MicroPython build).

Performance reality check: even optimized, 2048 rounds of HMAC-SHA512 in an
interpreted pure-Python loop is not fast. The production path measures about
575-618 seconds on the 240MHz CYD. The UI's animated "deriving..." screen and
progress callback below exist specifically because a static ten-minute frame
is indistinguishable from a hung device.
"""
import hashlib

_BLOCK_SIZE = 128  # sha512 block size in bytes
_HLEN = 64  # sha512 digest size in bytes


def _hmac_sha512_precompute(key):
    if len(key) > _BLOCK_SIZE:
        key = hashlib.sha512(key).digest()
    key = key + bytes(_BLOCK_SIZE - len(key))
    inner = hashlib.sha512(bytes(b ^ 0x36 for b in key))
    outer = hashlib.sha512(bytes(b ^ 0x5C for b in key))
    return inner, outer


def _hmac_sha512_round(inner, outer, msg):
    h_in = inner.copy()
    h_in.update(msg)
    h_out = outer.copy()
    h_out.update(h_in.digest())
    return h_out.digest()


# Progress hook, set via seedwitness.mnemonic.mnemonic_to_seed()'s `progress`
# argument (which saves/restores it around the call) rather than passed as an
# argument, because the only caller is embit's bip39.mnemonic_to_seed() and it
# invokes hashlib.pbkdf2_hmac() with a fixed positional signature. Threading a
# kwarg through would mean forking vendored embit; a module-level hook keeps
# embit untouched. Called as progress(completed_rounds, total_rounds).
progress_cb = None

# How often to report. 2048 rounds / 16 = 128 updates -- one every ~4.5s at
# the measured ~575s stretch. Was 64 (an update every ~18s), which is slower
# than the ~10s a person watches a still screen before deciding the device
# has hung -- and this project has real freeze reports born exactly that way.
# The UI's per-update repaint is a few small partial draws (see
# DerivingScreen.update_progress), so 4x the callbacks still costs well under
# a second across the whole stretch.
_PROGRESS_EVERY = 16


def pbkdf2_hmac(hash_name, password, salt, iterations, dklen):
    if hash_name != "sha512":
        raise ValueError("this implementation only supports sha512")
    if isinstance(password, str):
        password = password.encode("utf-8")

    inner, outer = _hmac_sha512_precompute(password)
    cb = progress_cb

    blocks_needed = -(-dklen // _HLEN)  # ceil division
    dk = b""
    for block_index in range(1, blocks_needed + 1):
        u = _hmac_sha512_round(inner, outer, salt + block_index.to_bytes(4, "big"))
        t = bytearray(u)
        for round_index in range(iterations - 1):
            u = _hmac_sha512_round(inner, outer, u)
            for i in range(_HLEN):
                t[i] ^= u[i]
            if cb is not None and round_index % _PROGRESS_EVERY == 0:
                # report across ALL blocks, not just this one, so a dklen
                # needing several blocks still yields a monotonic 0..total
                cb((block_index - 1) * iterations + round_index, blocks_needed * iterations)
        dk += bytes(t)
    if cb is not None:
        cb(blocks_needed * iterations, blocks_needed * iterations)
    return dk[:dklen]
