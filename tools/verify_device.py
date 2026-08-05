"""Verify a real CYD board against a published build manifest, from outside it.

Why this is not just an on-device "show me your hash" screen
-----------------------------------------------------------
A device that reports its own fingerprint is being asked to describe itself.
Compromised code answers that question with whatever string you were hoping to
see, so self-attestation catches accidental drift and lazy tampering and
nothing more. It is worth having (see the Verify Build screen, which needs no
computer and so preserves the air gap) but it is not what proves anything.

This script escapes that circularity in tier 1 by reading the flash through the
ESP32's ROM bootloader via esptool. The ROM is burned into the chip, runs
before any firmware does, and is not the thing under audit. The application
cannot influence what comes back.

Two tiers, and the difference between them matters:

  TIER 1  firmware, fully external. esptool reads the checked bootloader
          region, partition table and the bytes from the published image that
          occupy the factory partition straight off flash, then compares them
          against the official micropython.org ESP32_GENERIC image. That target
          is published by someone who is not the author of this project, which
          is the entire reason this repo runs stock firmware. Ship a custom
          build and this tier degrades to "compare against the author's own
          binary", which is not verification.

  TIER 2  application files. With littlefs-python, esptool reads raw flash and
          this host parses LittleFS without asking running firmware to list or
          hash anything. Without it, a clearly labelled fallback transfers
          bytes through the running firmware; that mode means considerably
          less and must be interpreted together with tier 1.

Honest limits, so nobody reads more into a green result than is there:

  * A tier-1 pass means the checked firmware bytes match the supplied official
    image. NVS and radio-calibration partitions are excluded.
    It does not prove the silicon is honest. Nothing reachable over USB can: a
    chip that lies to its own ROM interface is outside what any host-side tool
    can see.
  * Secure boot is not enabled (see SECURITY.md), so nothing stops someone
    with physical access from reflashing. This detects that after the fact; it
    does not prevent it.
  * Tier 2 runs EXTERNAL when littlefs-python is installed: the vfs partition
    is read through the ROM bootloader and the filesystem is parsed here, so a
    malicious .mpy cannot hide a file from the listing. Without that package it
    falls back to enumerating through the running firmware, which is weaker and
    is labelled as such in the output. `pip install littlefs-python`.

Usage:
    python tools/verify_device.py --port COM9 --manifest manifest.json
    python tools/verify_device.py --port COM9 --manifest manifest.json \\
        --firmware ~/firmware/ESP32_GENERIC-20260406-v1.28.0.bin

Without --firmware, tier 1 still reports what is on the flash (and flags the
partition table) but cannot compare it to anything, so it reports UNVERIFIED
rather than OK. Exit status is non-zero on any mismatch.
"""
import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Flash geometry for a stock ESP32 4MB build. The combined firmware .bin that
# micropython.org publishes is flashed at 0x1000 and is contiguous from there,
# so a byte at flash address A lives at file offset A - 0x1000.
FLASH_BASE = 0x1000
BOOTLOADER = (0x1000, 0x7000)     # (addr, length)
PTABLE = (0x8000, 0x1000)
APP_ADDR = 0x10000

# Deliberately NOT compared: nvs (0x9000) and phy_init (0xf000). Both are
# written by the running system -- wifi calibration, stored key/value pairs --
# so they differ from the shipped image on any board that has ever booted.
# Including them would produce a mismatch on a perfectly good device, and a
# check that cries wolf is a check people learn to skip.
MUTABLE = {"nvs", "phy_init"}

OK, MISMATCH, MISSING, UNEXPECTED, UNVERIFIED, RUNTIME = (
    "OK", "MISMATCH", "MISSING", "UNEXPECTED", "UNVERIFIED", "RUNTIME")

# Files that legitimately exist on a board but are not build output, so they
# cannot appear in a manifest. Listed explicitly rather than pattern-matched:
# an unexpected file is the shape a payload takes, so every exemption should
# have to be argued for by name in source someone can read.
#
#   accounts.json enrolled watch-only xpubs, written by the user
#   diag.txt      boot/crash breadcrumb, written on-device (see
#                 device/seedwitness/diag.py); data-only by construction,
#                 and the exemption tests forbid it ever becoming executable
#
# These are reported as RUNTIME, not OK. They are still shown in the output,
# and their contents are still nobody's guarantee -- the point is only that
# their presence is not evidence of tampering. Anything NOT on this list stays
# UNEXPECTED and fails the run.
RUNTIME_FILES = {"accounts.json", "diag.txt"}


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("%s failed (%d):\n%s\n%s"
                           % (cmd[0], p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
    return p.stdout


def read_flash(port, addr, length, workdir, baud=460800, attempts=3):
    """Pull a flash region via the ROM bootloader. Outside the firmware.

    Retried, and the retry drops the baud rate. These reads run to megabytes
    (the app partition and the whole filesystem), and at 460800 a single
    corrupted chunk aborts the transfer with "Corrupt data, expected 0x1000
    bytes but received ...". That is a link-layer hiccup, not evidence about
    the device, so failing the whole verification on it would teach the
    operator to rerun until green, which is precisely the habit that makes a
    real MISMATCH get shrugged off.

    The result is still verified by content afterwards, so a retry cannot
    launder a genuine mismatch: it only gets the bytes across.
    """
    out = Path(workdir) / ("flash_%x_%x.bin" % (addr, length))
    rates = [baud, 230400, 115200][:attempts]
    last = None
    for rate in rates:
        try:
            _run([sys.executable, "-m", "esptool", "--port", port,
                  "--baud", str(rate), "read-flash",
                  hex(addr), hex(length), str(out)])
            data = out.read_bytes()
            if len(data) == length:
                return data
            last = RuntimeError("short read: %d of %d bytes" % (len(data), length))
        except RuntimeError as exc:
            last = exc
        time.sleep(1.0)
    raise last


def parse_ptable(blob):
    """Decode an ESP32 partition table. 32-byte entries, 0xAA50 magic, and an
    0xEBEB record marking the end."""
    parts = []
    for i in range(0, len(blob), 32):
        e = blob[i:i + 32]
        if len(e) < 32 or e[:2] == b"\xeb\xeb" or e[:2] == b"\xff\xff":
            break
        if e[:2] != b"\xaa\x50":
            continue
        off, size = struct.unpack("<II", e[4:12])
        parts.append({
            "label": e[12:28].rstrip(b"\x00").decode("ascii", "replace"),
            "type": e[2], "subtype": e[3], "offset": off, "size": size,
        })
    return parts


def tier1(port, workdir, firmware=None):
    """Firmware, read externally through the ROM bootloader."""
    results = []
    official = Path(firmware).read_bytes() if firmware else None

    def cmp_region(name, addr, length):
        got = read_flash(port, addr, length, workdir)
        if official is None:
            return {"name": name, "status": UNVERIFIED, "sha256": sha256(got),
                    "detail": "no --firmware given, nothing to compare against"}
        lo = addr - FLASH_BASE
        want = official[lo:lo + length]
        if len(want) < length:
            return {"name": name, "status": UNVERIFIED, "sha256": sha256(got),
                    "detail": "official image is shorter than this region"}
        return {"name": name,
                "status": OK if want == got else MISMATCH,
                "sha256": sha256(got), "expected": sha256(want)}

    results.append(cmp_region("bootloader", *BOOTLOADER))

    ptable_blob = read_flash(port, PTABLE[0], PTABLE[1], workdir)
    parts = parse_ptable(ptable_blob)
    r = {"name": "partition-table", "sha256": sha256(ptable_blob),
         "partitions": parts}
    if official is not None:
        want = official[PTABLE[0] - FLASH_BASE:PTABLE[0] - FLASH_BASE + PTABLE[1]]
        r["status"] = OK if want == ptable_blob else MISMATCH
        r["expected"] = sha256(want)
    else:
        r["status"] = UNVERIFIED
    results.append(r)

    # The app partition is mostly 0xFF padding, so hashing the whole partition
    # says little. Compare exactly as many bytes as the official image carries.
    app = next((p for p in parts if p["label"] == "factory"), None)
    if app is None:
        results.append({"name": "app", "status": MISSING,
                        "detail": "no 'factory' partition in the table"})
    elif official is None:
        results.append({"name": "app", "status": UNVERIFIED,
                        "detail": "no --firmware given"})
    else:
        app_len = len(official) - (APP_ADDR - FLASH_BASE)
        app_len = min(app_len, app["size"])
        got = read_flash(port, APP_ADDR, app_len, workdir)
        want = official[APP_ADDR - FLASH_BASE:APP_ADDR - FLASH_BASE + app_len]
        results.append({"name": "app", "status": OK if want == got else MISMATCH,
                        "sha256": sha256(got), "expected": sha256(want),
                        "bytes": app_len})
    return results


def vfs_region(parts, flash_size):
    """Where the filesystem lives.

    MicroPython's ESP32 partition table does not declare a vfs entry (verified
    against the official image: three entries, byte-identical), so it cannot be
    looked up. It is the space after the last declared partition, which is
    derivable from the table plus the flash size without asking the firmware
    anything. Asking the firmware would defeat the point of this tier.
    """
    end = max((p["offset"] + p["size"]) for p in parts) if parts else 0
    if end >= flash_size:
        return None
    return (end, flash_size - end)


def flash_size(port):
    # stderr as well as stdout: esptool prints the size line to stderr, and
    # _run returns only stdout, so parsing that alone found nothing.
    p = subprocess.run([sys.executable, "-m", "esptool", "--port", port,
                        "flash-id"], capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if "Detected flash size" in line:
            token = line.split(":")[-1].strip()
            if token.upper().endswith("MB"):
                return int(token[:-2].strip()) * 1024 * 1024
            if token.upper().endswith("KB"):
                return int(token[:-2].strip()) * 1024
    raise RuntimeError("could not determine flash size from esptool flash-id")



def tier2_external(port, manifest, workdir, parts):
    """Application files, read and parsed entirely outside the firmware.

    This is the tier that actually closes the hole. The mpremote path below
    asks the running firmware to list its own files, and a malicious .mpy can
    patch os.ilistdir to hide one. Here the vfs partition comes back through
    the ROM bootloader as raw bytes and the filesystem is parsed on this host,
    so nothing on the device participates in deciding what is present.

    littlefs, not FAT, despite the partition subtype saying fat. Confirmed by
    the superblock magic at offset 8 of the region.

    The parsing is delegated to littlefs-python, which wraps the reference C
    implementation, rather than hand-rolled. A subtly wrong filesystem parser
    inside a verification tool is worse than no parser at all: it would report
    OK on bytes it misread, which is the exact failure this tool exists to
    prevent.
    """
    from littlefs import LittleFS

    size = flash_size(port)
    region = vfs_region(parts, size)
    if region is None:
        raise RuntimeError("no space after the last partition for a filesystem")
    offset, length = region
    img = read_flash(port, offset, length, workdir)

    fs = LittleFS(block_size=4096, block_count=len(img) // 4096, mount=False)
    fs.context.buffer = bytearray(img)
    fs.mount()

    found = {}

    def walk(d):
        for name in fs.listdir(d):
            path = (d.rstrip("/") + "/" + name)
            st = fs.stat(path)
            if st.type == 2:
                walk(path)
            else:
                with fs.open(path, "rb") as f:
                    found[path.lstrip("/")] = hashlib.sha256(f.read()).hexdigest()

    walk("/")

    want = {e["path"]: e["sha256"] for e in manifest["deploy"]["files"]}
    results = []
    for path in sorted(found):
        if path not in want:
            results.append({"path": path,
                            "status": RUNTIME if path in RUNTIME_FILES else UNEXPECTED})
        else:
            results.append({"path": path,
                            "status": OK if found[path] == want[path] else MISMATCH,
                            "sha256": found[path], "expected": want[path]})
    for path in sorted(want):
        if path not in found:
            results.append({"path": path, "status": MISSING})
    return results


def _mpremote(port, *args, retries=6):
    """Run an mpremote subcommand, tolerating a board that is still booting.

    Tier 1 finishes with esptool hard-resetting the chip, so the first attempt
    here usually lands mid-boot and fails with "could not enter raw repl". That
    is a race, not a fault, and retrying is the whole fix -- but it must be
    bounded, or a genuinely wedged board turns a verification run into a hang,
    which is the failure mode most likely to be mistaken for a pass.
    """
    exe = shutil.which("mpremote")
    cmd = ([exe] if exe else [sys.executable, "-m", "mpremote"]) + \
        ["connect", port] + list(args)
    last = None
    for attempt in range(retries):
        try:
            return _run(cmd)
        except RuntimeError as exc:
            last = exc
            if "raw repl" not in str(exc) and "SerialException" not in str(exc):
                raise
            time.sleep(1.5 * (attempt + 1))
    raise last


def device_files(port):
    """Enumerate every file on the board, depth-first."""
    listing = _mpremote(port, "exec", """
import os
def walk(d):
    for name, typ, _, _ in os.ilistdir(d):
        p = d + '/' + name if d != '/' else '/' + name
        if typ == 0x4000:
            walk(p)
        else:
            print(p)
walk('/')
""")
    return sorted(x.strip().lstrip("/") for x in listing.splitlines() if x.strip())


def tier2(port, manifest, workdir):
    """Application files. Bytes come off the board; hashing happens here."""
    want = {e["path"]: e["sha256"] for e in manifest["deploy"]["files"]}
    on_device = device_files(port)
    results = []
    tmp = Path(workdir) / "pull"
    tmp.mkdir(exist_ok=True)

    for path in on_device:
        if path not in want:
            results.append({"path": path,
                            "status": RUNTIME if path in RUNTIME_FILES else UNEXPECTED})
            continue
        local = tmp / path.replace("/", "__")
        _mpremote(port, "cp", ":" + path, str(local))
        got = sha256(local.read_bytes())
        results.append({"path": path, "status": OK if got == want[path] else MISMATCH,
                        "sha256": got, "expected": want[path]})

    present = set(on_device)
    for path in sorted(want):
        if path not in present:
            results.append({"path": path, "status": MISSING})
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. COM9")
    ap.add_argument("--manifest", help="manifest.json from tools/build_manifest.py")
    ap.add_argument("--firmware", help="official micropython.org ESP32_GENERIC .bin")
    ap.add_argument("--skip-tier2", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as workdir:
        t1 = tier1(args.port, workdir, args.firmware)
        report = {"tier1": t1, "tier2": [], "tier2_external": False}
        if not args.skip_tier2:
            if not args.manifest:
                ap.error("--manifest is required unless --skip-tier2")
            manifest = json.loads(Path(args.manifest).read_text())
            parts = next((r.get("partitions") for r in t1 if r.get("partitions")), [])
            try:
                report["tier2"] = tier2_external(args.port, manifest, workdir, parts)
                report["tier2_external"] = True
            except ImportError:
                # Not fatal, but the difference matters and is reported.
                report["tier2"] = tier2(args.port, manifest, workdir)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("TIER 1  firmware, read externally via the ROM bootloader")
        for r in report["tier1"]:
            print("  %-11s %s" % (r["status"], r["name"]))
            if r.get("detail"):
                print("              %s" % r["detail"])
            for p in r.get("partitions", []):
                print("              %-10s off=0x%06x size=%4d KB"
                      % (p["label"], p["offset"], p["size"] // 1024))
        if report["tier2"]:
            mode = ("filesystem parsed HERE from raw flash; firmware not consulted"
                    if report.get("tier2_external")
                    else "files listed BY THE FIRMWARE (weaker: install littlefs-python)")
            print("")
            print("TIER 2  " + mode)
            bad = [r for r in report["tier2"] if r["status"] not in (OK, RUNTIME)]
            rt = [r for r in report["tier2"] if r["status"] == RUNTIME]
            print("  %d files, %d OK, %d runtime, %d not OK"
                  % (len(report["tier2"]), len(report["tier2"]) - len(bad) - len(rt),
                     len(rt), len(bad)))
            for r in rt:
                print("  %-11s %s" % (r["status"], r["path"]))
            for r in bad:
                print("  %-11s %s" % (r["status"], r["path"]))

    failed = [r for r in report["tier1"] + report["tier2"]
              if r["status"] in (MISMATCH, MISSING, UNEXPECTED)]
    unver = [r for r in report["tier1"] if r["status"] == UNVERIFIED]
    print()
    if failed:
        print("FAIL: %d check(s) did not match" % len(failed))
        return 1
    if unver:
        print("INCOMPLETE: firmware not compared (pass --firmware to complete tier 1)")
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
