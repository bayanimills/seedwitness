"""Deployed over embit/wordlists/bip39.py on the device only.

Upstream that module is a literal list of 2048 strings; here it resolves to
the flash-backed reader instead, so the words live on the filesystem rather
than the heap. Same WORDLIST name and same WordlistBase interface, so
embit/bip39.py and everything above it are untouched.

This mirrors what embit's own `ubip39` does, minus the custom-firmware
requirement. See mp_shims/wordlist_flash/flash_wordlist.py.
"""
from .base import WordlistBase as _WordlistBase

from flash_wordlist import WORDS as _WORDS

WORDLIST = _WordlistBase(_WORDS)
