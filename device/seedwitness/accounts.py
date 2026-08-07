"""Enrolled watch-only accounts: account xpubs persisted to flash.

The point of enrolment
----------------------
Entering a mnemonic costs 575s of PBKDF2 on this board and puts a secret in
RAM. Enrolment pays that once, keeps the account xpub, and forgets the
mnemonic. Every verification afterwards runs from the xpub at 2.9s with no
secret on the device at all.

That is the security argument, not a speed one. With no secret at rest, a
tampered board, an undisableable radio, a missing secure element and a stolen
unit all stop being "funds gone" and become "someone learned which addresses
are yours". No amount of firmware verification buys a reduction of that size.

What is stored, and what it costs you
-------------------------------------
An account xpub is public by construction, so writing it to flash is not a key
leak. It is a PRIVACY leak: anyone holding it can derive every address in that
account, past and future, and link them together. Forever, and irrevocably --
you cannot rotate an xpub without moving the coins.

So this file stores nothing secret and is still worth protecting, and every
screen offering enrolment has to say so in those terms. "Not a key" is not the
same as "harmless", and a user who hears only the first half has been misled.

Storage format is plain JSON, deliberately. It is a handful of records read
once per session; a binary format would save a few hundred bytes of flash and
cost anyone auditing this device the ability to read what their own hardware
is holding with `mpremote cat`.

A record may also carry "marks": a sorted list of address indices the user
has ticked on the verification screens. The mark is the user's own
bookkeeping and the device stores NO meaning with it -- not "verified", not
"funded", nothing the device cannot know. It lives inside the account's own
record so deleting the account takes its marks with it.
"""
import json

try:
    import os
    _stat = os.stat
except ImportError:  # pragma: no cover
    _stat = None

PATH = "/accounts.json"

# Five, because the accounts list renders exactly five rows and does not
# paginate: the store and the screen must agree on what "full" means, and
# five is the screen's number. The old cap of 16 let records 6..16 enrol
# onto flash and then fall off the bottom of a list that never shows them --
# enrolled but unreachable, so impossible to see, verify against, rename,
# or above all DELETE. An xpub on flash is a deliberate, permanent privacy
# exposure the user accepted at enrolment; one they cannot reach to delete
# is that exposure with the consent removed. (A store already holding more
# than five, written by an older build, still loads whole; the list shows
# its first five and each delete surfaces the next hidden record, so every
# record stays reachable eventually. Only NEW enrolments are refused.)
MAX_ACCOUNTS = 5


def _p(path):
    """Resolve the store path at CALL time, not at def time.

    `def add(..., path=PATH)` binds the module constant when the function is
    defined, so reassigning accounts.PATH afterwards silently has no effect --
    every write still lands on the real filesystem root. Harmless on the
    board, where PATH never changes; on a desktop it means a test that thinks
    it redirected the store is actually writing to /accounts.json, which on
    Windows fails outright and on Unix would quietly succeed.
    """
    return PATH if path is None else path


def _read_raw(path=None):
    path = _p(path)
    try:
        f = open(path)
    except OSError:
        return []
    try:
        data = json.load(f)
    except (ValueError, OSError):
        # A truncated or corrupt file must not wedge the device on boot. An
        # empty list is recoverable (re-enrol); an exception out of a screen
        # constructor is not.
        return []
    finally:
        f.close()
    return data if isinstance(data, list) else []


def load(path=None):
    """Every enrolled account. Records missing required fields are dropped
    rather than returned half-built, so callers never have to guard.

    The TYPES are checked, not just the keys. This file is unauthenticated
    data read back from flash, and a present-but-wrong-typed field is exactly
    as damaging as a missing one: {"xpub": 5} passed the old key-only check,
    then raised AttributeError inside AccountListScreen.draw() when
    xpub_fingerprint called .encode() on an int. Because that crash happens in
    draw(), it repeated on every entry to Accounts, so the damaged record
    could never be reached to DELETE it. An enrolled xpub the user cannot
    remove is the permanent privacy exposure MAX_ACCOUNTS exists to prevent,
    with the consent removed.

    Dropped records are purged by the next save(), the same policy the
    missing-field case already had.
    """
    out = []
    for rec in _read_raw(path):
        if not isinstance(rec, dict):
            continue
        if not isinstance(rec.get("xpub"), str):
            continue
        if not isinstance(rec.get("bip"), int) or isinstance(rec.get("bip"), bool):
            continue
        # `account` may legitimately be absent: flow_accounts routes that to
        # DamagedAccountScreen rather than inventing a path. Present-but-wrong
        # must not outrank absent, so a bad value is dropped to that same path.
        #
        # bool is excluded for the same reason it is on `bip`: True is an int
        # in Python, and it rendered as "m/84h/0h/1h". Negative is excluded
        # because it rendered "m/84h/0h/-1h". Derivation reads the xpub and
        # ignores this field, so a bad value does not produce a wrong address,
        # it produces a wrong PATH LINE, on the one line the user is told is
        # their check that they are looking at the right wallet.
        acc = rec.get("account")
        if "account" in rec and (not isinstance(acc, int)
                                 or isinstance(acc, bool) or acc < 0):
            del rec["account"]
        # `label` is the field this check originally missed, and missing it
        # recreated the exact crash the type validation was added to stop:
        # AccountListScreen does `rec.get("label") or "account"`, so any TRUTHY
        # non-string flows into the row button and is sliced by canvas.text.
        # {"label": 5} raises TypeError and {"label": {...}} raises KeyError,
        # both inside draw(), so they repeat on every entry to Accounts and the
        # record can never be reached to delete. Falsy wrong types (0, null,
        # false) were harmless only by luck, via the `or` fallback.
        if "label" in rec and not isinstance(rec["label"], str):
            del rec["label"]
        out.append(rec)
    return out


def save(records, path=None):
    path = _p(path)
    f = open(path, "w")
    try:
        json.dump(records, f)
    finally:
        f.close()


def add(label, xpub, bip, account=0, network="main", path=None):
    """Enrol an account. Returns the stored record.

    Re-enrolling the same xpub updates its label instead of appending a
    duplicate: the same account under two names is exactly the ambiguity a
    verification device should not introduce.
    """
    records = load(path)
    for rec in records:
        if rec["xpub"] == xpub:
            rec["label"] = label
            save(records, path)
            return rec
    if len(records) >= MAX_ACCOUNTS:
        # Backstop, not the user-facing refusal: EnrolScreen refuses a full
        # store on-screen before this can fire, because an exception out of
        # a tap handler lands on the CrashScreen and ends the session --
        # destroying the multi-minute derivation the user just paid for.
        # This raise only guarantees the store can never grow past the cap
        # through some future caller that forgets to check. The update-in-
        # place loop above runs first, deliberately, so relabelling an
        # already-enrolled account still works on a full (or over-cap) store.
        raise ValueError("at most %d accounts" % MAX_ACCOUNTS)
    rec = {"label": label, "xpub": xpub, "bip": bip,
           "account": account, "network": network}
    records.append(rec)
    save(records, path)
    return rec


def _clean_marks(raw):
    """A record's mark list, sanitised. Anything that is not a list of
    non-negative ints degrades to whatever can be salvaged, never an
    exception: the store is plain unauthenticated JSON on flash, and a
    damaged field must not wedge a screen constructor any more than a
    damaged file may (see _read_raw)."""
    if not isinstance(raw, list):
        return []
    out = []
    for i in raw:
        # bool is an int subclass; `true` in a damaged file is not an index
        if isinstance(i, bool) or not isinstance(i, int):
            continue
        if i >= 0 and i not in out:
            out.append(i)
    out.sort()
    return out


def marks(xpub, path=None):
    """The user's ticked address indices for one account, sorted.

    The tick means whatever the user decided it means -- checked against a
    wallet, funded, anything. This device only records that (account, index)
    was ticked; no caller may present it as a verdict. An unknown xpub is
    simply an account with no marks."""
    for rec in load(path):
        if rec["xpub"] == xpub:
            return _clean_marks(rec.get("marks"))
    return []


def toggle_mark(xpub, index, path=None):
    """Flip the tick on (account, index). Returns the new state.

    Stored inside the account's own record, so nothing can orphan a mark:
    delete the account and its marks go with it. Toggling on an xpub that is
    not enrolled changes nothing and returns False."""
    records = load(path)
    for rec in records:
        if rec["xpub"] == xpub:
            m = _clean_marks(rec.get("marks"))
            if index in m:
                m.remove(index)
            else:
                m.append(index)
                m.sort()
            rec["marks"] = m
            save(records, path)
            return index in m
    return False


def remove(xpub, path=None):
    records = [r for r in load(path) if r["xpub"] != xpub]
    save(records, path)
    return records


def clear(path=None):
    save([], path)
