"""Device code must not call methods MicroPython does not have.

This exists because of a shipped crash. Three screens called
``str.capitalize()``. MicroPython has no such method, so every one of them
raised AttributeError on the board, on the screen that shows a completed
ceremony. All 624 desktop tests passed, because CPython has the method: the
suite is structurally incapable of catching this class, which is exactly what
PLAN.md section 7 and SECURITY.md R5 say about device_tests being mandatory
and never having been run.

A static scan is not a substitute for running the code on real MicroPython.
It is the cheap half that runs on every commit, and it turns a whole class of
"passes on the desktop, dies on the board" into a test failure.

The list is deliberately conservative: only names verified absent from the
MicroPython 1.28 ESP32 port this project targets. A false positive here would
be worse than a gap, because it would teach people to add exemptions.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVICE = ROOT / "device"

# Attribute calls that exist in CPython and not on this MicroPython port.
# Verified on the board: str has no capitalize/title/casefold/removeprefix/
# removesuffix/expandtabs/swapcase/format_map/isprintable/isidentifier.
FORBIDDEN = {
    "capitalize": "MicroPython str has no capitalize(); use s[:1].upper() + s[1:]",
    "title": "MicroPython str has no title()",
    "casefold": "MicroPython str has no casefold(); use lower()",
    "removeprefix": "MicroPython str has no removeprefix()",
    "removesuffix": "MicroPython str has no removesuffix()",
    "expandtabs": "MicroPython str has no expandtabs()",
    "swapcase": "MicroPython str has no swapcase()",
    "format_map": "MicroPython str has no format_map()",
    "isprintable": "MicroPython str has no isprintable()",
    "isidentifier": "MicroPython str has no isidentifier()",
}

# embit is vendored third-party code, diffable against upstream. Patching it
# would destroy that property (see README), so it is out of scope here.
SKIP_DIRS = {"embit", "__pycache__"}


def _device_sources():
    for path in sorted(DEVICE.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def test_there_are_device_sources_to_scan():
    """Guards the guard: a broken glob would make every scan below vacuous."""
    files = list(_device_sources())
    assert len(files) > 10, files


@pytest.mark.parametrize("path", list(_device_sources()),
                         ids=lambda p: str(p.relative_to(DEVICE)))
def test_no_cpython_only_method_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN:
            bad.append("%s:%d  .%s()  %s"
                       % (path.relative_to(ROOT), node.lineno, func.attr,
                          FORBIDDEN[func.attr]))
    assert not bad, (
        "device code calls methods MicroPython does not have. The desktop "
        "suite cannot catch these; the board raises AttributeError.\n  "
        + "\n  ".join(bad))


def test_the_scanner_actually_detects_the_pattern():
    """Guards the guard. If the AST walk stopped matching, every file above
    would pass silently, which is how the original bug shipped."""
    tree = ast.parse("x = 'a b'.capitalize()\n")
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in FORBIDDEN]
    assert len(hits) == 1


def test_the_replacement_helper_behaves_like_capitalize():
    sys.path.insert(0, str(DEVICE))
    from seedwitness import entropy as ent
    for source in ent.SOURCES.values():
        got = ent.plural_unit_title(source)
        assert got == ent._plural_unit(source).capitalize(), source.name
        assert got[0].isupper(), got
