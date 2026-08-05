"""Direct correctness tests for device/mp_shims/ -- the MicroPython-only
compat shims (pbkdf2.py, secp256k1.py's _reverse64 patch).

These modules are deliberately excluded from every OTHER desktop test's
sys.path (see compat.py's docstring: mp_shims/ must never shadow CPython's
real hashlib), but their own internal correctness is platform-independent
logic that's just as easy to verify by cross-checking against real CPython
primitives here -- this file is the one deliberate, documented exception,
and it adds mp_shims to sys.path only for itself, not globally.

This complements, not replaces, device_tests/run_all.py's end-to-end
pipeline test under the real MicroPython interpreter -- that one proves the
shims work in place under the actual target runtime; this one exhaustively
cross-checks pbkdf2.py's own math against hashlib.pbkdf2_hmac for cases the
end-to-end test doesn't exercise (varying dklen, iteration counts, the
non-sha512 error path).
"""
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device" / "mp_shims"))

from pbkdf2 import pbkdf2_hmac as shim_pbkdf2_hmac  # noqa: E402


def _pure_python_sha512():
    """Load the vendored SHA-512 even though CPython already has a native one.

    Give it a private package name so this test never shadows stdlib hashlib
    in the rest of the test process. ``const`` is a MicroPython builtin; put
    its no-op CPython equivalent directly in the module namespace.
    """
    package_name = "_seedwitness_sha512_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT / "device" / "mp_shims" / "hashlib")]
    sys.modules[package_name] = package

    sha_spec = importlib.util.spec_from_file_location(
        package_name + "._sha",
        ROOT / "device" / "mp_shims" / "hashlib" / "_sha.py",
    )
    sha_module = importlib.util.module_from_spec(sha_spec)
    sys.modules[sha_spec.name] = sha_module
    sha_spec.loader.exec_module(sha_module)

    sha512_spec = importlib.util.spec_from_file_location(
        package_name + "._sha512",
        ROOT / "device" / "mp_shims" / "hashlib" / "_sha512.py",
    )
    sha512_module = importlib.util.module_from_spec(sha512_spec)
    sha512_module.const = lambda value: value
    sys.modules[sha512_spec.name] = sha512_module
    sha512_spec.loader.exec_module(sha512_module)
    return sha512_module.sha512


def test_pure_python_sha512_ring_schedule_matches_native_hashlib():
    """Cross every padding/block boundary used by HMAC and PBKDF2.

    The device implementation deliberately retains only 16 of SHA-512's 80
    schedule words to bound peak heap use.  This pins that memory change to
    the standard algorithm rather than merely cross-checking PBKDF2 against
    itself.
    """
    sha512 = _pure_python_sha512()
    for size in (0, 1, 63, 64, 111, 112, 127, 128, 129, 255, 256, 1024):
        message = bytes((i * 131 + 17) & 0xFF for i in range(size))
        assert sha512(message).digest() == hashlib.sha512(message).digest()


def test_pure_python_sha512_ring_schedule_supports_incremental_updates_and_copy():
    sha512 = _pure_python_sha512()
    message = bytes(range(256)) + b"published disposable test payload"
    h = sha512()
    h.update(message[:73])
    clone = h.copy()
    h.update(message[73:])
    clone.update(message[73:])
    expected = hashlib.sha512(message).digest()
    assert h.digest() == expected
    assert clone.digest() == expected


def test_pbkdf2_matches_real_hashlib_across_dklen_values():
    password = b"correct horse battery staple"
    salt = b"mnemonic"
    for dklen in (16, 32, 63, 64, 65, 100, 128, 200):
        expected = hashlib.pbkdf2_hmac("sha512", password, salt, 4, dklen)
        actual = shim_pbkdf2_hmac("sha512", password, salt, 4, dklen)
        assert actual == expected, "mismatch at dklen=%d" % dklen


def test_pbkdf2_matches_real_hashlib_across_iteration_counts():
    password = b"password"
    salt = b"salt"
    for iterations in (1, 2, 10, 100):
        expected = hashlib.pbkdf2_hmac("sha512", password, salt, iterations, 64)
        actual = shim_pbkdf2_hmac("sha512", password, salt, iterations, 64)
        assert actual == expected, "mismatch at iterations=%d" % iterations


def test_pbkdf2_matches_real_hashlib_across_password_lengths():
    # crosses the sha512 128-byte block-size threshold (the HMAC key-padding
    # branch that hashes an over-long key down first) both sides of it
    salt = b"salt"
    for pw_len in (0, 1, 64, 127, 128, 129, 200, 500):
        password = b"p" * pw_len
        expected = hashlib.pbkdf2_hmac("sha512", password, salt, 3, 64)
        actual = shim_pbkdf2_hmac("sha512", password, salt, 3, 64)
        assert actual == expected, "mismatch at password length=%d" % pw_len


def test_pbkdf2_accepts_str_password_like_hashlib_does():
    expected = hashlib.pbkdf2_hmac("sha512", "hello".encode("utf-8"), b"salt", 2, 64)
    actual = shim_pbkdf2_hmac("sha512", "hello", b"salt", 2, 64)
    assert actual == expected


def test_pbkdf2_rejects_non_sha512():
    try:
        shim_pbkdf2_hmac("sha256", b"x", b"y", 1, 32)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_reverse64_matches_plain_python_slice_reversal():
    # secp256k1.py's _reverse64 exists only because MicroPython's bytes type
    # doesn't support b[::-1] at all -- confirmed against a real MicroPython
    # 1.22 interpreter (see secp256k1.py's docstring). Its manual
    # reversal should be exactly equivalent to the slice-based version for
    # any 64-byte input, which is all it's ever called with in practice.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_secp256k1_shim_for_test", str(ROOT / "device" / "mp_shims" / "secp256k1.py")
    )
    # secp256k1.py does `from embit.util.py_secp256k1 import *`, which needs
    # `embit` importable -- add device/ (not mp_shims) for that, same as
    # every other test file does.
    sys.path.insert(0, str(ROOT / "device"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for trial in (bytes(range(64)), bytes(reversed(range(64))), bytes(64), bytes([255] * 64)):
        x, y = trial[:32], trial[32:]
        expected = x[::-1] + y[::-1]
        assert module._reverse64(trial) == expected
