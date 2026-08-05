"""Diceware passphrase generation, from the same physical dice as the seed.

Why generate rather than let the user type one
----------------------------------------------
This is not a usability preference. Free-text passphrase entry cannot be made
correct on this port.

BIP39 requires the passphrase to be NFKD-normalised before it reaches PBKDF2.
MicroPython has no `unicodedata`, so NFKD is not implementable here. A user who
typed anything outside plain ASCII would get a different wallet on this device
than in every other wallet, discover it only when recovering, and lose the
funds. Generating from a wordlist sidesteps the whole problem: the output is
lowercase ASCII by construction, so normalisation is a no-op and this device
and any other agree by definition.

Why the EFF large list, and why a D6 only
-----------------------------------------
7776 words, because 6**5 == 7776 exactly. Five D6 rolls select one word with a
perfectly uniform mapping: no modulo bias, no rejection sampling, no
conditional re-rolling for the user to get wrong. That exact fit is the reason
the list is that size.

Every other source is deliberately refused. 2**13 = 8192 and 8**5 = 32768 both
overshoot 7776, so a coin or a D8 would need rejection sampling, and a
ceremony that sometimes says "discard those flips and do it again" is a
ceremony people shortcut. The D6 is the one source where the honest mapping is
also the simple one.

Why not reuse the BIP39 wordlist
--------------------------------
It is already on the board, already flash-backed, and 2048 words gives a clean
11 bits. It was rejected anyway: passphrase words and seed words would then be
drawn from the same list, and the two get written on paper near each other by
the same person on the same day. Anything that makes a passphrase word look
like a seed word invites a backup where nobody can tell which is which.

Entropy
-------
log2(7776) = 12.925 bits per word. BIP39 stretches the passphrase with only
2048 rounds of HMAC-SHA512, which is weak, so the passphrase has to carry its
own weight rather than lean on the KDF:

    6 words   77.5 bits    floor, offered but not the default
    8 words  103.4 bits    default
   10 words  129.2 bits    exceeds the 128-bit seed it protects
"""
import hashlib

WORD_COUNT = 7776
ROLLS_PER_WORD = 5          # 6**5 == 7776, exactly
WORD_WIDTH = 10             # longest EFF word is 9 chars, +1 so it stays even
BITS_PER_WORD = 12.925      # log2(7776), for display only

WORDS_FILE = "/eff_words.bin"


def _p(path):
    """Resolve the wordlist path at CALL time, not at def time.

    `def read_word(..., path=WORDS_FILE)` binds the module constant when the
    function is defined, so reassigning passphrase.WORDS_FILE afterwards has
    no effect and every read still goes to the board's filesystem root. Same
    defect accounts.py had. Harmless on the device, where the path never
    changes; on the desktop it means a test that thinks it pointed at the
    build tree is really opening a file that does not exist.
    """
    return WORDS_FILE if path is None else path

# Offered lengths. 8 is the default: 103 bits is comfortably more than the
# ~2048-round KDF gives back, and short enough to write down without error.
LENGTHS = (6, 8, 10)
DEFAULT_LENGTH = 8


def bits_for(word_count):
    """Entropy of a generated passphrase, in whole bits, rounded down.

    Rounded DOWN deliberately: this number is shown to a user deciding whether
    the passphrase is strong enough, and a security figure should never round
    in the flattering direction.
    """
    return int(word_count * BITS_PER_WORD)


def rolls_needed(word_count):
    return word_count * ROLLS_PER_WORD


def word_index(rolls):
    """Five D6 rolls (each 1..6) to a word index in 0..7775.

    Base-6 with the faces mapped 1..6 -> 0..5, most significant roll first,
    which is Diceware's own convention. Uniform because 6**5 is exactly the
    list length.
    """
    if len(rolls) != ROLLS_PER_WORD:
        raise ValueError("need exactly %d rolls, got %d"
                         % (ROLLS_PER_WORD, len(rolls)))
    index = 0
    for r in rolls:
        if not (1 <= r <= 6):
            raise ValueError("passphrase rolls must be a D6 (1..6), got %d" % r)
        index = index * 6 + (r - 1)
    return index


def read_word(index, path=None):
    """One word from the flash-backed list.

    Read on demand rather than held in RAM: the list is ~76KB fixed-width and
    the board has tens of KB free. Same technique the BIP39 wordlist uses.
    """
    if not (0 <= index < WORD_COUNT):
        raise ValueError("word index out of range: %d" % index)
    f = open(_p(path), "rb")
    try:
        f.seek(index * WORD_WIDTH)
        raw = f.read(WORD_WIDTH)
    finally:
        f.close()
    end = raw.find(b"\x00")
    if end >= 0:
        raw = raw[:end]
    return raw.decode("ascii")


def words_from_rolls(rolls, path=None):
    """Whole passphrase from a flat list of D6 rolls."""
    if len(rolls) % ROLLS_PER_WORD:
        raise ValueError("roll count %d is not a multiple of %d"
                         % (len(rolls), ROLLS_PER_WORD))
    out = []
    for i in range(0, len(rolls), ROLLS_PER_WORD):
        out.append(read_word(word_index(rolls[i:i + ROLLS_PER_WORD]), path))
    return out


def to_passphrase(words):
    """The exact string handed to PBKDF2.

    Space-separated, which is Diceware's convention and what every other tool
    will expect when the user types it in to recover. This string is the
    passphrase; nothing downstream may trim, case-fold or re-join it.
    """
    return " ".join(words)


def check_value(passphrase):
    """Short fingerprint of a passphrase, for confirming it was typed the same
    as last time without displaying it.

    Not a security control and not a password check: 4 hex is 16 bits, so it
    catches typos, not attackers. It exists because changing the passphrase
    invalidates the seed cache and every derivation after it costs the full
    575 seconds. Without this, discovering a typo costs ten minutes.
    """
    h = hashlib.sha256(passphrase.encode()).digest()
    return "".join("%02x" % b for b in h[:2])
