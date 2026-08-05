"""Deploy _build_mpy/ to a board and refuse to claim success until the device
proves it holds the build.

Why this exists
---------------
`mpremote cp` returns 0 on a copy it did not make. Observed three times in one
session: the file stayed stale on the board, mpremote reported success, and the
failure surfaced much later as an AttributeError at runtime or a MISMATCH in
verification. Once it left a patched copy of the crypto library on the device
for an hour after the source had been reverted, with the repo and the hardware
silently disagreeing.

The cause is structural, not bad luck. Every `mpremote connect PORT ...`
invocation independently enters the raw REPL, which means interrupting whatever
is running. That is a race, and the losing side is silent.

So this tool does not trust the copy. It:

  1. stops the app once, deliberately, and holds the board there
  2. copies only files whose hash differs from the board's
  3. re-reads every file it touched and compares hashes again
  4. retries just the failures
  5. exits non-zero if the tree still disagrees

Hashing happens on the device for the comparison (SHA-256 is native there and
runs at ~555 KB/s, so a full tree costs about 3 seconds) and the result is
checked against hashes computed here. That is a deploy-correctness check, not a
security check: the point is catching a copy that did not land, and the device
has no reason to lie about its own freshly written files. For the security
question, which assumes the device DOES lie, use verify_device.py: it reads the
flash through the ROM bootloader instead.

Usage:
    python tools/deploy.py --port COM9
    python tools/deploy.py --port COM9 --all      # copy everything, skip diffing
    python tools/deploy.py --port COM9 --dry-run  # report drift, change nothing
"""
import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "_build_mpy"

# Written by the device or the user, never by the build. Must not be deleted or
# overwritten, and their absence from the build is not drift. Kept in step by
# hand with attest.RUNTIME_FILES and verify_device.RUNTIME_FILES.
RUNTIME_FILES = {"accounts.json", "diag.txt"}

MAX_ATTEMPTS = 4


def _mpremote(port, *args, retries=6):
    """One mpremote subcommand, tolerating a board that is mid-boot.

    Bounded, because an unbounded retry against a genuinely wedged board turns
    a deploy into a hang, and a hang is the failure most easily mistaken for
    slow progress.
    """
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    last = ""
    for attempt in range(retries):
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout
        last = (p.stdout or "") + (p.stderr or "")
        if "raw repl" not in last and "SerialException" not in last:
            break
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("mpremote %s failed:\n%s" % (" ".join(args), last[-1500:]))


def local_hashes():
    out = {}
    for p in sorted(BUILD.rglob("*")):
        if p.is_file():
            rel = p.relative_to(BUILD).as_posix()
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def device_hashes(port):
    """Hash every file on the board, on the board.

    One round trip for the whole tree rather than one per file: at 115200 baud
    the serial handshake dominates, and a per-file loop turned a 3 second job
    into minutes.
    """
    script = """
import hashlib, os
def walk(d, out):
    for name, typ, _, _ in os.ilistdir(d):
        p = (d + '/' + name) if d != '' else name
        if typ == 0x4000:
            walk(p, out)
        else:
            out.append(p)
    return out
for path in sorted(walk('', [])):
    h = hashlib.sha256()
    f = open('/' + path, 'rb')
    while True:
        b = f.read(512)
        if not b:
            break
        h.update(b)
    f.close()
    print(path, ''.join('%02x' % c for c in h.digest()))
"""
    out = {}
    for line in _mpremote(port, "exec", script).splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and len(parts[1]) == 64:
            out[parts[0]] = parts[1]
    return out


def copy(port, rel):
    """Create parent directories first: mpremote will not, and the resulting
    ENOENT is reported in a way that reads like a connection problem."""
    parts = rel.split("/")
    for i in range(1, len(parts)):
        d = "/".join(parts[:i])
        try:
            _mpremote(port, "fs", "mkdir", ":" + d, retries=1)
        except RuntimeError:
            pass  # already exists; mpremote has no mkdir -p
    _mpremote(port, "fs", "cp", str(BUILD / rel), ":" + rel)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--all", action="store_true",
                    help="copy every file rather than only those that differ")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not BUILD.is_dir():
        print("no _build_mpy/. Run tools/build_mpy.sh first.", file=sys.stderr)
        return 2

    want = local_hashes()
    print("build: %d files" % len(want))

    # Stop the app once and hold the board at the REPL for the whole run,
    # rather than letting every copy race the running app independently.
    _mpremote(port := args.port, "exec", "pass")

    have = device_hashes(port)
    stale = sorted(r for r in want if have.get(r) != want[r])
    extra = sorted(r for r in have if r not in want and r not in RUNTIME_FILES)

    if args.all:
        stale = sorted(want)

    if not stale:
        print("device already matches the build")
    else:
        print("%d file(s) to copy" % len(stale))
    for rel in extra:
        # Not deleted automatically: an unexpected file may be a stale deploy
        # or may be something the user put there, and this tool cannot tell.
        # verify_device.py is the thing that decides whether it is a problem.
        print("  EXTRA (not in build, not removed): %s" % rel)

    if args.dry_run:
        for rel in stale:
            print("  would copy %s" % rel)
        return 1 if (stale or extra) else 0

    pending = list(stale)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not pending:
            break
        for rel in pending:
            copy(port, rel)
        # The whole point: re-read and compare rather than trusting the copy.
        have = device_hashes(port)
        failed = [r for r in pending if have.get(r) != want[r]]
        done = len(pending) - len(failed)
        print("  attempt %d: %d landed, %d still wrong" % (attempt, done, len(failed)))
        pending = failed

    if pending:
        print("\nFAILED: %d file(s) never landed:" % len(pending), file=sys.stderr)
        for rel in pending:
            print("  %s" % rel, file=sys.stderr)
        return 1

    missing = [r for r in want if r not in have]
    if missing:
        print("\nFAILED: %d file(s) absent after deploy" % len(missing), file=sys.stderr)
        return 1

    print("\nOK: every build file verified present on the device by hash.")
    print("For the security check, which does not trust the device, run:")
    print("  python tools/verify_device.py --port %s --manifest manifest.json "
          "--firmware <official.bin>" % port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
