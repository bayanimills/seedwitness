"""The on-device fingerprint is only useful if it equals the number the
manifest publishes. Two implementations compute it -- attest.py on the board
and tools/build_manifest.py on a workstation -- and nothing but these tests
stops them drifting apart.

A drift here is quiet and nasty: the screen keeps showing a plausible hex
string, it just stops matching, and the user is left comparing two correct
numbers that disagree and concluding their device is compromised.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))

from seedwitness import attest  # noqa: E402

BUILD = ROOT / "_build_mpy"
needs_build = pytest.mark.skipif(
    not BUILD.is_dir(), reason="run tools/build_mpy.sh first")


def test_build_provenance_gate_rejects_staged_and_unstaged_device_changes():
    script = (ROOT / "tools" / "build_mpy.sh").read_text(encoding="utf-8")
    assert 'git -C "$HERE" diff --quiet -- device/ tools/build_mpy.sh' in script
    assert ('git -C "$HERE" diff --cached --quiet -- device/ tools/build_mpy.sh'
            in script)
    assert "ls-files --others --exclude-standard -- device/" in script


@needs_build
def test_fingerprint_equals_the_published_manifest_digest():
    """The whole point. If this fails, the screen lies to the user's face."""
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "build_manifest.py")],
                         capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    manifest = json.loads(out.stdout)
    digest, _, _ = attest.fingerprint()
    assert digest == manifest["deploy"]["digest"]


@needs_build
def test_runtime_files_are_excluded_from_both_sides():
    """Runtime records cannot be in a release manifest, so the on-device and
    external implementations must skip the same small, data-only set."""
    verify = (ROOT / "tools" / "verify_device.py").read_text(encoding="utf-8")
    for name in attest.RUNTIME_FILES:
        assert '"%s"' % name in verify, (
            "%s is skipped on-device but not exempted in verify_device.py, so "
            "a real board would fail verification on it" % name)


def test_no_exempt_path_can_execute():
    """The rule that makes exemptions safe.

    An exemption is an unhashed file that verification reports as fine. If
    such a file can EXECUTE, the exemption list stops being a data allowance
    and becomes a code-execution allowlist that the checker blesses.

    boot.py was exactly that: MicroPython runs it on every reset, including
    the soft reset mpremote performs to enter the raw REPL, so it ran during
    the verification run itself. A payload there changed no hashed byte and
    both tiers reported PASS. It is now shipped as build output and pinned.

    This test exists so nobody re-opens that hole by adding a convenient .py
    to the list.
    """
    for name in attest.RUNTIME_FILES:
        assert not name.endswith((".py", ".mpy")), (
            "%s is exempt from hashing but can execute; pin it in the build "
            "instead of exempting it" % name)


def test_digest_is_defined_over_sorted_sha256sum_lines():
    """Pin the construction itself, not just today's output. Anyone changing
    the separator, the sort, or the trailing newline has to change this test
    and should have to think about why."""
    files = {"b.mpy": b"two", "a.mpy": b"one"}
    lines = b""
    for path in sorted(files):
        lines += hashlib.sha256(files[path]).hexdigest().encode()
        lines += b"  " + path.encode() + b"\n"
    expected = hashlib.sha256(lines).hexdigest()

    roll = hashlib.sha256()
    for path in sorted(files):
        roll.update(hashlib.sha256(files[path]).hexdigest().encode())
        roll.update(b"  ")
        roll.update(path.encode())
        roll.update(b"\n")
    assert roll.hexdigest() == expected


def test_short_groups_hex_so_a_human_can_compare_it():
    s = attest.short("0123456789abcdef" * 4)
    assert s == "0123 4567 89ab cdef"
    # unspaced hex is where transposition errors hide, so the spacing is the
    # feature, not decoration
    assert s.count(" ") == 3
