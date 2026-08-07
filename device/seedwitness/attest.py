"""On-device build fingerprint: hash every file the board runs and roll it up
into one short string a human can compare against the published manifest.

Read this before trusting what it prints
----------------------------------------
This is self-attestation. The device is being asked to describe itself, and
code that has been tampered with will answer with whatever you were hoping to
see. It cannot detect a competent attacker, and nothing about the fact that it
runs on-device makes it stronger -- that is precisely what makes it weaker.

What it is actually good for:

  * a deploy went wrong and a stale file is still on the board
  * someone copied a file by hand and forgot one
  * the board in your drawer is not the build you think it is

All three are real and all three are common. None of them involve an
adversary. That is the honest scope.

For the check that does survive an adversary, use tools/verify_device.py,
which reads the flash through the ESP32's ROM bootloader -- outside the
firmware, outside anything this file could lie about. Its tier 1 is the one
that proves something. This screen exists because it needs no computer, and so
can be run without breaking the air gap, which is the one thing verify_device
cannot offer.

The digest is defined identically to tools/build_manifest.py, so the two are
directly comparable:

    line   = "<sha256 hex>  <path>\\n"      (two spaces, sha256sum format)
    digest = sha256(concat(lines sorted bytewise by path))

Paths are relative to the filesystem root with no leading slash, matching the
manifest's `deploy` section.
"""
import hashlib
import os

# Files that exist on a board but are not build output, so a manifest cannot
# contain them and including them would make every device disagree with the
# published digest. Kept in sync BY HAND with RUNTIME_FILES in
# tools/verify_device.py -- there is no shared module between host and device,
# and inventing one to hold two strings would cost more than it saves. If you
# add to one list, add to the other.
#
#   accounts.json enrolled watch-only xpubs, written by the user
#   diag.txt      boot/crash breadcrumb, written on-device (see diag.py)
#
# accounts.json was missed when enrolment landed, and the consequence was
# nasty: from the first enrolment onward the fingerprint could never match the
# published manifest again, and it changed again on every label edit. The user
# is then shown a permanent mismatch, which teaches them that mismatches are
# normal. A warning nobody believes is worse than no warning, so this omission
# was actively destroying the only thing the screen is for. diag.txt would
# have re-opened the same hole on the first crash or non-PWRON boot.
RUNTIME_FILES = ("accounts.json", "diag.txt")

_DIR = 0x4000
_CHUNK = 512       # flash reads; measured ~555 KB/s hashing on this board

# On the board the deploy tree IS the filesystem root. On the desktop there is
# no board, so the sim points at the build output instead -- same files, same
# relative paths, same digest, which is what makes the screenshot honest.
try:
    os.ilistdir
    ROOT = ""
except AttributeError:  # CPython
    ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "_build_mpy")
    ROOT = os.path.normpath(ROOT).replace("\\", "/")


def _entries(path):
    """(name, is_dir) pairs, on MicroPython and on CPython.

    os.ilistdir is MicroPython-only. Without a fallback this module imports
    fine on the desktop and then explodes the moment a screen calls it, which
    would mean the one screen in the app that the sim cannot draw -- and
    therefore cannot screenshot, and therefore cannot review. This project has
    already been bitten once by the sim quietly disagreeing with the device;
    it is not worth introducing a fresh case of it to save eight lines.
    """
    try:
        return [(n, t == _DIR) for n, t, _, _ in os.ilistdir("/" + path if path else "/")]
    except AttributeError:
        root = ROOT + ("/" + path if path else "")
        return [(n, os.path.isdir(root + "/" + n)) for n in os.listdir(root)]


def _walk(path, out):
    for name, is_dir in _entries(path):
        full = name if path == "" else path + "/" + name
        if is_dir:
            _walk(full, out)
        elif full not in RUNTIME_FILES:
            out.append(full)
    return out


def file_sha256(path):
    h = hashlib.sha256()
    f = open((ROOT + "/" + path) if ROOT else ("/" + path), "rb")
    try:
        while True:
            b = f.read(_CHUNK)
            if not b:
                break
            h.update(b)
    finally:
        f.close()
    # Built by hand rather than importing ubinascii for one call. NOT because
    # .hex() is missing: it exists on this port, verified on the board
    # (bytes(range(4)).hex() -> "00010203"). The comment here used to claim
    # otherwise, which contradicted flow_generate.py's use of .hex() on the
    # same firmware and would have sent the next reader looking for a bug
    # that is not there.
    return "".join("%02x" % b for b in h.digest())


def fingerprint(progress=None):
    """Roll every build file into one digest.

    `progress(done, total)` is called as it goes, because this takes long
    enough to look like a hang otherwise. Returns (digest_hex, file_count,
    total_bytes).
    """
    paths = sorted(_walk("", []))
    roll = hashlib.sha256()
    total = 0
    for i, p in enumerate(paths):
        digest = file_sha256(p)
        roll.update(digest.encode())
        roll.update(b"  ")
        roll.update(p.encode())
        roll.update(b"\n")
        try:
            total += os.stat((ROOT + "/" + p) if ROOT else ("/" + p))[6]
        except OSError:
            pass
        if progress:
            progress(i + 1, len(paths))
    return ("".join("%02x" % b for b in roll.digest()), len(paths), total)


def short(digest_hex, groups=4, size=4):
    """First `groups`*`size` hex chars, spaced. A human compares this against
    the manifest by eye, and unspaced hex is where transposition errors hide.
    """
    head = digest_hex[:groups * size]
    return " ".join(head[i:i + size] for i in range(0, len(head), size))
