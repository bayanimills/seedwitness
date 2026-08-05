"""Emit (or check) a build manifest: SHA-256 of every deployed file, of every
source file it was built from, and one roll-up digest over each set.

Why this exists. The board runs .mpy bytecode cross-compiled
on a workstation. Reading device/ tells you what the project *says* it runs;
without a manifest there is nothing that ties the bytes on the flash back to
those sources. A manifest is the missing link: publish it alongside the source,
and anyone can (a) rebuild and confirm they get the same bytes, and (b) read
the board back with tools/verify_device.py and confirm it holds those bytes.

Two sections, two independent roll-ups:

  deploy  every file in _build_mpy/, pathed exactly as it lands on the board.
          This is what verify_device.py compares a real board against.
  source  device/** plus tools/build_mpy.sh, pathed from the repo root. These
          are precisely the inputs that determine the deploy tree, so pinning
          them lets a reader say "this bytecode came from that source" instead
          of just "this bytecode is stable".

The roll-up digest is defined so it can be reproduced without this script:

    line   = "<sha256 hex>  <path>\\n"      (two spaces, i.e. sha256sum format)
    digest = sha256(concat(lines sorted bytewise by path))

so for the deploy tree it is exactly:

    cd _build_mpy && find . -type f -printf '%P\\n' | LC_ALL=C sort \\
        | tr '\\n' '\\0' | xargs -0 sha256sum -t | sha256sum

(-t is coreutils' text mode. It is the default on Linux but not in Git Bash on
Windows, which otherwise prints "hash *path" and yields a different roll-up.)

Deliberately excluded from both digests: the generation timestamp, toolchain
versions, and the optional firmware section. A digest must depend on content
alone, or two honest builders on two machines get different numbers and the
whole exercise stops meaning anything.

Usage:
    python tools/build_manifest.py                    # JSON to stdout
    python tools/build_manifest.py -o manifest.json
    python tools/build_manifest.py --firmware ~/firmware-backups/ESP32_GENERIC-20260406-v1.28.0.bin
    python tools/build_manifest.py --check manifest.json   # rebuild? same bytes?

--check recomputes from disk and reports OK / MISMATCH / MISSING / UNEXPECTED
per file, exiting non-zero on any difference. That is the determinism test: run
build_mpy.sh again, then --check against the published manifest.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "seedwitness-manifest/1"

# Default source set: everything that determines the deploy tree. build_mpy.sh
# is in here because the source-to-bytecode mapping is its doing -- it prunes
# files, swaps in the flash-backed wordlist and generates bip39_words.bin, so a
# manifest that pinned device/ alone would leave the interesting half unpinned.
DEFAULT_SOURCES = ["device", "tools/build_mpy.sh"]

# pytest and CPython leave these behind inside device/; they are not source and
# build_mpy.sh already refuses to ship them.
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        # Chunked rather than read() so hashing the 20MB firmware .elf an
        # auditor might point this at does not balloon memory.
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(base, skip_junk):
    """Yield paths of every regular file under `base`, or just `base` itself if
    it is a file. Returned unsorted; callers sort by the recorded path."""
    base = Path(base)
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        if skip_junk:
            # in-place prune so os.walk does not descend into them at all
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if skip_junk and Path(name).suffix in SKIP_SUFFIXES:
                continue
            yield Path(dirpath) / name


def collect(base, path_root, skip_junk=True):
    """Hash every file under `base`, recording paths relative to `path_root`.

    Paths are stored POSIX-style with forward slashes even when generated on
    Windows: the manifest has to compare equal across the build host and the
    board, and the board only speaks '/'.
    """
    entries = []
    for f in walk_files(base, skip_junk):
        rel = f.resolve().relative_to(Path(path_root).resolve()).as_posix()
        entries.append({
            "path": rel,
            "size": f.stat().st_size,
            "sha256": sha256_file(f),
        })
    entries.sort(key=lambda e: e["path"].encode("utf-8"))
    return entries


def rollup(entries):
    """One digest over the sorted (hash, path) pairs. See the module docstring
    for the exact byte format; it is sha256sum's, so it stays checkable with
    coreutils if this script ever goes missing."""
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda e: e["path"].encode("utf-8")):
        h.update(("%s  %s\n" % (e["sha256"], e["path"])).encode("utf-8"))
    return h.hexdigest()


def toolchain_info():
    """Record what produced the bytecode. Informational only, and excluded from
    the digests -- but a manifest whose deploy digest differs from yours is
    almost always explained here, because a different mpy-cross emits different
    bytes from identical source."""
    info = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    try:
        out = subprocess.run([sys.executable, "-m", "mpy_cross", "--version"],
                             capture_output=True, text=True, timeout=60)
        info["mpy_cross_banner"] = (out.stdout + out.stderr).strip() or "unknown"
    except Exception as exc:  # noqa: BLE001 - never fail a manifest over this
        info["mpy_cross_banner"] = "unavailable: %s" % exc
    try:
        from importlib.metadata import version
        info["mpy_cross_package"] = version("mpy_cross")
    except Exception:  # noqa: BLE001
        info["mpy_cross_package"] = "unavailable"
    return info


def build_manifest(build_dir, sources, firmware=None, metadata=True):
    deploy = collect(build_dir, build_dir, skip_junk=False)

    stray = [e["path"] for e in deploy
             if "__pycache__" in e["path"] or e["path"].endswith(".pyc")]
    if stray:
        # Not fatal here, but it means the build tree is not what ships.
        print("WARNING: build tree contains CPython bytecode: %s"
              % ", ".join(stray), file=sys.stderr)

    source = []
    for spec in sources:
        source.extend(collect(ROOT / spec, ROOT, skip_junk=True))
    source.sort(key=lambda e: e["path"].encode("utf-8"))

    man = {
        "schema": SCHEMA,
        "deploy": {
            "note": "files as they land on the board, paths relative to /",
            "digest": rollup(deploy),
            "files": deploy,
        },
        "source": {
            "note": "inputs that determine the deploy tree, paths from repo root",
            "roots": list(sources),
            "digest": rollup(source),
            "files": source,
        },
    }

    if metadata:
        man["generated_utc"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        man["toolchain"] = toolchain_info()

    if firmware:
        fw = Path(firmware).expanduser()
        # Recorded, not attested. micropython.org publishes no checksum for
        # these images, so this pins "the image this board was flashed with",
        # nothing stronger. SECURITY.md spells out the difference.
        man["firmware"] = {
            "note": "flashed MicroPython image; see SECURITY.md, Build "
                    "verification has preconditions and limits",
            "name": fw.name,
            "size": fw.stat().st_size,
            "sha256": sha256_file(fw),
        }

    return man


def check(manifest_path, build_dir, sources):
    """Recompute from disk and diff against a published manifest."""
    man = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    fresh = build_manifest(build_dir, sources, metadata=False)

    ok = True
    for section in ("deploy", "source"):
        want = {e["path"]: e for e in man.get(section, {}).get("files", [])}
        got = {e["path"]: e for e in fresh[section]["files"]}
        print("[%s]" % section)
        for path in sorted(set(want) | set(got)):
            if path not in got:
                print("  MISSING     %s" % path)
                ok = False
            elif path not in want:
                print("  UNEXPECTED  %s" % path)
                ok = False
            elif want[path]["sha256"] != got[path]["sha256"]:
                print("  MISMATCH    %s" % path)
                print("              manifest %s" % want[path]["sha256"])
                print("              on disk  %s" % got[path]["sha256"])
                ok = False
            else:
                print("  OK          %s" % path)

        want_digest = man.get(section, {}).get("digest")
        got_digest = fresh[section]["digest"]
        print("  digest manifest %s" % want_digest)
        print("  digest on disk  %s" % got_digest)
        if want_digest != got_digest:
            ok = False

    tc = man.get("toolchain")
    if tc and not ok:
        # A deploy mismatch with identical source almost always means a
        # different cross-compiler, so put the two toolchains side by side
        # before anyone starts hunting for a compromise.
        print("\nmanifest was built with: %s" % tc.get("mpy_cross_banner"))
        print("this machine has:        %s"
              % toolchain_info()["mpy_cross_banner"])

    print("\n%s" % ("MATCH: rebuild reproduced the manifest"
                    if ok else "DIFFERENT: see the lines above"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emit or check a seedwitness build manifest.")
    ap.add_argument("--build-dir", default=str(ROOT / "_build_mpy"),
                    help="deploy tree produced by tools/build_mpy.sh")
    ap.add_argument("--source", action="append", default=None,
                    metavar="PATH",
                    help="repo-relative source path to pin (repeatable); "
                         "default: %s" % " ".join(DEFAULT_SOURCES))
    ap.add_argument("--firmware", metavar="PATH",
                    help="also record the SHA-256 of a flashed firmware image")
    ap.add_argument("--no-metadata", action="store_true",
                    help="omit timestamp and toolchain so two manifests of the "
                         "same tree are byte-identical")
    ap.add_argument("-o", "--out", metavar="FILE",
                    help="write here instead of stdout")
    ap.add_argument("--check", metavar="MANIFEST",
                    help="recompute from disk and diff against this manifest")
    args = ap.parse_args(argv)

    sources = args.source or DEFAULT_SOURCES
    build_dir = Path(args.build_dir)
    if not build_dir.is_dir():
        print("no build tree at %s -- run tools/build_mpy.sh first" % build_dir,
              file=sys.stderr)
        return 2

    if args.check:
        return check(args.check, build_dir, sources)

    man = build_manifest(build_dir, sources, args.firmware,
                         metadata=not args.no_metadata)
    # Stable key order and a trailing newline: this file is meant to be
    # committed, diffed and eyeballed, not just parsed.
    text = json.dumps(man, indent=2, sort_keys=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("wrote %s" % args.out, file=sys.stderr)
        print("deploy digest %s" % man["deploy"]["digest"], file=sys.stderr)
        print("source digest %s" % man["source"]["digest"], file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
