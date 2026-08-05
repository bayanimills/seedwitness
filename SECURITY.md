# Security policy

This document says what is true of the current SeedWitness code and hardware.
It is not a changelog or a list of every defect the project has fixed. Resolved
vulnerabilities belong in the relevant commit, release notes, or a published
GitHub Security Advisory, where affected versions and the fix can be stated
accurately.

SeedWitness is intended for reproducible tests using data expected to control
no real-world value. It is not intended to process a live wallet backup or
generate a wallet that will be funded. It cannot distinguish test material
from live wallet material.

Treat an unexplained derivation result, unexpected persistence of entered
material, or failure of a verification control as a security issue.

## Supported versions

| Version or build | Security fixes |
|---|---|
| `0.1.0` | Supported |
| Untagged commits, locally modified builds, and mismatched manifests | Not supported |

A manifest describes one exact build. A fix on `main` does not change an older
device, and a passing manifest from one commit says nothing about a different
commit. Support applies only to the exact source and manifest named in the
table.

## Reporting a vulnerability

Please report potential vulnerabilities privately. On the repository's
**Security** page, use **Report a vulnerability**. Do not put vulnerability details, real
seed words, passphrases, roll strings, xpubs, addresses, flash dumps, or memory
dumps in a public issue.

If GitHub does not show that control, open a public issue containing
no vulnerability details and ask the maintainer for a private contact method.
No private email address is currently published for this project.

A useful report includes:

- the commit, manifest digest, MicroPython image, and CYD board revision used;
- the security impact and whether seed confidentiality or address correctness
  is affected;
- minimal reproduction steps using test-only data;
- the smallest proof-of-concept or failing test that demonstrates the issue;
- any known workaround; and
- whether the issue or proof has already been disclosed elsewhere.

Use public issues for ordinary bugs that do not expose secrets, produce a
confidently wrong security result, or bypass a security boundary.

This is a volunteer-maintained project and cannot promise an acknowledgement
or remediation deadline. Please use the private advisory thread to agree on a
disclosure timeline before publishing details. Maintainers should credit
reporters in a published advisory when requested and when GitHub supports the
credit.

## Security model

### In scope

SeedWitness is intended to compare deterministic results from test entropy.
Enter the same input into SeedWitness and another device or implementation
following the same SHA-256-to-BIP39 procedure, use the same settings, and
compare the resulting words and addresses.

The physical input is the only entropy input. Reproducibility, source
inspection and external build verification are the project model.

### Out of scope

SeedWitness is not intended to accept a live wallet seed or passphrase, check a
wallet backup controlling real-world value, generate a wallet that will be
funded, or store an xpub connected to real financial activity.

It does not sign transactions, construct PSBTs, protect against observation of
the screen or dice, judge whether the physical rolls were fair or private,
resist a malicious chip, or remain secure after an attacker gains physical or
debug access to the board.

"Offline" describes how the owner must operate the device. It is not a
hardware-enforced air gap.

## Current security limitations

The following limitations apply to the supported SeedWitness code and
hardware.

### USB and physical access are a complete compromise

The stock MicroPython USB REPL is enabled. A computer connected over USB can
inspect memory and the filesystem, including secret material held during an
active session and enrolled account records.

Secure boot, flash encryption, JTAG lock, and UART download lock are not
enabled. The CYD has no secure element or tamper evidence. Anyone with physical
possession can read or replace the flash. `tools/verify_device.py` can detect a
changed build afterwards; it cannot prevent the change.

### The radios exist and are not hardware-disabled

The ESP32 contains Wi-Fi and Bluetooth radios. The application does not use
them, but they are not removed, fuse-disabled, or disabled by a custom
MicroPython build. Keeping the device offline therefore depends on operational
isolation and on the installed code being the code that was reviewed.

Physical antenna or radio removal is unsupported, may damage the board and may
not eliminate all RF emissions.

### Session secrets are dropped, not securely erased

During a ceremony the roll list, mnemonic, passphrase, PBKDF2 intermediates,
and cached seed can exist in the MicroPython heap. Returning home, backing out,
sleeping, and the handled-crash path drop the application's live references,
but immutable Python objects are not overwritten and normal session end does
not reboot the board. Garbage collection is not secure zeroisation.

Do not treat a return to the home screen as proof that RAM remnants are
unrecoverable.

The application is not designed to persist a mnemonic or private key to flash.
Enrolment persists an account xpub and address marks in plain, unauthenticated
JSON. An xpub cannot spend funds, but it lets anyone derive and link every
address in that account. Disclosure is permanent even after the record is
deleted.

### The device cannot establish input entropy

SHA-256 can make a constant or human-chosen input look random; it cannot create
entropy that was not in the physical rolls. The ceremony checks for several
obvious patterns and distribution anomalies before deriving a mnemonic. When
one is found, **Check Your Rolls** requires a held choice between using the
rolls and starting over. The check is advisory because a fair sequence can
legitimately look patterned.

Passing this check does not prove that a die was fair, private or actually
rolled. A copied or adversary-known sequence can look statistically ordinary,
and a modestly biased die may not be detectable in one ceremony. The user
remains responsible for the physical entropy source. The checks and their
measured false-positive bounds are documented in
[`docs/ENTROPY_CHECKS.md`](docs/ENTROPY_CHECKS.md).

### Passphrase input is deliberately limited

The current UI can generate an ASCII Diceware BIP39 passphrase from the EFF
wordlist. It cannot type or import an existing arbitrary BIP39 passphrase.
MicroPython lacks the Unicode NFKD support required to safely accept general
text. Passphrase comparisons must use the exact same test passphrase in both
implementations.

### SeedSigner is not an independent address-derivation check

SeedWitness and SeedSigner both use `embit`. Their agreement on an address can
repeat the same library defect. SeedSigner's 99-roll flow is useful for testing
the explicitly supported 99-roll interoperability path, but address derivation
should also be checked with an unrelated implementation, offline, as described
in the README.

### Build verification has preconditions and limits

The on-device **Verify Build** fingerprint is self-attestation. Compromised
software can display an expected value, so this screen catches deployment drift
but does not establish that the device is trustworthy.

`tools/verify_device.py` is the stronger check because it can read flash through
the ESP32 ROM bootloader. Its result is only as strong as its inputs and mode:

- obtain the reference MicroPython image independently from the official
  MicroPython site; the tool compares the supplied file but does not establish
  that file's provenance for you;
- review the source commit and its exact published manifest rather than
  accepting both from the device vendor without inspection;
- install `littlefs-python` so the filesystem tier is parsed externally;
  fallback enumeration through the running firmware is weaker and is labelled
  as such; and
- repeat the external check after the device has left your custody or whenever
  its state is in doubt.

A successful check proves that the checked firmware regions and application
files match the supplied references. Mutable NVS and radio calibration
partitions are deliberately outside that claim. It does not make flash
immutable or prove the silicon or ROM interface is honest.

CI reproduces the committed deploy digest on Ubuntu and Windows using the
pinned `mpy-cross==1.27.0.post2` toolchain. Every push to `main` and every pull
request runs the desktop tests and verifies the published manifest. Different
`mpy-cross` versions are not reproduction targets because `.mpy` output is
toolchain-dependent.

Desktop coverage is complemented by checks on the supported CYD under stock
MicroPython v1.28.0 at 240 MHz: full mnemonic/passphrase/address derivation,
constrained-memory derivation through the resident UI path, the 99-roll
SeedSigner vector, UI and confirmation-gate checks, and external ROM-bootloader
plus raw-filesystem verification. Hosted CI cannot attach to a physical CYD,
so the hardware checks are repeated against each tagged release rather than
represented as continuously enforced CI.

### Hardware side-channel resistance is not provided

There is no secure element, shielding, constant-time guarantee, or claimed
resistance to power, timing, electromagnetic, or fault-injection attacks. Seed
words and generated passphrases are intentionally displayed in clear text.

## Policy maintenance

Security claims in the UI and documentation must describe enforced current
behaviour, not intended behaviour. When a vulnerability is fixed, update the
supported versions, publish an advisory when users of an affected build need
to act, and remove the resolved issue from **Current security limitations**.
Resolved vulnerabilities are documented in versioned release notes or GitHub
Security Advisories. This policy lists only limitations that apply to supported
code and hardware.
