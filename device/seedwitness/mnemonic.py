"""Entropy -> BIP39 mnemonic, plus the bit-level breakdown of the final
(checksum) word used by the "watch the key develop" screens.

Wraps `embit.bip39` (vendored in device/embit/), the same audited library
SeedSigner itself depends on, rather than reimplementing wordlist/checksum
arithmetic. See device/seedwitness/compat.py for what makes embit runnable
on stock MicroPython in the first place.

Plain class, not @dataclass -- see entropy.py's docstring for why.
"""
from embit import bip39

from .entropy import WORD_COUNT_TO_ENTROPY_BITS


class ChecksumBreakdown:
    def __init__(self, word_index, leftover_entropy_bits, checksum_bits, word):
        self.word_index = word_index  # 1-based position of the checksum-bearing word
        self.leftover_entropy_bits = leftover_entropy_bits  # raw entropy bits folded in
        self.checksum_bits = checksum_bits  # BIP39 checksum bits folded in
        self.word = word


def _normalize(mnemonic):
    """Collapse any whitespace run to single spaces, strip ends. embit's own
    bip39.mnemonic_to_bytes/mnemonic_to_seed split on a literal single space
    (`mnemonic.split(" ")`), not Python's whitespace-collapsing str.split(),
    so an unnormalized string (double space, tab, leading/trailing space)
    would otherwise either fail validation that should have passed, or --
    worse, in mnemonic_to_seed's case -- silently derive a *different* seed
    than the canonical single-spaced form, no error raised at all. Not
    reachable through this app's own UI today (WordEntryScreen always builds
    a clean " ".join(words)), but every function here that accepts a
    caller-supplied mnemonic string normalizes at the boundary rather than
    relying on every future caller to already be careful."""
    return " ".join(mnemonic.split())


def entropy_to_mnemonic(entropy):
    return bip39.mnemonic_from_bytes(entropy)


def mnemonic_to_entropy(mnemonic):
    """The raw entropy bytes the mnemonic encodes: the exact inverse of
    entropy_to_mnemonic, checksum-verified by embit on the way through.
    This is the CompactSeedQR payload -- 16 bytes for 12 words, 32 for 24."""
    return bip39.mnemonic_to_bytes(_normalize(mnemonic))


def word_indices(mnemonic):
    """0-based wordlist index of each word, for Standard SeedQR export.

    Binary search per word, same reason as words_with_prefix: on-device the
    wordlist is flash-backed, so a .index() scan would be up to 2048 file
    reads per word. The error message deliberately does not echo the word:
    secrets do not belong in exception messages, logs or diagnostics even
    though the crash screen itself displays only the exception type.
    """
    wl = bip39.WORDLIST
    n = len(wl)
    out = []
    for word in _normalize(mnemonic).split():
        lo = 0
        hi = n
        while lo < hi:
            mid = (lo + hi) >> 1
            if wl[mid] < word:
                lo = mid + 1
            else:
                hi = mid
        if lo >= n or wl[lo] != word:
            raise ValueError("word not in the BIP39 wordlist")
        out.append(lo)
    return out


def words_with_prefix(prefix, limit=4):
    """Wordlist entries starting with `prefix`, for the manual-entry keyboard.

    Binary search rather than a scan, which matters on-device: the wordlist
    lives on flash there (see mp_shims/wordlist_flash/), so the obvious
    `[w for w in WORDLIST if w.startswith(prefix)]` would mean 2048 file reads
    on every keystroke. This costs ~11 reads instead. The BIP39 wordlist is
    specified in alphabetical order, and that ordering is asserted at build
    time when the flash file is generated.

    Works unchanged on desktop, where WORDLIST is an ordinary list -- it only
    relies on indexing and len(), which both backings provide.
    """
    wl = bip39.WORDLIST
    n = len(wl)
    if not prefix:
        return [wl[i] for i in range(min(limit, n))]
    # lower bound: first index whose word sorts >= prefix
    lo = 0
    hi = n
    while lo < hi:
        mid = (lo + hi) >> 1
        if wl[mid] < prefix:
            lo = mid + 1
        else:
            hi = mid
    out = []
    i = lo
    while i < n and len(out) < limit:
        word = wl[i]
        if not word.startswith(prefix):
            break
        out.append(word)
        i += 1
    return out


def checksum_breakdown(mnemonic):
    mnemonic = _normalize(mnemonic)
    words = mnemonic.split()
    word_count = len(words)
    entropy_bits = WORD_COUNT_TO_ENTROPY_BITS[word_count]
    checksum_bits = entropy_bits // 32
    bits_in_prior_words = (word_count - 1) * 11
    leftover_entropy_bits = entropy_bits - bits_in_prior_words
    assert leftover_entropy_bits + checksum_bits == 11

    return ChecksumBreakdown(
        word_index=word_count,
        leftover_entropy_bits=leftover_entropy_bits,
        checksum_bits=checksum_bits,
        word=words[-1],
    )


def mnemonic_to_seed(mnemonic, passphrase="", progress=None):
    """`progress`, if given, is called as progress(done, total) during the
    PBKDF2 stretch so a caller can show something moving. This is the single
    slowest operation in the whole app -- 885s measured on a real CYD at
    160MHz -- so a blocking call with no feedback is indistinguishable from a
    crash to anyone holding the device.

    The hook is installed on the pbkdf2 shim module rather than passed down,
    because the call actually goes through vendored embit, which invokes
    hashlib.pbkdf2_hmac() with a fixed signature (see pbkdf2.py). Saved and
    restored so this never leaks into an unrelated later call.
    """
    if progress is None:
        return bip39.mnemonic_to_seed(_normalize(mnemonic), password=passphrase)
    try:
        import pbkdf2  # device-only flattened shim; absent on desktop CPython
    except ImportError:
        return bip39.mnemonic_to_seed(_normalize(mnemonic), password=passphrase)
    previous = pbkdf2.progress_cb
    pbkdf2.progress_cb = progress
    try:
        return bip39.mnemonic_to_seed(_normalize(mnemonic), password=passphrase)
    finally:
        pbkdf2.progress_cb = previous


def mnemonics_match(a, b):
    """Whitespace-normalized comparison, used by the backup-confirmation screen
    to check a re-typed mnemonic against the one just generated."""
    return a.split() == b.split()


def validate_mnemonic(mnemonic):
    mnemonic = _normalize(mnemonic)
    words = mnemonic.split()
    if len(words) != 12 and len(words) != 24:
        return False
    try:
        bip39.mnemonic_to_bytes(mnemonic)
        return True
    except ValueError:
        return False
