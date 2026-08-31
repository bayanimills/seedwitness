"""Drive the CYD touch UI programmatically through a full ceremony +
verification flow and save one PNG per screen to screenshots/. Deterministic:
fixed roll sequence, no real randomness -- reproducible and diffable.

Usage: .venv/bin/python sim/capture_screens.py
"""
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

from mock_canvas import MockCanvas  # noqa: E402

from seedwitness.ui.app import App  # noqa: E402
from seedwitness.ui.screens import WordEntryScreen  # noqa: E402

OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)
# Clear stale frames before writing. Numbering shifts whenever a screen is
# added, so leaving the previous run's files behind would keep retired names
# alive beside the current set and quietly pad the gallery with images no
# screen draws any more. After a run this directory is exactly what this
# build of the app renders, nothing more.
for _stale in OUT.glob("*.png"):
    _stale.unlink()

canvas = MockCanvas()
app = App()
shot_n = itertools.count(1)


def snap(label):
    app.draw(canvas)
    n = next(shot_n)
    path = OUT / ("%02d_%s.png" % (n, label))
    canvas.save(str(path))
    print("saved %s  (screen: %s)" % (path.name, type(app.screen).__name__))


def tap_button(label_substring, optional=False):
    """Find and tap the first button on the current screen whose label
    contains the given substring -- robust to layout, matches real usage
    ("tap the button that says X") rather than hardcoded pixel coordinates.

    A miss raises.  This used to return False and every caller ignored it, so
    when a new screen appeared mid-flow the walkthrough tapped nothing and
    kept snapping that screen under the filenames of the screens it never
    reached.  A tap that does not land is a broken walkthrough: say so at the
    step that broke, not twenty steps later.  Pass optional=True for the taps
    that are genuinely conditional.
    """
    for b in app.screen.buttons:
        # labels may be split across lines to fit a narrow screen, so match
        # against the flattened text rather than the literal label
        flat = b.label.replace("\n", " ").lower()
        if label_substring.lower() in flat:
            app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
            return True
    if optional:
        return False
    raise AssertionError("no button matching %r on %s; buttons are %r" % (
        label_substring, type(app.screen).__name__,
        [b.label for b in app.screen.buttons]))


def tap_roll(value):
    """RollEntryScreen-specific: tap the numbered button for this roll value."""
    for b in app.screen.buttons:
        if b.label == str(value):
            app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
            return
    raise AssertionError("no roll button labeled %r" % value)


def type_word(word):
    """WordEntryScreen-specific: tap letters until the word is confirmed
    (either via a unique-prefix auto-confirm or by tapping the resulting
    candidate button)."""
    screen = app.screen
    assert isinstance(screen, WordEntryScreen)
    start_len = len(screen.words)
    for letter in word:
        letter_btn = next(b for b in screen.buttons if b.label == letter)
        app.handle_tap(canvas, letter_btn.x + letter_btn.w // 2, letter_btn.y + letter_btn.h // 2)
        if not isinstance(app.screen, WordEntryScreen) or len(app.screen.words) > start_len:
            return
    # not auto-confirmed (ambiguous prefix truncated early) -- tap the
    # matching candidate button explicitly
    for b in screen._candidate_buttons():
        if b.label == word:
            app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
            return


# 0. Boot splash: drawn by run_on_hardware before the App exists, so it is
# saved outside the numbered tap sequence. The device reads the wordmark
# from /wordmark.bin; on the desktop it lives in the build tree (falls back
# to the text wordmark if tools/build_mpy.sh has not run).
import seedwitness.ui.app as _appmod  # noqa: E402
from seedwitness.ui.app import _splash_frame  # noqa: E402

_appmod.WORDMARK_FILE = str(ROOT / "_build_mpy" / "wordmark.bin")
_splash_frame(canvas)
canvas.save(str(OUT / "00_splash.png"))

# 1. Home
snap("home")

# 2. Roll flow -> method select
tap_button("Roll a Seed")
snap("method_select")

# 3. Choose D6
tap_button("D6")
snap("word_count")

# 4. Choose 12 words -> RollEntryScreen
tap_button("12 words")
snap("roll_entry_start")

# 5. Roll partway (deterministic 1..6 repeating cycle) for a mid-progress shot
mid_rolls = [((i % 6) + 1) for i in range(20)]
for val in mid_rolls:
    tap_roll(val)
snap("roll_entry_mid")

# 5b. The hold-to-confirm dial part-way round a value button. On hardware the
# sweep runs while the finger is held; drive it directly here so the
# walkthrough shows what the user actually sees mid-press.
_rb = app.screen.buttons[2]
_rb.draw(canvas, accent=True)
app.screen._draw_sweep(canvas, _rb, 0.62)
canvas.save(str(OUT / ("%02d_roll_hold_sweep.png" % next(shot_n))))
app.screen.draw(app, canvas)

# 6. Finish the remaining rolls (D6/12-word needs 50 total)
remaining = app.screen.session.target_rolls - len(app.screen.session.rolls)
rest_rolls = [((i % 6) + 1) for i in range(20, 20 + remaining)]
for val in rest_rolls:
    tap_roll(val)

# 6b. The fixed 1..6 cycle above is exactly the input the advisory pattern
# check exists to notice, so the walkthrough meets the warning here. Capture
# it -- a real user reaches it about once in 645 honest d6 ceremonies, and it
# had no screenshot -- then clear it the way they would. Tapping through
# rather than perturbing the rolls keeps the sequence deterministic, so every
# mnemonic-derived screen below stays byte-identical.
if type(app.screen).__name__ == "EntropyWarningScreen":
    snap("entropy_pattern_warning")
    tap_button("Use These Rolls")
snap("entropy_captured")

# 7. Reveal words
tap_button("Reveal Words")
snap("word_list_page1")

# 8. Continue to checksum word screen (only one page needed for 12 words -- 11
# display words at 6/page = 2 pages)
tap_button("Next")
snap("word_list_page2")
tap_button("Continue")
snap("checksum_word")

# 9. Continue to ceremony-complete menu
tap_button("Continue")
snap("ceremony_complete")

generated_mnemonic = app.last_mnemonic

# 9b. SeedQR export: the warning gate, then both reveal frames. In the sim
# the reveal blanks itself immediately (hold_ms=0 skips the 20s dwell), so
# the lit frames are captured via _draw_qr_frame -- the exact method the
# hold-gated reveal path draws with.
tap_button("Export QR")
snap("qr_export_warning")

from seedwitness import mnemonic as _mn  # noqa: E402
from seedwitness import qr as _qr  # noqa: E402

_qs = app.screen
_size, _mat = _qr.compact_seedqr(_mn.mnemonic_to_entropy(_qs.mnemonic))
_qs._draw_qr_frame(canvas, _size, _mat, compact=True)
canvas.save(str(OUT / ("%02d_qr_compact_revealed.png" % next(shot_n))))
_size, _mat = _qr.standard_seedqr(_mn.word_indices(_qs.mnemonic))
_qs._draw_qr_frame(canvas, _size, _mat, compact=False)
canvas.save(str(OUT / ("%02d_qr_standard_revealed.png" % next(shot_n))))
app.handle_tap(canvas, 230, 10)   # corner Back, out of the export gate

# 9b. Confirm Backup -- success path (type the actual generated mnemonic back)
tap_button("Confirm Backup")
snap("confirm_backup_start")
for w in generated_mnemonic.split():
    type_word(w)
snap("confirm_backup_success")
tap_button("Continue")  # back to Ceremony Complete

# 10. Verify Address -> derivation path select
tap_button("Verify Address")
snap("derivation_path_select")

# 10a. Optional BIP39 passphrase: length, the roll grid that generates it,
# and the resulting words with their check value. Generated from dice like
# the seed, never typed. Discarded again here so the addresses below stay
# those of the bare mnemonic.
from seedwitness import passphrase as _pph  # noqa: E402

_pph.WORDS_FILE = str(ROOT / "_build_mpy" / "eff_words.bin")
if Path(_pph.WORDS_FILE).exists():
    tap_button("Passphrase")
    snap("passphrase_length")
    tap_button("8 words")
    for _i in range(_pph.rolls_needed(8) - 1):
        tap_roll((_i % 6) + 1)
    snap("passphrase_roll")
    tap_roll(((_pph.rolls_needed(8) - 1) % 6) + 1)   # the last roll commits
    snap("passphrase_words")
    tap_button("Discard")
    tap_button("Back")
    assert type(app.screen).__name__ == "DerivationPathScreen"
    assert app.passphrase == "", "passphrase leaked into the walkthrough"
else:
    print("skipped passphrase screens: run tools/build_mpy.sh for eff_words.bin")

# 10b. All Types: one PBKDF2 stretch, then every script type, so nobody has
# to know their wallet's address type before the ten-minute wait. The
# deriving frame is captured after completion, arrival lines included.
tap_button("All Types")
snap("address_index_all")
for b in app.screen.buttons:
    if b.label == "Derive Address":
        app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
        break
canvas.save(str(OUT / ("%02d_deriving_all_types.png" % next(shot_n))))
snap("all_addresses")
tap_button("SegWit")                # a row opens the full comparison view
snap("address_display_from_all")
tap_button("Back")                  # back to the four-row list
app.handle_tap(canvas, 230, 10)     # corner Back to the index screen
app.handle_tap(canvas, 230, 10)     # and back to the type menu

# 11. Choose BIP84 (native segwit) -> address index screen
tap_button("BIP84")
snap("address_index")

# 12. Derive -> address display (this is the screen that flashes a
# DerivingScreen frame internally before the blocking call -- capture that
# interim frame too, for real, not simulated)
for b in app.screen.buttons:
    if b.label == "Derive Address":
        app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
        break
canvas.save(str(OUT / ("%02d_deriving_interim_frame.png" % next(shot_n))))

# 12b. The same frame part-way through. Desktop CPython uses native
# hashlib.pbkdf2_hmac, which has no progress hook (only the device's
# pure-Python shim reports progress), so drive update_progress() directly --
# this documents what the user actually stares at for ~10 minutes on hardware.
# The clock is backdated to the board's measured ~575s stretch rate so the
# frame carries the time estimate a real user sees, not the pre-estimate
# blank the first half-minute shows.
from seedwitness.ui.screens import DerivingScreen as _DerivingScreen  # noqa: E402

_deriving = _DerivingScreen()
_deriving.draw(app, canvas)
_deriving.update_progress(canvas, 0, 2048)          # first callback: t0 lands
_deriving._t0 -= int(1300 * 575000 / 2048)          # 1300 rounds at board rate
_deriving.update_progress(canvas, 1300, 2048)
canvas.save(str(OUT / ("%02d_deriving_progress.png" % next(shot_n))))

snap("address_display")

# 12c. The account-level key behind that address: read-only, stores nothing,
# and the screen a user reaches when a wallet asks for an xpub rather than an
# address.
tap_button("Account Key")
snap("account_key")
tap_button("Back")

# 13. Back to home, then into manual verify entry flow
app.reset_to_home()
tap_button("Verify")
snap("verify_entry")
tap_button("Enter seed manually")
snap("manual_word_count")
tap_button("12 words")
snap("word_entry_empty")

# type "sh" to narrow toward a small candidate list
for letter in "sh":
    letter_btn = next(b for b in app.screen.buttons if b.label == letter)
    app.handle_tap(canvas, letter_btn.x + letter_btn.w // 2, letter_btn.y + letter_btn.h // 2)
snap("word_entry_candidates")

# 13b. A letter key held down: the magnified preview above the keyboard is
# what makes a 34px key usable, since a fingertip covers the key itself.
_k = next(b for b in app.screen.buttons if b.label == "a")
_k.draw(canvas, accent=True)
app.screen._draw_sweep(canvas, _k, 0.55)
app.screen._press_preview(canvas, _k)
canvas.save(str(OUT / ("%02d_letter_press_preview.png" % next(shot_n))))
app.screen.draw(app, canvas)

# 13c. A seed that does not checksum. Twelve valid BIP39 words are still
# almost never a valid seed, so this is the screen a mistyped backup meets.
app.reset_to_home()
tap_button("Verify")
tap_button("Enter seed manually")
tap_button("12 words")
for _w in ["abandon"] * 12:
    type_word(_w)
snap("invalid_seed")

# 14. About screen
app.reset_to_home()
tap_button("About")
snap("about")
# Every help page, in order. Two of them have a topic button; the rest are
# reached only by paging, which is why "Compare" and "Trust" had no
# screenshot at all -- the old loop looked for a "Trust?" button that does
# not exist and quietly captured nothing.
from seedwitness.ui.flow_info import AboutScreen as _AboutScreen  # noqa: E402

for _page in range(1, len(_AboutScreen.PAGES)):
    tap_button("Next")
    snap("about_%s" % _AboutScreen.PAGES[_page][0].lower().replace(" ", "_"))

# 14b. Verify Build and its diagnostics: the attestation path off the home
# menu, and the only screens here that read the deploy tree. Skip cleanly on
# a checkout that has not built one rather than shipping a half-drawn frame.
app.reset_to_home()
if (ROOT / "_build_mpy").is_dir():
    tap_button("Verify Build")
    snap("verify_build")
    tap_button("Diagnostics")
    snap("diagnostics")
    app.reset_to_home()
else:
    print("skipped verify_build/diagnostics: run tools/build_mpy.sh first")

# 15. Confirm Backup -- mismatch path, on a fresh App with one deliberate typo
from seedwitness.ui.app import App as _App  # noqa: E402
from seedwitness.ui.screens import GenerateCompleteScreen as _GCS  # noqa: E402

mismatch_app = _App()
mismatch_app.last_mnemonic = generated_mnemonic
mismatch_app.push(_GCS(generated_mnemonic))
for b in mismatch_app.screen.buttons:
    if "Confirm Backup" in b.label:
        mismatch_app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
        break

wrong_words = generated_mnemonic.split()
mid = len(wrong_words) // 2
wrong_words[mid] = "zoo" if wrong_words[mid] != "zoo" else "yellow"
for w in wrong_words:
    screen = mismatch_app.screen
    start_len = len(screen.words)
    for letter in w:
        letter_btn = next(b for b in screen.buttons if b.label == letter)
        mismatch_app.handle_tap(canvas, letter_btn.x + letter_btn.w // 2, letter_btn.y + letter_btn.h // 2)
        if not isinstance(mismatch_app.screen, WordEntryScreen) or len(mismatch_app.screen.words) > start_len:
            break
    else:
        for b in screen._candidate_buttons():
            if b.label == w:
                mismatch_app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
                break

mismatch_app.draw(canvas)
n = next(shot_n)
path = OUT / ("%02d_confirm_backup_mismatch.png" % n)
canvas.save(str(path))
print("saved %s  (screen: %s)" % (path.name, type(mismatch_app.screen).__name__))

# 16. Enrolled accounts: management, nicknames, fingerprints, public QRs.
# The store is redirected to a temp file so the walkthrough never touches a
# real /accounts.json (on a desktop that path would be the filesystem root).
import tempfile  # noqa: E402

from seedwitness import accounts as _acct  # noqa: E402
from seedwitness import derive as _drv  # noqa: E402
from seedwitness.ui.screens import (  # noqa: E402
    AddressQRScreen as _AQS,
    EnrolScreen as _Enrol,
)

_acct.PATH = str(Path(tempfile.mkdtemp()) / "accounts.json")


def _t(bip):
    return next(t for t in _drv.PATH_TEMPLATES if t.bip == bip)


_, _xpub84 = _drv.account_xpub(generated_mnemonic, _t(84))
_, _xpub44 = _drv.account_xpub(generated_mnemonic, _t(44))
_acct.add("cold savings", _xpub84, 84)
_acct.add("BIP44 Legacy", _xpub44, 44)

app.reset_to_home()

# 16a. Enrol success now offers a nickname
app.push(_Enrol(generated_mnemonic, _t(84)))
tap_button("Enrol Account")
snap("enrol_done_nickname_offer")
tap_button("Add Nickname")
for letter in "cold":
    tap_button(letter)
snap("nickname_entry")
tap_button("Cancel")
app.reset_to_home()
# Re-enrolling the same xpub above renamed the record to its auto label
# (add() updates in place rather than duplicating), so restore the
# walkthrough nickname the later steps tap on.
_acct.add("cold savings", _xpub84, 84)

# 16b. The account list: nickname + fingerprint per row, Edit corners
tap_button("Accounts")
snap("account_list")

# 16c. Manage -> delete confirmation -> xpub QR
tap_button("Edit")
snap("account_manage")
tap_button("Delete")
snap("account_delete_confirm")
tap_button("Cancel")
tap_button("xpub QR")     # the BIP84 account splits the row: xpub / zpub
snap("account_xpub_qr")
app.handle_tap(canvas, 230, 10)   # corner Back to the manage screen
tap_button("zpub QR")     # the SLIP-132 spelling, QR re-encoded to match
snap("account_zpub_qr")
app.handle_tap(canvas, 230, 10)   # corner Back to the manage screen
app.handle_tap(canvas, 230, 10)   # and back to the list

# 16d. Opening an account shows which one: the fingerprint on the verify path
tap_button("cold savings")
snap("account_address_index")
tap_button("Derive Address")
snap("address_display_with_qr")

# 16e. The address QR itself, ungated: bech32 (alphanumeric), legacy (byte)
tap_button("QR")
snap("address_qr_bech32")
app.handle_tap(canvas, 230, 10)   # corner Back

app.push(_AQS("1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA", "m/44h/0h/0h/0/0"))
snap("address_qr_legacy")
app.push(_AQS("bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
              "m/86h/0h/0h/0/0"))
snap("address_qr_taproot")
app.reset_to_home()

# 17. Demonstration mode: the [!] gate, the fill, and the DEMO stamp
# travelling with the seed through every subsequent screen.
tap_button("Roll a Seed")
tap_button("D6")
tap_button("12 words")
snap("roll_entry_demo_button")          # the [!] beside Back, pre-roll only
_d = app.screen._demo_button()
app.handle_tap(canvas, _d.x + _d.w // 2, _d.y + _d.h // 2)
snap("demo_gate")
tap_button("Start Demo")
snap("demo_entropy_captured")
tap_button("Reveal Words")
snap("demo_word_list")
tap_button("Next")
tap_button("Continue")
snap("demo_checksum")
tap_button("Continue")
snap("demo_ceremony_complete")
tap_button("Verify Address")
tap_button("BIP84")
for b in app.screen.buttons:
    if b.label == "Derive Address":
        app.handle_tap(canvas, b.x + b.w // 2, b.y + b.h // 2)
        break
snap("demo_address_display")
tap_button("Enrol Account")
snap("demo_enrol_refused")
app.reset_to_home()

# 17b. The verify-path [!] and its gate
tap_button("Verify Seed")
app.draw(canvas)                        # builds the [!] into the buttons
_d = app.screen._demo_button()
app.handle_tap(canvas, _d.x + _d.w // 2, _d.y + _d.h // 2)
tap_button("Start Demo")
snap("demo_verify_path_select")
app.reset_to_home()

# 18. The 24-word roll count gate. A 24-word seed needs 100 D6 rolls;
# SeedSigner's ceremony uses 99, so the device stops at 99 and asks which
# convention the user wants rather than silently picking one.
app.reset_to_home()
tap_button("Roll a Seed")
tap_button("D6")
tap_button("24 words")
for _i in range(99):
    tap_roll((_i % 6) + 1)
snap("seedsigner_roll_gate")
tap_button("Use 99: SeedSigner")
snap("roll_count_confirm")
app.reset_to_home()

print("\n%d screenshots written to %s" % (next(shot_n) - 1, OUT))
