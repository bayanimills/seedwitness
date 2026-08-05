# Vendored dependency: embit

**Source:** https://github.com/diybitcoinhardware/embit
**Commit:** `fff7ffa43f6ce088c5ba22cb3877a122bf01dc96` (master, 2026-06-02)
**Fetched:** 2026-08-01
**License:** MIT (Copyright (c) 2020 Stepan Snigirev)

```
MIT License

Copyright (c) 2020 Stepan Snigirev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## What's here vs. upstream

This directory is **not** a full copy of embit — it's pruned to what
`seedwitness` actually imports (bip39/bip32/script/ec/base58/bech32/hashes/
compact/networks/misc/base + the util/ secp256k1 backends + the English
BIP39 wordlist). Removed entirely, since this is a generation +
verification device, not a signer: `psbt.py`, `psbtview.py`,
`finalizer.py`, `transaction.py`, `bip85.py`, `slip39.py`, the `liquid/`
and `descriptor/` subpackages, and the non-English/non-BIP39 wordlists.

No other modification was made to any retained file (confirmed by diffing
against the fetched commit at build time).

## Why a single NOTICE instead of a per-file header

`drivers/ili9341.py`, `drivers/xpt2046.py`, and every file in `mp_shims/`
carry individual attribution headers (small vendored units, one file each).
embit is ~6,300 lines across ~20 files pulled from one upstream repo at one
commit — a single NOTICE here, rather than the same boilerplate repeated at
the top of every file, keeps that provenance information in one place
without cluttering the retained source. The project keeps vendored units
diffable against their recorded upstream revisions; the complete dependency
and licence inventory is in `THIRD_PARTY_NOTICES.md` at the repository root.
