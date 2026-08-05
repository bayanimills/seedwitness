"""MicroPython stdlib compatibility glue -- import this before anything else
touches embit or hashlib. Keeps the actual gap-filling code (mp_shims/) and
the vendored packages (embit/) untouched and independently
auditable/upgradable; this is the one place that wires them together.

device/mp_shims/ (hashlib/, hmac.py, pbkdf2.py, secp256k1.py) exists ONLY for
the on-device MicroPython filesystem -- it is deliberately kept out of
desktop CPython's import path (tests/, sim/ only add `device/` itself, never
`device/mp_shims/`) so desktop testing exercises the REAL system hashlib and
embit's own CPython-native secp256k1 backend, not our MicroPython shims.
On the real board, main.py's boot sequence puts device/embit/,
device/seedwitness/, and the flattened contents of device/mp_shims/ all at
the filesystem root, exactly mirroring how this was verified locally with
MICROPYPATH=device/mp_shims:device.

Gaps mp_shims/ fills, each verified against a real MicroPython 1.22
interpreter + known BIP39/BIP32 test vectors (see device_tests/):
  - hashlib.pbkdf2_hmac: doesn't exist in MicroPython's hashlib at all
    (CPython-only convenience function). embit.bip39.mnemonic_to_seed()
    calls it directly. mp_shims/pbkdf2.py implements it by hand.
  - `import secp256k1`: embit expects a compiled native module on
    MicroPython; mp_shims/secp256k1.py redirects that to embit's own
    pure-Python fallback and patches its one MicroPython incompatibility
    (bytes doesn't support extended-step slicing). That patch is
    self-contained and applies itself automatically the moment
    `import secp256k1` runs -- nothing to do here for that one.
  - hashlib itself lacks sha512 on stock MicroPython; mp_shims/hashlib/ is
    the official micropython-lib hashlib-core + hashlib-sha512 packages,
    vendored unmodified.

On desktop CPython, none of this is needed or applied: hashlib.pbkdf2_hmac
already exists natively, and embit's own dispatcher (embit/misc.py) picks a
CPython-appropriate secp256k1 backend automatically.
"""
import sys

if sys.implementation.name == "micropython":
    import hashlib

    from pbkdf2 import pbkdf2_hmac

    hashlib.pbkdf2_hmac = pbkdf2_hmac
