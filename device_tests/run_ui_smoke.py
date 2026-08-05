"""Runs under real MicroPython -- `micropython device_tests/run_ui_smoke.py`.
Drives screens.py/app.py through the main navigation flow with a no-op
DummyCanvas, to catch MicroPython syntax/stdlib incompatibilities in the UI
layer that CPython testing can't (percent-formatting was used deliberately
throughout screens.py for this reason, but this is what actually proves it,
not just style intent). Not a visual check -- screenshots (via sim/, desktop
CPython + PIL) cover that; PIL isn't available under MicroPython.
"""
import sys

sys.path.insert(0, "device/mp_shims")
sys.path.insert(0, "device")
sys.path.insert(0, "device_tests")

from dummy_canvas import DummyCanvas  # noqa: E402

import seedwitness  # noqa: E402  (applies compat patches)
from seedwitness.ui.app import App  # noqa: E402
from seedwitness.ui.screens import (  # noqa: E402
    AboutScreen,
    AddressDisplayScreen,
    AddressIndexScreen,
    BackupResultScreen,
    ChecksumWordScreen,
    DerivationPathScreen,
    EntropyCapturedScreen,
    GenerateCompleteScreen,
    HomeScreen,
    InvalidSeedScreen,
    ManualWordCountScreen,
    MethodSelectScreen,
    QRExportScreen,
    VerifyEntryScreen,
    WordCountScreen,
    WordEntryScreen,
    WordListScreen,
)
from seedwitness import entropy as ent  # noqa: E402

_failures = []


def check(name, condition):
    if condition:
        print("PASS", name)
    else:
        print("FAIL", name)
        _failures.append(name)


def tap_button(app, canvas, label_substring):
    for b in app.screen.buttons:
        if label_substring.lower() in b.label.lower():
            app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
            return True
    return False


canvas = DummyCanvas()
app = App()

app.draw(canvas)
check("home screen draws", isinstance(app.screen, HomeScreen))

tap_button(app, canvas, "Generate New Seed")
app.draw(canvas)
check("-> method select", isinstance(app.screen, MethodSelectScreen))

tap_button(app, canvas, "D6")
app.draw(canvas)
check("-> word count", isinstance(app.screen, WordCountScreen))

tap_button(app, canvas, "12 words")
app.draw(canvas)
check("-> roll entry, session created", app.session is not None)

# tap through a full D6/12-word ceremony via the roll grid
session = app.session
while not session.is_complete:
    value = (len(session.rolls) % 6) + 1
    for b in app.screen.buttons:
        if b.label == str(value):
            app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
            break
    app.draw(canvas)

check("-> entropy captured after full ceremony", isinstance(app.screen, EntropyCapturedScreen))
check("mnemonic was generated", app.last_mnemonic is not None)
mnemonic = app.last_mnemonic

tap_button(app, canvas, "Reveal Words")
app.draw(canvas)
check("-> word list", isinstance(app.screen, WordListScreen))

# page through to the checksum screen
while isinstance(app.screen, WordListScreen):
    tap_button(app, canvas, "Next") or tap_button(app, canvas, "Continue")
    app.draw(canvas)
check("-> checksum word screen", isinstance(app.screen, ChecksumWordScreen))

tap_button(app, canvas, "Continue")
app.draw(canvas)
check("-> ceremony complete", isinstance(app.screen, GenerateCompleteScreen))

# backup confirmation, success path
tap_button(app, canvas, "Confirm Backup")
app.draw(canvas)
check("-> word entry (backup confirm)", isinstance(app.screen, WordEntryScreen))

for word in mnemonic.split():
    screen = app.screen
    start_len = len(screen.words)
    for letter in word:
        matched = False
        for b in screen.buttons:
            if b.label == letter:
                app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
                matched = True
                break
        if not isinstance(app.screen, WordEntryScreen) or len(app.screen.words) > start_len:
            break
    else:
        for b in screen._candidate_buttons():
            if b.label == word:
                app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
                break
    app.draw(canvas)

check("-> backup result screen", isinstance(app.screen, BackupResultScreen))
check("backup confirmed as a match", app.screen.success)

tap_button(app, canvas, "Continue")
app.draw(canvas)
check("back to ceremony complete", isinstance(app.screen, GenerateCompleteScreen))

# SeedQR export: the gate must appear, the reveal must run (this is what
# proves qr.py -- GF tables, Reed-Solomon, matrix build -- executes under
# real MicroPython; correctness is pinned by the desktop decoder tests),
# nothing may land on the stack, and Back must leave.
tap_button(app, canvas, "Export QR")
app.draw(canvas)
check("-> qr export warning gate", isinstance(app.screen, QRExportScreen))
qr_screen = app.screen
b = qr_screen.compact_btn
app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
check("reveal ran and pushed nothing", app.screen is qr_screen)
b = qr_screen.standard_btn
app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
check("standard reveal ran and pushed nothing", app.screen is qr_screen)
# the corner Back is drawn directly rather than kept in .buttons
app.handle_tap(canvas, qr_screen.back_button(app).x + 5, qr_screen.BACK_INSET + 5)
app.draw(canvas)
check("back leaves the export gate", isinstance(app.screen, GenerateCompleteScreen))

# verification path
tap_button(app, canvas, "Verify Address")
app.draw(canvas)
check("-> derivation path select", isinstance(app.screen, DerivationPathScreen))

tap_button(app, canvas, "BIP84")
app.draw(canvas)
check("-> address index screen", isinstance(app.screen, AddressIndexScreen))

for b in app.screen.buttons:
    if b.label == "Derive Address":
        app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
        break
app.draw(canvas)
check("-> address display screen", isinstance(app.screen, AddressDisplayScreen))
check(
    "correct known-vector-independent address shape",
    app.screen.address.startswith("bc1q"),
)

# home -> about
app.reset_to_home()
tap_button(app, canvas, "About")
app.draw(canvas)
check("-> about screen", isinstance(app.screen, AboutScreen))
tap_button(app, canvas, "Back")
app.draw(canvas)
check("back button returns home", isinstance(app.screen, HomeScreen))

# manual verify entry + invalid seed path
tap_button(app, canvas, "Verify Existing Seed")
tap_button(app, canvas, "Enter seed manually")
app.draw(canvas)
check("-> manual word count", isinstance(app.screen, ManualWordCountScreen))
tap_button(app, canvas, "12 words")
app.draw(canvas)
check("-> word entry (verify)", isinstance(app.screen, WordEntryScreen))

# type 12 copies of "abandon" (invalid checksum) to exercise InvalidSeedScreen
for _ in range(12):
    screen = app.screen
    start_len = len(screen.words)
    for letter in "abandon":
        for b in screen.buttons:
            if b.label == letter:
                app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
                break
        # "aban" is a unique prefix (only "abandon" matches) so this
        # auto-confirms partway through -- stop feeding it more letters
        # once that happens, same as every other word-typing loop here.
        if not isinstance(app.screen, WordEntryScreen) or len(app.screen.words) > start_len:
            break
    app.draw(canvas)
check("-> invalid seed screen (bad checksum caught)", isinstance(app.screen, InvalidSeedScreen))

# InvalidSeedScreen "Try Again" must pop itself before pushing a fresh
# WordEntryScreen -- previously it only pushed, leaking one stale
# InvalidSeedScreen frame onto the stack per failed retry, forever.
stack_depth_before_retry = len(app.stack)
tap_button(app, canvas, "Try Again")
app.draw(canvas)
check("-> Try Again returns to word entry", isinstance(app.screen, WordEntryScreen))
check(
    "Try Again does not grow the screen stack (InvalidSeedScreen popped first)",
    len(app.stack) == stack_depth_before_retry,
)

# back out to Home rather than finishing this deliberately-invalid entry
app.reset_to_home()

# "Use last generated seed" -- the shortcut VerifyEntryScreen only shows
# once app.last_mnemonic is set (from the earlier successful ceremony)
check("last_mnemonic is set from the earlier ceremony", app.last_mnemonic == mnemonic)
tap_button(app, canvas, "Verify Existing Seed")
app.draw(canvas)
check("-> verify entry screen", isinstance(app.screen, VerifyEntryScreen))
found_shortcut = tap_button(app, canvas, "Use last generated seed")
check("'Use last generated seed' button is present and tappable", found_shortcut)
app.draw(canvas)
check("-> derivation path select (via shortcut)", isinstance(app.screen, DerivationPathScreen))
check("shortcut used the correct mnemonic", app.screen.mnemonic == mnemonic)

print()
if _failures:
    print("%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
else:
    print("ALL UI SMOKE CHECKS PASSED under real MicroPython")
