"""Pin the set of tests allowed to skip.

A skipped test reports nothing, forever. That silence is exactly how two
specifications sat in this repo for three weeks describing behaviour nobody
built, and how the implementation behind one of them was lost without a single
red build.

They are committed now and deliberately skipped, which would trade one silence
for another. So the skip set is pinned instead. `expected_skips.txt` lists every
test allowed to skip, and this guard fails the run on either kind of drift:

  * a skip that is not on the list, so a real test cannot quietly stop running;
  * a listed test that did NOT skip, which means its feature landed and the
    guard around it should come off.

Entries naming tests that were not collected are ignored, so the list does not
have to move in lockstep with branches that add or remove spec files.
"""

from pathlib import Path

_LIST = Path(__file__).parent / "expected_skips.txt"

_skipped = set()
_collected = set()


def _norm(nodeid):
    return nodeid.replace("\\", "/")


def _expected():
    if not _LIST.exists():
        return set()
    out = set()
    for line in _LIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(_norm(line))
    return out


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        _collected.add(_norm(item.nodeid))


def pytest_collectreport(report):
    # A module-level pytest.skip(allow_module_level=True) surfaces here, not as
    # a test report, and its nodeid is the file path.
    if report.skipped:
        nodeid = _norm(report.nodeid)
        _skipped.add(nodeid)
        _collected.add(nodeid)


def pytest_runtest_logreport(report):
    if report.when == "setup" and report.skipped:
        _skipped.add(_norm(report.nodeid))


def pytest_sessionfinish(session, exitstatus):
    expected = _expected()
    unlisted = sorted(_skipped - expected)
    # Listed, actually collected, and yet did not skip: the feature exists now.
    revived = sorted((expected & _collected) - _skipped)

    if not unlisted and not revived:
        return

    print("\n" + "=" * 70)
    print("skip guard failed")
    if unlisted:
        print("\nThese tests skipped but are not in tests/expected_skips.txt.")
        print("A test that stops running without anyone deciding so is a")
        print("regression, not a convenience. Fix it, or add it with a reason:")
        for nodeid in unlisted:
            print("  " + nodeid)
    if revived:
        print("\nThese are listed as expected skips but ran. Their feature has")
        print("landed, so remove the skip guard in the file and drop the line")
        print("from tests/expected_skips.txt:")
        for nodeid in revived:
            print("  " + nodeid)
    print("=" * 70)

    if exitstatus == 0:
        session.exitstatus = 1
