# Contributing to SeedWitness

Thank you for helping improve SeedWitness. The project tests deterministic
Bitcoin seed derivation, so correctness, reproducibility and clear scope
matter more than feature count or speed of change.

## Before you start

- Search the existing issues before opening a new one.
- For a substantial feature or user-flow change, open an issue first so the
  security and hardware trade-offs can be discussed before implementation.
- Never post a real seed phrase, BIP39 passphrase, private key, roll sequence,
  wallet descriptor, or other wallet material. Use test data expected to
  control no real-world value. Account xpubs do not enable spending, but they
  are still sensitive financial metadata and should not be posted.
- Do not report a vulnerability in a public issue. Follow the instructions in
  [SECURITY.md](SECURITY.md).

## Development setup

SeedWitness's desktop tests require Python 3.10 or later.

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m pytest tests/ -q
```

Activate the virtual environment first if desired:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS or Linux: `source .venv/bin/activate`

The simulator executes the real UI code against a PIL-backed canvas:

```bash
python sim/capture_screens.py
```

Building the deploy tree additionally requires `mpy-cross` and a Bash shell:

```bash
python -m pip install mpy-cross==1.27.0.post2
./tools/build_mpy.sh
```

The build normally refuses uncommitted changes to its device inputs. For a
local development build only, run this in Bash:

```bash
SEEDWITNESS_ALLOW_DIRTY=1 ./tools/build_mpy.sh
```

Never use that override for a build or manifest presented as a release
artifact. Hardware deployment and verification are documented in the
[README](README.md#development).

## Making a change

- Keep changes focused and explain the user-visible or security consequence.
- Add or update tests for changed behaviour. A regression test should fail for
  the old behaviour for the reason claimed.
- Update the README or design documentation when behaviour, limitations,
  installation, or verification steps change.
- Do not commit generated `_build_mpy/` files, virtual environments, caches,
  serial captures, or real wallet material.
- If physical hardware matters to the result, state the board revision,
  MicroPython version, serial-port tooling, and what was observed. Do not imply
  hardware validation when only the simulator was run.
- A published `manifest.json` must describe committed source built without the
  dirty-tree override. Call out manifest impact in the pull request rather
  than regenerating it from an uncommitted tree.

## Pull requests

In the pull request, include:

1. the problem and why the change is the smallest correct solution;
2. security, privacy, compatibility, and recovery implications;
3. the exact tests and hardware checks run; and
4. screenshots for visible UI changes, with no wallet material in them.

By contributing, you agree that your contribution is licensed under the
project's [MIT License](LICENSE). Do not add third-party material without its
licence and provenance; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
