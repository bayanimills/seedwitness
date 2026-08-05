"""BIP32 derivation for the independent-verification screen: given a mnemonic
(freshly generated OR re-entered from another wallet) and a chosen path,
derive the same address a second device should show -- so the two can be
compared by eye.

Performance note: on-device, mnemonic_to_seed() (PBKDF2-HMAC-SHA512, 2048
rounds) is the expensive step here, not the EC math -- see pbkdf2.py's
docstring. The UI shows a "deriving..." screen around this call.

Plain class, not @dataclass -- see entropy.py's docstring for why.

Import discipline (load-bearing, do not "tidy"): the embit EC chain (bip32,
script, ec, secp256k1 -- tens of KB of RAM once loaded) is imported inside
the functions that do EC math, AFTER any PBKDF2 stretch, never at module
scope. PBKDF2 is the hungriest stretch of code on this device -- 2048 rounds
of pure-Python SHA-512 bigint churn -- and it needs headroom, not EC tables:
measured on the board, running it with the EC chain already resident left
~14KB free and died with the field failure signature (MemoryError in
_sha512._transform), while deferring the chain until the seed exists gives
the stretch the ~50KB it wants and loads the EC code afterwards, into a
freshly collected heap. This module can be imported for its DATA
(PATH_TEMPLATES, SLIP132, fingerprints) without paying for embit at all,
which is what keeps the derivation menu and the accounts list cheap.
"""
import gc

from . import mnemonic as mn

# Cache of the last (mnemonic, passphrase) -> seed. Deliberately ONE entry.
#
# The seed depends only on the mnemonic and passphrase, never on the
# derivation path or address index -- but derive_address() used to recompute
# it on every call, so "check another index" paid the full PBKDF2 stretch
# again: 618s on this board, per address. Since comparing several addresses
# against another wallet is the entire point of this device, that made the
# feature close to unusable. With the cache, the first derive costs ~10
# minutes and each subsequent index costs ~11s (the EC math alone).
#
# Held as module state rather than on a screen object because the navigation
# stack rebuilds screens as you move around (AddressDisplayScreen pops back to
# a fresh AddressIndexScreen), so anything screen-scoped would be thrown away
# exactly when it's needed.
#
# Why one entry and not a dict: a seed is the most sensitive value this device
# ever holds, so keeping a growing collection of them alive is the wrong
# default. A single slot bounds the exposure, is trivially clearable, and
# still captures the only access pattern that matters (repeated derivations
# from the mnemonic currently on screen).
_cached_key = None
_cached_seed = None


def clear_seed_cache():
    """Drop the cached seed. Call when a ceremony/verification session ends so
    a seed doesn't outlive the flow that produced it (see app.reset_to_home)."""
    global _cached_key, _cached_seed
    _cached_key = None
    _cached_seed = None


def _seed_for(seed_mnemonic, passphrase, progress):
    global _cached_key, _cached_seed
    key = (seed_mnemonic, passphrase)
    if key == _cached_key and _cached_seed is not None:
        if progress is not None:
            # nothing to wait for, but let the caller close out its UI cleanly
            progress(1, 1)
        return _cached_seed
    seed = mn.mnemonic_to_seed(seed_mnemonic, passphrase, progress=progress)
    _cached_key = key
    _cached_seed = seed
    return seed


class PathTemplate:
    def __init__(self, label, bip, script_kind):
        self.label = label
        self.bip = bip
        self.script_kind = script_kind  # 'p2pkh' | 'p2sh-p2wpkh' | 'p2wpkh' | 'p2tr'


# Labels are kept short enough to fit a 240px-wide portrait screen at the 8px
# font: the longest here is 23 characters (184px), inside the 224px usable
# width. The previous, wordier labels ("BIP84 - Native SegWit (bc1q...)", 31
# chars) overflowed and were silently clipped -- see tests/test_no_text_clipping.py.
PATH_TEMPLATES = [
    PathTemplate("BIP44 Legacy (1...)", 44, "p2pkh"),
    PathTemplate("BIP49 Nested (3...)", 49, "p2sh-p2wpkh"),
    PathTemplate("BIP84 SegWit (bc1q...)", 84, "p2wpkh"),
    PathTemplate("BIP86 Taproot (bc1p...)", 86, "p2tr"),
]


def _script_for(kind, pub):
    from embit import script
    if kind == "p2pkh":
        return script.p2pkh(pub)
    if kind == "p2sh-p2wpkh":
        return script.p2sh(script.p2wpkh(pub))
    if kind == "p2wpkh":
        return script.p2wpkh(pub)
    if kind == "p2tr":
        return script.p2tr(pub)
    raise ValueError("unknown script kind %s" % kind)


def derive_address(seed_mnemonic, template, account=0, change=0, index=0,
                    network="main", passphrase="", progress=None):
    """Returns (full_derivation_path_str, address).

    `progress` is forwarded to mnemonic_to_seed() and called as
    progress(done, total) through the PBKDF2 stretch -- the only part of this
    slow enough to need a progress indicator (minutes, versus ~11s for the EC
    derivation that follows). On a cache hit there is nothing to wait for and
    progress is called once with (1, 1).

    The seed is cached across calls (see _seed_for), so changing only the
    index or the script type re-runs just the EC derivation.
    """
    coin_type = 0 if network == "main" else 1
    path = "m/%dh/%dh/%dh/%d/%d" % (template.bip, coin_type, account, change, index)

    # Seed FIRST, EC chain second -- see the module docstring. The collect
    # between them matters as much as the order: the stretch leaves the heap
    # littered with bigint garbage, and the EC modules are the next largest
    # allocation this call makes.
    #
    # `script` is imported HERE, together with bip32, not merely inside
    # _script_for where it is used. The point derivation below churns the
    # heap with bigint garbage, and a module load needs contiguous KB-scale
    # chunks -- measured on the board, deferring script.py until after the
    # EC math died with "MemoryError: allocating 1280 bytes" on the
    # ceremony-then-verify-address path. Every module this call will ever
    # need loads now, into the freshly collected heap, before the math.
    seed = _seed_for(seed_mnemonic, passphrase, progress)
    gc.collect()
    from embit import bip32, script  # noqa: F401  (script: see note above)
    from embit.networks import NETWORKS
    net = NETWORKS[network]
    root = bip32.HDKey.from_seed(seed, version=net["xprv"])
    key = root.derive(path)
    addr = _script_for(template.script_kind, key.to_public()).address(net)
    return path, addr


# ---------------------------------------------------------------------------
# The xpub path
#
# Everything above starts from a mnemonic, so everything above inherits
# PBKDF2: 575s on this board, measured, and a secret in RAM for the duration.
# Deriving from an account extended PUBLIC key skips all of it -- 2.9s, and
# nothing on the device is worth stealing.
#
# That combination is the reason this exists. It is not a performance tweak
# with a security footnote: if the device holds no secret, then a tampered
# board, the hardware radios, a missing secure element and
# a stolen unit all degrade from "funds gone" to "someone learned which
# addresses are yours". Verification cannot achieve a reduction like that;
# only not holding the secret can.
#
# The privacy cost is real and must be stated wherever this is offered: an
# account xpub reveals every address in that account, forever. It is public by
# definition, so storing it is not a key leak, but "not a key leak" is not the
# same as "harmless".
# ---------------------------------------------------------------------------

def account_xpub(seed_mnemonic, template, account=0, network="main",
                 passphrase="", progress=None):
    """Derive the account-level extended PUBLIC key. Returns (path, xpub).

    This is the one operation that still costs the full PBKDF2 stretch, and
    the point is that it is the LAST one: after this the mnemonic is not
    needed again for verification.

    The xpub is serialised with the plain `xpub` version bytes regardless of
    script type, and the script type travels separately (see accounts.py).
    SLIP-132's ypub/zpub prefixes encode the script type into the string,
    which sounds tidier and is not: the prefix zoo means the same key has four
    spellings, wallets disagree about which to show, and a user comparing two
    correct strings sees a mismatch. Storing the kind as its own field keeps
    one canonical serialisation and no guessing.
    """
    coin_type = 0 if network == "main" else 1
    path = "m/%dh/%dh/%dh" % (template.bip, coin_type, account)
    # Same order as derive_address: stretch, collect, then the EC chain.
    seed = _seed_for(seed_mnemonic, passphrase, progress)
    gc.collect()
    from embit import bip32
    from embit.networks import NETWORKS
    net = NETWORKS[network]
    root = bip32.HDKey.from_seed(seed, version=net["xprv"])
    return path, root.derive(path).to_public().to_base58(version=net["xpub"])


# SLIP-132 version prefixes, keyed by BIP. Only the script types SLIP-132
# actually defines are here, and that absence is load-bearing: a zpub minted
# from a BIP44 account would tell the importing wallet to derive native SegWit
# from a legacy account -- a valid-looking key watching the wrong addresses.
# BIP44 is absent because xpub IS its SLIP-132 form; BIP86 because SLIP-132
# predates taproot and defines nothing for it.
SLIP132 = {
    49: ("ypub", b"\x04\x9d\x7c\xb2"),
    84: ("zpub", b"\x04\xb2\x47\x46"),
}


def slip132_form(xpub, bip):
    """The SLIP-132 spelling of an account xpub for its OWN script type.

    Returns (prefix_name, string), or None where no alternate exists. The
    conversion is base58check re-encoding with different version bytes -- the
    key material is untouched, which is why the account fingerprint stays
    computed from the canonical xpub and never changes with the display form.
    """
    v = SLIP132.get(bip)
    if v is None:
        return None
    from embit import base58
    return v[0], base58.encode_check(v[1] + base58.decode_check(xpub)[4:])


def address_from_xpub(xpub, template, change=0, index=0, network="main"):
    """Derive a receive address from an account xpub. No seed involved.

    Two unhardened steps instead of five hardened ones, and no PBKDF2:
    measured at 2.9s against 575s for the first address on the seed path.
    """
    from embit import bip32, script  # noqa: F401  (script: loaded before the
    #                                  EC math churns the heap; see derive_address)
    from embit.networks import NETWORKS
    net = NETWORKS[network]
    key = bip32.HDKey.from_base58(xpub).derive("m/%d/%d" % (change, index))
    # No .to_public() here: the key is already public and embit raises
    # HDError("Already public") rather than returning self, so the call that
    # is correct on the seed path is an error on this one.
    return _script_for(template.script_kind, key).address(net)


def xpub_fingerprint(xpub):
    """Short human-comparable id for an xpub.

    Not the BIP32 parent fingerprint -- that is 4 bytes of HASH160 chosen for
    machines. This is for a person checking on a 240px screen that the account
    they are about to verify against is the one they enrolled, so it is a
    hash of the serialised string itself: whatever they can see is what gets
    hashed, with no encoding step in between to disagree about.
    """
    import hashlib
    h = hashlib.sha256(xpub.encode()).digest()
    return "".join("%02x" % b for b in h[:4])
