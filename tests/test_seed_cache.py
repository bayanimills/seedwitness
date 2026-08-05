"""The seed cache is a correctness-and-privacy surface, not just a speedup.

On real hardware the PBKDF2 stretch costs ~618s, so derive_address() caches
the resulting seed and reuses it when only the path or index changes. That
makes two things worth pinning down in tests:

  - it must never hand back a seed derived from a DIFFERENT mnemonic or
    passphrase (a stale hit here would silently show an address belonging to
    another wallet -- the exact failure this device exists to catch)
  - it must be clearable, so a seed doesn't outlive the session that made it
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))

import pytest  # noqa: E402

from seedwitness import derive as drv  # noqa: E402
from seedwitness.derive import (  # noqa: E402
    PATH_TEMPLATES,
    clear_seed_cache,
    derive_address,
)

MNEMONIC_A = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)
MNEMONIC_B = (
    "legal winner thank year wave sausage worth useful legal winner "
    "thank yellow"
)

BIP84 = next(t for t in PATH_TEMPLATES if t.bip == 84)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_seed_cache()
    yield
    clear_seed_cache()


def test_repeat_derivation_reuses_cached_seed():
    derive_address(MNEMONIC_A, BIP84, index=0)
    first = drv._cached_seed
    assert first is not None
    derive_address(MNEMONIC_A, BIP84, index=1)
    # identity, not equality: proves it was not recomputed
    assert drv._cached_seed is first


def test_changing_index_still_changes_the_address():
    """The cache must speed up derivation without flattening the result --
    reusing the seed but still walking a different path."""
    _, addr0 = derive_address(MNEMONIC_A, BIP84, index=0)
    _, addr1 = derive_address(MNEMONIC_A, BIP84, index=1)
    assert addr0 != addr1
    assert addr0 == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def test_different_mnemonic_is_not_served_from_cache():
    _, addr_a = derive_address(MNEMONIC_A, BIP84, index=0)
    seed_a = drv._cached_seed
    _, addr_b = derive_address(MNEMONIC_B, BIP84, index=0)
    assert drv._cached_seed != seed_a
    assert addr_a != addr_b


def test_different_passphrase_is_not_served_from_cache():
    """Same mnemonic, different passphrase, is a different wallet entirely."""
    _, plain = derive_address(MNEMONIC_A, BIP84, index=0)
    _, with_pass = derive_address(MNEMONIC_A, BIP84, index=0, passphrase="TREZOR")
    assert plain != with_pass


def test_clear_seed_cache_drops_the_seed():
    derive_address(MNEMONIC_A, BIP84, index=0)
    assert drv._cached_seed is not None
    clear_seed_cache()
    assert drv._cached_seed is None
    assert drv._cached_key is None


def test_progress_is_reported_once_on_a_cache_hit():
    """The UI closes out its 'deriving' frame from the progress callback, so a
    cache hit must still call it rather than leaving the bar part-drawn."""
    derive_address(MNEMONIC_A, BIP84, index=0)
    calls = []
    derive_address(MNEMONIC_A, BIP84, index=2,
                   progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 1)]


def test_cache_survives_a_template_change():
    """Switching script type reuses the same seed -- only the path differs."""
    derive_address(MNEMONIC_A, BIP84, index=0)
    seed = drv._cached_seed
    bip44 = next(t for t in PATH_TEMPLATES if t.bip == 44)
    _, addr = derive_address(MNEMONIC_A, bip44, index=0)
    assert drv._cached_seed is seed
    assert addr == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"
