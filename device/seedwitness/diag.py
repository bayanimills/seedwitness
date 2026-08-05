"""The device's only breadcrumb: one tiny, bounded diagnostic register.

Two field failures motivated this, both reconstructed from symptoms because
nothing was recorded: a reboot during Verify Build that was never reproduced
(a reset-cause record would have said watchdog-or-power in seconds), and a
freeze mid-refresh that took hours to trace to canvas.wait_release()'s old
unbounded busy-wait (an exception-location record names that class of bug
immediately when it raises rather than wedges).

What gets written, and the rule that bounds it
----------------------------------------------
The records are single short lines in diag.txt at the filesystem root,
overwritten in place -- never appended, so the file cannot grow and cannot
fill the flash:

    boot <RESET_CAUSE_NAME>
    boots <COUNT>
    crash <ExceptionTypeName> <file.py:line>
    crashes <COUNT>
    ack_crashes <COUNT>
    last_crash_boot <COUNT>
    build <16_HEX_CHARS>

The counts replace a clock.  The CYD has no battery-backed, synchronised
calendar clock, while its boot-relative ticks counter wraps, so a timestamp
would look precise while being false.  ``crashes / boots`` and
``last_crash_boot`` make recurrence measurable across power cycles without
pretending the device knows when they happened.  Counts saturate at nine
digits, keeping both allocation and file size bounded.  One tiny rewrite per
boot is the unavoidable cost of a durable boot denominator; crash, acknowledge
and build writes happen only when those facts change.

``ack_crashes`` is a cursor, not deletion.  Acknowledging a crash preserves
the cumulative total, last location and boot context; it only records that a
human has reviewed every crash through that count.  Thus a later crash becomes
visibly new again.

THE RULE: only code coordinates, never data. This is a seed device, and an
exception MESSAGE can carry a secret -- embit's bip39 raises
ValueError("Word '%s' is not in the dictionary") with a user word inside it.
So the crash recorder is built to be physically unable to see the message:

  * it reads type(exc).__name__, which is a compile-time identifier;
  * it reads the traceback's frame coordinates (filename, line number),
    which describe code, not data. On CPython that is the tb_frame chain;
    on MicroPython it is sys.print_exception() output, scanned ONLY between
    the 'Traceback' header and the first non-frame line -- which is where
    the message begins, so the scan stops before the message even if the
    message itself contains a line shaped like a frame;
  * everything written passes through _clean(): a character whitelist plus
    a hard length cap, as a second, independent layer.

str(exc) and exc.args are never touched, anywhere in this module. Keep it
that way: the guarantee is structural, not a filter that has to keep up
with future exception text.

Failure behaviour: every public function swallows every exception and returns
its fail-soft sentinel (False, an empty record, or "no pending crash").
record_crash() runs inside the crash handler -- the one path guaranteed to
execute when things are already wrong -- so a full filesystem, a corrupt file
or a MemoryError here must degrade to "no breadcrumb", never to a second
crash.

diag.txt is a RUNTIME file: written on-device, absent from the build, so it
is exempted from hashing in all three hand-maintained lists (attest.py,
tools/verify_device.py, tools/deploy.py). It is .txt, not .py, because
tests/test_attest.py rightly forbids an executable exemption.
"""
import os

# Same platform probe as attest.py, for the same reason: this module must
# import and run on the desktop (sim/capture_screens.py and the test suite
# draw the screen that reads it) without exploding on a MicroPython-only
# API. On the board the record lives at the filesystem root; on the desktop it
# goes to the system temp dir, never the repo.
try:
    os.ilistdir
    _PATH = "/diag.txt"
except AttributeError:  # CPython
    import tempfile
    _PATH = os.path.join(tempfile.gettempdir(), "seedwitness_diag.txt")

# Whitelist for everything written: identifier characters plus the few that
# appear in a file:line location. No space, no quotes -- a multi-word secret
# cannot survive this even if the structural guarantee above were somehow
# bypassed.
_SAFE = ("abcdefghijklmnopqrstuvwxyz"
         "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
_MAX_FILE = 256   # read cap: a corrupt/foreign file must not allocate big
_MAX_COUNT = 999999999
_ORDER = ("boot", "boots", "crash", "crashes", "ack_crashes",
          "last_crash_boot", "build")

_CAUSE_NAMES = ("PWRON_RESET", "HARD_RESET", "WDT_RESET",
                "DEEPSLEEP_RESET", "SOFT_RESET", "BROWNOUT_RESET")


def _clean(s, limit):
    out = ""
    for ch in s[:limit]:
        if ch in _SAFE:
            out += ch
    return out


def _last_frame(text):
    """Deepest 'File "x", line N' from MicroPython's printed traceback.

    Scans ONLY the frame lines between the 'Traceback' header and the first
    line that is not a frame line -- the message starts there, so the scan
    stops before it. A message containing '\\n  File "..." ...' therefore
    cannot inject a fake location: it sits after the stop point.
    """
    loc = ""
    started = False
    for line in text.split("\n"):
        line = line.strip()
        if not started:
            started = line.startswith("Traceback")
            continue
        if not line.startswith('File "'):
            break
        q = line.find('"', 6)
        if q < 0:
            break
        fn = line[6:q].replace("\\", "/").split("/")[-1]
        i = line.find(", line ", q)
        num = ""
        if i >= 0:
            for ch in line[i + 7:]:
                if not ch.isdigit():
                    break
                num += ch
        if fn and num:
            loc = fn + ":" + num
    return loc


def _where(exc):
    """Deepest code location of `exc` as 'file.py:line', or ''.

    Reads only frame coordinates -- see the module docstring for why the
    message must stay unreachable from here.
    """
    try:
        import sys
        pe = getattr(sys, "print_exception", None)
        if pe is None:  # CPython: walk the real traceback objects
            tb = getattr(exc, "__traceback__", None)
            if tb is None:
                return ""
            while tb.tb_next is not None:
                tb = tb.tb_next
            fn = tb.tb_frame.f_code.co_filename
            return "%s:%d" % (fn.replace("\\", "/").split("/")[-1],
                              tb.tb_lineno)
        import io  # MicroPython: print_exception is the only traceback API
        buf = io.StringIO()
        pe(exc, buf)
        return _last_frame(buf.getvalue())
    except Exception:
        return ""


def _count(value, default=0):
    """A bounded non-negative integer from a persisted value."""
    try:
        s = str(value)
        if not s or len(s) > 9:
            return default
        for ch in s:
            if not ch.isdigit():
                return default
        n = int(s)
        return n if n <= _MAX_COUNT else _MAX_COUNT
    except Exception:
        return default


def _normalise(raw):
    """Validate a parsed record and migrate the original two-line format."""
    if not raw:
        return {}
    out = {}
    boot = raw.get("boot")
    if boot and boot == _clean(boot, 24):
        out["boot"] = boot
    crash = raw.get("crash")
    if crash:
        parts = crash.split(" ")
        valid = (len(parts) == 1
                 and parts[0] == _clean(parts[0], 32))
        if len(parts) == 2:
            valid = (parts[0] == _clean(parts[0], 32)
                     and parts[1] == _clean(parts[1], 48))
        if valid:
            out["crash"] = crash
    build = raw.get("build")
    if (build and len(build) == 16
            and all(ch in "0123456789abcdef" for ch in build)):
        out["build"] = build

    # A legacy line proves at least one occurrence.  Treating it as zero
    # would silently acknowledge the very field breadcrumb being migrated.
    boots = _count(raw.get("boots"), 1 if "boot" in out else 0)
    crashes = _count(raw.get("crashes"), 1 if "crash" in out else 0)
    ack = _count(raw.get("ack_crashes"), 0)
    if ack > crashes:
        ack = crashes
    out["boots"] = boots
    out["crashes"] = crashes
    out["ack_crashes"] = ack
    if "crash" in out:
        out["last_crash_boot"] = _count(
            raw.get("last_crash_boot"), boots)
    return out


def _load():
    raw = {}
    try:
        f = open(_PATH, "r")
        try:
            text = f.read(_MAX_FILE)
        finally:
            f.close()
    except Exception:
        return raw
    for line in text.split("\n"):
        for key in _ORDER:
            if line.startswith(key + " "):
                raw[key] = line[len(key) + 1:].strip()
                break
    return _normalise(raw)


def _write(rec):
    """Rewrite the complete bounded register.  Never append, never raise."""
    try:
        rec = _normalise(rec)
        f = open(_PATH, "w")
        try:
            for k in _ORDER:
                if k in rec:
                    f.write(k + " " + str(rec[k]) + "\n")
        finally:
            f.close()
        return True
    except Exception:
        return False


def read():
    """Return the bounded register; missing/corrupt files read as empty."""
    try:
        return _load()
    except Exception:
        return {}


def record_boot(machine_mod=None):
    """Record why the board reset, from machine.reset_cause().

    This is the fact that separates a genuine watchdog/brownout reset from
    a clean power cycle -- the distinction the Verify-Build-reboot report
    could not make. Called from device/main.py at every boot; on the
    desktop there is no `machine` module and this is a silent no-op.
    `machine_mod` exists for tests only.
    """
    try:
        if machine_mod is None:
            import machine as machine_mod
    except ImportError:
        return False
    try:
        n = machine_mod.reset_cause()
        name = None
        for c in _CAUSE_NAMES:
            if getattr(machine_mod, c, None) == n:
                name = c
                break
        if name is None:
            name = "CAUSE_%d" % n
        rec = _load()
        rec["boot"] = _clean(name, 24)
        rec["boots"] = min(_MAX_COUNT, rec.get("boots", 0) + 1)
        return _write(rec)
    except Exception:
        return False


def record_crash(exc):
    """Record an unhandled exception: type name and code location ONLY.

    Runs inside the crash handler after the seed cache is cleared. Never
    raises, and never reads str(exc) or exc.args -- the message is the one
    part of an exception that can carry a secret.
    """
    try:
        # Make room first rather than trusting the caller to have: when the
        # exception being recorded is a MemoryError, the StringIO the
        # traceback is printed into and the flash write below are exactly
        # the allocations that fail, and the swallowed failure costs the
        # only breadcrumb of the crash (seen in the field: a crash screen
        # with no crash line in diag.txt). `exc` and its traceback are still
        # referenced by the caller, so collecting cannot disturb them.
        import gc
        gc.collect()
        name = _clean(type(exc).__name__, 32)
        loc = _clean(_where(exc), 48)
        rec = _load()
        rec["crash"] = (name + " " + loc) if loc else name
        rec["crashes"] = min(_MAX_COUNT, rec.get("crashes", 0) + 1)
        rec["last_crash_boot"] = rec.get("boots", 0)
        return _write(rec)
    except Exception:
        return False


def has_unacknowledged():
    """Whether a persisted crash has not yet been reviewed.  Never raises."""
    try:
        rec = _load()
        return rec.get("crashes", 0) > rec.get("ack_crashes", 0)
    except Exception:
        return False


def acknowledge_crashes():
    """Mark all currently recorded crashes reviewed without deleting any.

    A later crash increments ``crashes`` and becomes unacknowledged again.
    The no-op case performs no flash write.
    """
    try:
        rec = _load()
        crashes = rec.get("crashes", 0)
        if rec.get("ack_crashes", 0) == crashes:
            return True
        rec["ack_crashes"] = crashes
        return _write(rec)
    except Exception:
        return False


def record_build(digest_hex):
    """Persist a safely computed build prefix, never arbitrary text.

    The caller is Verify Build, after it has already hashed the deployed
    tree.  Repeating the same verification costs no flash write.
    """
    try:
        head = digest_hex[:16].lower()
        if (len(head) != 16
                or any(ch not in "0123456789abcdef" for ch in head)):
            return False
        rec = _load()
        if rec.get("build") == head:
            return True
        rec["build"] = head
        return _write(rec)
    except Exception:
        return False
