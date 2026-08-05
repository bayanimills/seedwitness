"""Makes `import secp256k1` succeed on stock MicroPython with no compiled
native secp256k1 module present.

embit/misc.py branches on sys.implementation.name: on MicroPython it does a
bare `import secp256k1`, expecting a custom firmware build with a native C
secp256k1 module compiled in (the "proper" way projects like Krux do this,
for speed). We don't have that -- this is stock MicroPython on an
off-the-shelf ESP32 board, no custom firmware toolchain. So this top-level
module exists purely so that `import secp256k1` resolves to something:
embit's own pure-Python fallback implementation (embit/util/py_secp256k1.py),
which embit already ships specifically for this situation on non-MicroPython
platforms -- misc.py just doesn't try it automatically on MicroPython.

One patch is required on top of the pure-Python fallback: MicroPython's
`bytes` type does not support extended-step slicing (`b[::-1]`) at all --
confirmed directly against a real MicroPython 1.22 interpreter, not assumed:

    >>> bytes(range(4))[::-1]
    NotImplementedError: only slices with step=1 (aka None) are supported

py_secp256k1.py's `_reverse64()` (used by every public-key
serialize/parse call) relies on exactly that. This module replaces it with a
manual reversal that produces identical output. Verified against multiple
real BIP39/BIP32 test vectors end-to-end (mnemonic -> seed -> derived
address, bit-for-bit matching known-correct values) under the actual
MicroPython interpreter. The shim itself is exercised by tests/test_mp_shims.py;
the published address vectors are exercised by tests/test_xpub_path.py.

Performance note: this is a pure-Python secp256k1 implementation running on
an interpreted VM -- a single BIP32 derivation took ~90ms on a modern desktop
CPU under this same MicroPython build, so it's the PBKDF2 step (pbkdf2.py)
that dominates wall-clock time, not this module.
"""
from embit.util.py_secp256k1 import *  # noqa: F401,F403
from embit.util import py_secp256k1 as _impl


def _reverse64(b):
    x = b[:32]
    y = b[32:]
    return (
        bytes(x[len(x) - 1 - i] for i in range(len(x)))
        + bytes(y[len(y) - 1 - i] for i in range(len(y)))
    )


_impl._reverse64 = _reverse64
