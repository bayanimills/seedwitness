"""Address flow: derivation-path choice, the Diceware passphrase ceremony,
index selection, the derive wait screen, address comparison and the address
QR, plus enrolment of the verified seed as a watch-only account.

Load-on-demand: reached only through screens.flow_screen() at the navigation
point and unloaded (device only) when the session ends -- see screens.py's
docstring. Importing derive here costs only derive's own data (templates,
fingerprints): the embit EC chain -- the app's heaviest dependency -- loads
even later, inside derive's functions, AFTER any PBKDF2 stretch (see
derive.py's import-discipline note). Keeping that chain out of boot, out of
every other flow, and out of the stretch itself is a large part of why the
split exists.
"""
from .. import accounts as acct
from .. import derive as drv
from .. import passphrase as pph
from . import fonts
from . import theme as th

try:  # MicroPython: wraparound-safe millisecond clock
    from time import ticks_diff, ticks_ms
except ImportError:  # desktop CPython (sim, tests)
    from time import monotonic as _monotonic

    def ticks_ms():
        return int(_monotonic() * 1000)

    def ticks_diff(new, old):
        return new - old
from .screens import (
    BUTTON_GAP,
    HEADER_H,
    MARGIN,
    SHORT_NAMES,
    MenuScreen,
    Screen,
    TapButton,
    _auto_label,
    draw_qr_symbol,
    flow_screen,
    harmonise_scale,
    release_module,
)


class PassphraseLengthScreen(MenuScreen):
    """How many Diceware words. Entropy shown, because the number is the
    decision.

    BIP39 stretches a passphrase with only 2048 rounds of HMAC-SHA512, which
    is weak, so the passphrase has to carry its own weight rather than lean on
    the KDF. 8 is the default for that reason; 6 is offered because a floor
    people will actually use beats a recommendation they will not.
    """

    title = "Passphrase"

    def __init__(self, mnemonic):
        self.mnemonic = mnemonic
        items = []
        for n in pph.LENGTHS:
            items.append(("%d words\n%d bits" % (n, pph.bits_for(n)),
                          lambda app, n=n: app.push(
                              PassphraseRollScreen(self.mnemonic, n))))
        super().__init__(items)
        self.buttons = list(self.buttons) + [self.back_button(None)]

    BOTTOM_RESERVED = 30

    def draw(self, app, canvas):
        super().draw(app, canvas)
        y = th.HEIGHT - 24
        for line in th.wrap_text(
                "Lose these and the seed alone is not enough.",
                (th.WIDTH - 2 * MARGIN) // th.CHAR_W):
            canvas.text(MARGIN, y, line, th.WARN)
            y += 11


class PassphraseRollScreen(Screen):
    """Roll the passphrase, five D6 per word.

    D6 only, and the refusal is arithmetic rather than preference: 6**5 is
    exactly 7776, the size of the EFF list, so five rolls select one word with
    no modulo bias and no rejection sampling. A coin or D8 overshoots and would
    need "discard those and roll again", which is the instruction people
    shortcut.

    Same pip grid as the seed ceremony, so the physical action is identical
    and nothing new has to be learned at the point where mistakes are costly.
    """

    GRID_TOP = 96
    SWEEP_MS = 420          # matches the seed ceremony: a wrong roll is silent

    def __init__(self, mnemonic, word_count):
        self.mnemonic = mnemonic
        self.word_count = word_count
        self.target = pph.rolls_needed(word_count)
        self.rolls = []
        self.buttons = self._grid()

    def _grid(self):
        cols, rows = 2, 3
        w = (th.WIDTH - 2 * MARGIN - BUTTON_GAP) // cols
        h = (th.HEIGHT - 56 - self.GRID_TOP - (rows - 1) * BUTTON_GAP) // rows
        out = []
        for i in range(6):
            value = i + 1
            x = MARGIN + (i % cols) * (w + BUTTON_GAP)
            y = self.GRID_TOP + (i // cols) * (h + BUTTON_GAP)
            out.append(TapButton(x, y, w, h, str(value),
                                 self._handler(value), pips=value))
        return harmonise_scale(out)

    def _handler(self, value):
        def on_tap(app):
            self.rolls.append(value)
            if len(self.rolls) >= self.target:
                words = pph.words_from_rolls(self.rolls)
                app.pop()
                app.push(PassphraseWordsScreen(self.mnemonic, words))
        return on_tap

    def handle_tap(self, app, canvas, x, y):
        # The way out, same idiom as RollEntryScreen (drawn directly, plain
        # tap, no hold). Without it a user who picked a length was trapped:
        # the only exits were completing every hold-to-confirm roll or the
        # hour-long sleep timeout. The 420ms dwell stays on the dice, where
        # a wrong value silently corrupts the result; Back only discards,
        # and discarding is safe by construction -- the partial rolls live
        # on this screen object and die with it, and app.passphrase has not
        # been touched, so backing out can never leave part of a passphrase
        # armed for the next derivation.
        back = self.back_button(app)
        if back.contains(x, y):
            back.on_tap(app)
            return
        super().handle_tap(app, canvas, x, y)

    def _header_right_limit(self, canvas):
        # the corner Back is drawn directly rather than kept in
        # self.buttons (RollEntryScreen's idiom), so reserve its corner
        # explicitly or the title runs underneath it
        return th.WIDTH - self.BACK_INSET - self.BACK_W - 4

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        # "Rolling Words", not the old "Roll Passphrase": the corner Back
        # now claims its share of the header band, and the longer title no
        # longer held the primary face beside it -- the caption-face
        # fallback is a safety net, not a budget (see Screen.header).
        self.header(canvas, "Rolling Words")
        self.back_button(app).draw(canvas)
        canvas.text(MARGIN, HEADER_H + 10,
                    "%d/%d" % (len(self.rolls), self.target), th.FG, font=fonts.M)
        canvas.text(MARGIN, HEADER_H + 40, "roll a fair D6", th.MUTED, font=fonts.S)
        canvas.text(th.WIDTH - MARGIN - 11 * fonts.S.width, HEADER_H + 40,
                    "word %d of %d" % (min(len(self.rolls) // pph.ROLLS_PER_WORD + 1,
                                           self.word_count), self.word_count),
                    th.MUTED, font=fonts.S)
        for b in self.buttons:
            b.draw(canvas)


class PassphraseWordsScreen(Screen):
    """The generated passphrase, to be written down.

    Shown in full and only here. Unlike the seed there is no second
    confirmation step, because a passphrase that fails to match is not
    recoverable from anything: the check value below is what a returning user
    compares instead, and it is why they can tell a typo from a wrong wallet
    without ten minutes of PBKDF2.

    Two renderings of the same words, deliberately. The numbered list is
    what gets transcribed to paper -- but a numbered column says nothing
    about how the passphrase is typed back in, and the exact string handed
    to PBKDF2 is the words joined by single spaces, all lowercase. A user
    who writes six words in a column and later types them without spaces,
    or capitalised, derives a different wallet with no warning from
    anything, and concludes their backup is wrong -- the same silent
    fund-loss failure this device exists to catch. So the typed form is
    also shown once, wrapped, exactly as it will be entered, with the two
    rules a column cannot carry ("lowercase, single spaces") beside it.
    """

    def __init__(self, mnemonic, words):
        self.mnemonic = mnemonic
        self.words = words
        self.phrase = pph.to_passphrase(words)
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Use This", self._use),
        ]

    def _use(self, app):
        """Return to the Address Type screen with the passphrase set.

        Order matters and is not arbitrary: the passphrase is assigned AFTER
        the pops, never before. app.pop() clears the whole session when it
        lands on the home screen, so setting first and popping second could
        silently discard what the user just wrote down, leaving them deriving
        with no passphrase while believing they had one.

        Popping twice steps back over this screen and the length screen. If
        that lands on Home the session is genuinely over, and setting a
        passphrase there would leave it primed for whatever the next person
        checks, so it is deliberately not set.
        """
        phrase = self.phrase
        app.pop()          # this screen
        app.pop()          # the length screen
        if type(app.screen).__name__ != "HomeScreen":
            app.passphrase = phrase

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Write This Down")
        # The numbered list stays whole on one screen: a passphrase split
        # across pages is a passphrase someone writes down half of. Six
        # words keep the single column at the 12x24 face; eight and ten go
        # to two columns at the caption face -- that step-down is what buys
        # the room for the typed-form block below without paginating
        # anything. Column-major order (1..rows down the left, the rest down
        # the right), matching how a column gets written on paper.
        if len(self.words) <= 6:
            y = HEADER_H + 6
            for i, w in enumerate(self.words):
                canvas.text(MARGIN, y + (fonts.M.height - fonts.S.height),
                            "%d" % (i + 1), th.MUTED, font=fonts.S)
                canvas.text(MARGIN + 20, y, w, th.FG, font=fonts.M)
                y += 26
        else:
            col_w = (th.WIDTH - 2 * MARGIN) // 2
            rows = -(-len(self.words) // 2)   # ceil
            for i, w in enumerate(self.words):
                x = MARGIN + (i // rows) * col_w
                y = HEADER_H + 6 + (i % rows) * 22
                canvas.text(x, y, "%2d" % (i + 1), th.MUTED, font=fonts.S)
                canvas.text(x + 24, y, w, th.FG, font=fonts.S)
            y = HEADER_H + 6 + rows * 22
        y += 4
        canvas.text(MARGIN, y, "check " + pph.check_value(self.phrase),
                    th.ACCENT, font=fonts.S)
        y += fonts.S.height + 2
        # The typed form: the EXACT string a returning user must enter, the
        # way it will be entered. self.phrase IS what goes into PBKDF2, so
        # showing it (rather than a paragraph about it) means the display
        # cannot drift from the requirement. wrap_text breaks only at
        # spaces and every EFF word is shorter than a line, so reading the
        # wrapped lines as one line reconstructs the phrase exactly.
        budget = (th.WIDTH - 2 * MARGIN) // th.CHAR_W
        canvas.text(MARGIN, y, "type as one line, exactly:", th.MUTED)
        y += 11
        for line in th.wrap_text(self.phrase, budget):
            canvas.text(MARGIN, y, line, th.FG)
            y += 11
        canvas.text(MARGIN, y, "lowercase, single spaces", th.MUTED)
        for b in self.buttons:
            b.draw(canvas, accent=True)


class DerivationPathScreen(MenuScreen):
    # "Address Type", not "Derivation Path": both name this menu honestly,
    # but only the shorter one holds the 12x24 face beside the Back corner,
    # and "address type" is the vocabulary the wallet being compared against
    # will use anyway
    title = "Address Type"
    BOTTOM_RESERVED = 22  # room for the passphrase note drawn in draw()

    def __init__(self, mnemonic):
        self.mnemonic = mnemonic
        # "All Types" first, because it is the answer to a question most
        # users cannot answer: which address type their wallet uses. Choosing
        # wrong here used to surface as a MISMATCH -- the most alarming
        # possible misreading of a correct backup. Deriving all four costs
        # about 36 extra seconds on a ten-minute operation (the PBKDF2 seed
        # is cached; each further type is EC math only), and turns "you must
        # already know your type" into "one of these four will match".
        items = [("All Types",
                  lambda app: app.push(AddressIndexScreen(self.mnemonic, None)))]
        items += [
            (t.label, lambda app, t=t: app.push(AddressIndexScreen(self.mnemonic, t)))
            for t in drv.PATH_TEMPLATES
        ]
        # Half height: setting a passphrase is the exception, not the path
        # most people take, but it has to be reachable from here because this
        # is the screen where the derivation is chosen and where a
        # passphrase-protected wallet otherwise fails as a false mismatch.
        items.append(("Passphrase",
                      lambda app: app.push(PassphraseLengthScreen(self.mnemonic)),
                      0.6))
        super().__init__(items)
        self.buttons = list(self.buttons) + [self.back_button(None)]

    def draw(self, app, canvas):
        super().draw(app, canvas)
        # Which wallet is about to be derived, stated on the screen that
        # decides it. Deriving for a passphrase-protected wallet with no
        # indication why looks exactly like a "your backup is wrong" false
        # alarm from a verification device, which is the opposite of what it
        # is for -- so the footnote has to track the session, not describe a
        # build that no longer exists.
        #
        # It used to read "assumes no BIP39 passphrase" unconditionally,
        # written when no screen collected one. Once the Diceware passphrase
        # shipped, this became a line that actively lied on the one screen
        # where the user returns after arming it.
        if getattr(app, "passphrase", ""):
            # Repeat the value printed beside the generated words. This is a
            # continuity check across the hand-off: before paying for a long
            # derivation the user can see that the exact phrase they approved
            # is still the one armed in the session. It is deliberately not
            # called a backup check -- only comparing the resulting address
            # with the target wallet can prove their written transcription.
            check = pph.check_value(app.passphrase)
            canvas.text(MARGIN, th.HEIGHT - 16,
                        "passphrase SET  check " + check, th.ACCENT)
        else:
            canvas.text(MARGIN, th.HEIGHT - 16, "no BIP39 passphrase", th.MUTED)


class EnrolScreen(Screen):
    """Confirm turning the mnemonic just verified into a watch-only account.

    Reached only from the seed path, where the seed is already in the derive
    cache, so building the account xpub here costs the EC derivation and not
    another 575s of PBKDF2.

    The privacy warning is the whole screen and must not be softened. An
    account xpub is public by construction, so this is not a key leak -- but
    it lets anyone holding it derive and link every address in the account,
    past and future, and an xpub cannot be rotated without moving the coins.
    A user who hears "it is only a public key" and nothing else has been
    told the true half of a misleading sentence.
    """

    def __init__(self, mnemonic, template):
        self.mnemonic = mnemonic
        self.template = template
        self.done = None
        # True while draw() is showing the full-store refusal. Tracked so the
        # real controls can be RESTORED: unlike the demo refusal, this state
        # is escapable within the session -- the Accounts route below lets
        # the user delete a record and come straight back.
        self._refused = False
        self.buttons = self._action_buttons()

    def _action_buttons(self):
        return [
            TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN, 40,
                      "Enrol Account", self._enrol),
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Cancel", lambda app: app.pop()),
        ]

    def _enrol(self, app):
        if getattr(app, "demo", False):
            # Backstop only: draw() replaces this screen's controls with a
            # refusal before a demo user can ever reach this handler.
            return
        if len(acct.load()) >= acct.MAX_ACCOUNTS:
            # Backstop, same shape as the demo one: draw() shows the
            # full-store refusal before this handler is reachable. Without
            # this guard acct.add() would raise ValueError out of a tap
            # handler, onto the CrashScreen, ending the session and throwing
            # away the multi-minute derivation the user just paid for --
            # over a condition they were never warned about.
            return
        # The passphrase MUST be threaded through here. Without it this
        # enrolled the no-passphrase wallet's account key while the addresses
        # the user had just compared were derived WITH the passphrase: the
        # device would show MATCH, then store the account of a wallet they do
        # not own, and every later check from Accounts would report confident
        # mismatches against a perfectly good backup. That is the sharpest
        # failure this product has (SECURITY.md, "in scope"), and it was
        # silent. It also restores the cache hit -- the key is
        # (mnemonic, passphrase), so the un-passed version missed, re-ran the
        # full ~10 minute stretch inside handle_tap with no progress frame,
        # and clobbered the cache on the way out.
        _, xpub = drv.account_xpub(self.mnemonic, self.template,
                                   passphrase=getattr(app, "passphrase", ""))
        self._xpub = xpub
        acct.add(_auto_label(self.template.bip), xpub, self.template.bip)
        self.done = drv.xpub_fingerprint(xpub)
        # Nickname offered here, at enrolment, because this is the moment the
        # user still knows which wallet this is. The auto label says only the
        # derivation ("BIP84 SegWit"), so a second account of the same type
        # would be indistinguishable from this one on the list.
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN, 40,
                      "Add Nickname", self._nickname),
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN,
                      40, "Home", lambda a: a.reset_to_home()),
        ]

    def _nickname(self, app):
        bip = self.template.bip
        xpub = self._xpub
        # acct.add() updates the label of an existing xpub in place, so
        # saving is a re-add, never a duplicate. Empty falls back to the
        # auto label rather than an unnameable blank row.
        app.push(flow_screen("accounts", "NicknameScreen")(
            "", lambda a, text: acct.add(text or _auto_label(bip), xpub, bip)))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Enrol Account")
        y = HEADER_H + 12
        if getattr(app, "demo", False):
            # Refused outright, with the reason, not a disabled-looking
            # button: a demonstration seed is public and identical for
            # everyone, so an account made from it would watch nobody's
            # coins -- and enrolment is the one path that persists anything
            # to flash, so a demo must never cross it.
            for line in th.wrap_text(
                    "Refused: this is the DEMONSTRATION seed. It is public "
                    "and identical for everyone, so an account made from it "
                    "would watch nobody's coins. Roll a real seed first.",
                    (th.WIDTH - 2 * MARGIN) // fonts.S.width):
                canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
                y += 20
            self.buttons = [
                TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                          "Back", lambda a: a.pop()),
            ]
            for b in self.buttons:
                b.draw(canvas, accent=True)
            return
        stored = 0 if self.done else len(acct.load())
        if stored >= acct.MAX_ACCOUNTS:
            # Refused the way demo is refused: stated on the screen with the
            # reason, never a ValueError out of acct.add -- that raise lands
            # on the CrashScreen and ends the session, destroying the
            # derivation the user just paid minutes for. The store is full
            # at exactly the five rows the accounts list can show
            # (accounts.MAX_ACCOUNTS): a sixth record would sit on flash
            # unreachable, a permanent privacy exposure the user could
            # never see or delete.
            #
            # Refused by count alone, deliberately. Telling "this xpub is
            # already one of the five" (where add() would only refresh a
            # label) from a genuine sixth account needs the xpub, which
            # costs seconds of EC derivation on this board -- far too much
            # for a draw path. Renaming has its own route on the manage
            # screen, so refusing uniformly loses nothing.
            if not self._refused:
                self._refused = True
                self.buttons = [
                    # The route to the fix: the accounts list, where Delete
                    # lives. From there Back returns here, and the refusal
                    # stands down below the moment a slot is free.
                    TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN,
                              40, "Accounts",
                              lambda a: a.push(flow_screen(
                                  "accounts", "AccountListScreen")())),
                    TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN,
                              40, "Back", lambda a: a.pop()),
                ]
            for line in th.wrap_text(
                    "%d of %d accounts stored. Delete one you no longer "
                    "check, then enrol this one. The limit is the list: an "
                    "account it cannot show could never be deleted."
                    % (stored, acct.MAX_ACCOUNTS),
                    (th.WIDTH - 2 * MARGIN) // fonts.S.width):
                canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
                y += 20
            for b in self.buttons:
                b.draw(canvas, accent=True)
            return
        if self._refused:
            # A slot opened up (deleted via the Accounts route above, then
            # Back to here): put the real controls back, or this screen
            # would keep drawing a refusal that is no longer true.
            self._refused = False
            self.buttons = self._action_buttons()
        if self.done:
            canvas.text(MARGIN, y, "Enrolled.", th.GOOD, font=fonts.M)
            y += 34
            canvas.text(MARGIN, y, "account id " + self.done, th.ACCENT, font=fonts.S)
            y += 28
            body = ("You can now check addresses without entering your seed "
                    "again. Nothing secret is stored on this device.")
        else:
            # Kept to 8 lines at the 8x16 face: the space above the buttons
            # is 176px and this warning must never be the thing that clips.
            # The earlier wording ran to ten lines and lost its last word,
            # which was "coins" -- the one word that made the risk concrete.
            body = ("Stores this account's PUBLIC key. Later checks then take "
                    "seconds, with no seed on the device. Anyone reading the "
                    "device sees every address in this account. Permanent.")
        for line in th.wrap_text(body, (th.WIDTH - 2 * MARGIN) // fonts.S.width):
            canvas.text(MARGIN, y, line, th.FG if self.done else th.WARN,
                        font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=not self.done)


def _xpub_wait_frame(screen, canvas):
    """The brief static wait frame for an xpub derivation (~3s on the
    board): long enough that a frozen screen would look wrong, too short
    for a progress bar. One function, both callers (the index stepper's
    Derive and the display screen's prev/next stepping), so the frame
    cannot drift between them."""
    canvas.fill(th.BG)
    screen.header(canvas, "Deriving...")
    canvas.text(MARGIN, HEADER_H + 24, "From the enrolled account.",
                th.FG, font=fonts.S)
    canvas.text(MARGIN, HEADER_H + 48, "No seed on this device.",
                th.GOOD, font=fonts.S)
    canvas.present()


def _account_key_wait_frame(screen, canvas):
    """Static feedback while the account public key is derived.

    The seed path normally hits the PBKDF2 cache populated for the address
    already on screen, but the remaining BIP32 work is still visible on the
    CYD.  State explicitly that this action is read-only: it is adjacent to
    enrolment, which deliberately *does* write the same public key to flash.
    """
    canvas.fill(th.BG)
    screen.header(canvas, "Deriving...")
    canvas.text(MARGIN, HEADER_H + 24, "Account key from this seed.",
                th.FG, font=fonts.S)
    canvas.text(MARGIN, HEADER_H + 48, "Nothing will be stored.",
                th.GOOD, font=fonts.S)
    canvas.present()


class AddressIndexScreen(Screen):
    # Short per-BIP names for the header, shared with the accounts flow --
    # the canonical dict lives in screens.py (see SHORT_NAMES there). The
    # full template labels ("BIP84 SegWit (bc1q...)") are honest but too
    # long for the 12x24 face next to the Back corner -- the old header
    # clipped to "BIP84 SEGWIT (BC1Q.." at 8px, which is the exact clipping
    # failure this codebase keeps testing against. Short name in the header,
    # full label as a caption below it.
    SHORT_NAMES = SHORT_NAMES

    STEPPER_Y = 118
    STEPPER_S = 60   # square +/- targets, comfortably above the 34px minimum

    def __init__(self, mnemonic, template, index=0, xpub=None):
        # Two sources, one screen. `mnemonic` is the seed path (575s, secret
        # in RAM); `xpub` is an enrolled account (2.9s, no secret anywhere).
        # They are deliberately not two screens: the user is answering the
        # same question either way, and a second near-identical screen is
        # where layout fixes get applied to one copy and not the other.
        #
        # template=None means "all four types" (seed path only): one PBKDF2
        # stretch, then every template's address, so the user never has to
        # know their wallet's script type in advance. See handle_tap.
        self.mnemonic = mnemonic
        self.template = template
        self.index = index
        self.xpub = xpub
        self.account_path = None
        # On the enrolled-account path, the id the user wrote down at
        # enrolment. Shown here because this is the screen where they commit
        # to verifying against THIS account -- without it, nothing after
        # enrolment ever confirms which account is being used.
        self.fingerprint = None if xpub is None else drv.xpub_fingerprint(xpub)
        # named reference, not a positional index into self.buttons: a tap
        # on this exact button needs to reach `canvas` (to render the wait
        # screen below before the blocking derive call), which a normal
        # TapButton.on_tap(app) callback can't do -- handle_tap() special-
        # cases it by identity instead of by fragile list position.
        self.derive_button = TapButton(
            MARGIN, th.HEIGHT - 52, th.WIDTH - 2 * MARGIN, 44, "Derive Address",
            on_tap=None,  # unused: handle_tap() intercepts this button directly
        )
        s = self.STEPPER_S
        self.buttons = [
            TapButton(MARGIN, self.STEPPER_Y, s, s, "-", self._dec),
            TapButton(th.WIDTH - MARGIN - s, self.STEPPER_Y, s, s, "+", self._inc),
            self.derive_button,
            self.back_button(None),
        ]

    def _dec(self, app):
        self.index = max(self.index - 1, 0)

    def _inc(self, app):
        self.index = min(self.index + 1, 9)

    def handle_tap(self, app, canvas, x, y):
        if self.derive_button.contains(x, y):
            if self.xpub is not None:
                # ~2.9s measured: two unhardened steps, no PBKDF2, no seed.
                # Short enough that a static frame beats a progress bar, but
                # not so short that a frozen screen looks right.
                _xpub_wait_frame(self, canvas)
                path = "%s/0/%d" % (self.account_path or "m", self.index)
                addr = drv.address_from_xpub(self.xpub, self.template, index=self.index)
                app.push(AddressDisplayScreen(None, self.template, self.index,
                                              path, addr, xpub=self.xpub))
                return
            # A seed derivation is the hungriest thing this device does, and
            # this is the last moment to make room for it. The QR encoder is
            # the one module that may be resident here (the user exported a
            # SeedQR, then came to verify) and is safely reloadable -- no
            # screen below this one on the stack keeps a reference to it.
            # Measured on the board: without this, the ceremony-then-verify
            # path entered the stretch with ~7KB less and died loading the
            # EC chain afterwards.
            release_module("seedwitness.qr")
            # Render the wait screen BEFORE the blocking call: mnemonic_to_seed's
            # PBKDF2 (2048 rounds of HMAC-SHA512) measured 885s on a real CYD
            # at 160MHz, ~10 min at the 240MHz main.py now sets. That is far
            # too long to show a frozen screen, so the derive reports progress
            # back into this frame as it goes.
            waiting = DerivingScreen()
            waiting.draw(app, canvas)
            canvas.present()
            if self.template is None:
                # All four types from ONE stretch. The first derive pays the
                # PBKDF2 (progress bar above); _seed_for then serves the
                # cached seed, so each further template is EC math only, ~9s
                # on the board. The arrivals are painted as they land so the
                # ten-minute wait visibly turns into a quick finish.
                #
                # The "each type is quick:" caption appears only once the
                # stretch is over and the first type has landed: during the
                # ten-minute wait its slot belongs to the time estimate and
                # the activity dots, and a caption for a list with nothing
                # in it read as a leftover, not a promise. Progress is wired
                # to the first derive only -- the later ones are cache hits
                # whose single (1, 1) callback has nothing to report and
                # must not repaint over the arrival list.
                y = waiting.BAR_Y + waiting.BAR_H + 44
                results = []
                for i, t in enumerate(drv.PATH_TEMPLATES):
                    path, addr = drv.derive_address(
                        self.mnemonic, t, index=self.index,
                        passphrase=getattr(app, "passphrase", ""),
                        progress=(lambda done, total: waiting.update_progress(
                            canvas, done, total)) if i == 0 else None,
                    )
                    if i == 0:
                        # Stretch done: close the bar out at 100% (a no-op
                        # when the final PBKDF2 callback already did; on
                        # desktop, where hashlib has no progress hook, this
                        # is the only thing that ever fills it), then retire
                        # the wait furniture and caption the arrivals that
                        # are about to land.
                        waiting.update_progress(canvas, 1, 1)
                        waiting.clear_status(canvas)
                        canvas.text(MARGIN, y, "each type is quick:", th.MUTED)
                        y += 14
                    results.append((t, path, addr))
                    canvas.text(MARGIN, y, "BIP%d %s" % (
                        t.bip, self.SHORT_NAMES.get(t.bip, "")), th.GOOD,
                        font=fonts.S)
                    y += 18
                    canvas.present()
                app.push(AllAddressesScreen(self.mnemonic, self.index, results))
                return
            path, addr = drv.derive_address(
                self.mnemonic, self.template, index=self.index,
                passphrase=getattr(app, "passphrase", ""),
                progress=lambda done, total: waiting.update_progress(canvas, done, total),
            )
            app.push(AddressDisplayScreen(self.mnemonic, self.template, self.index, path, addr))
            return
        super().handle_tap(app, canvas, x, y)

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        if self.template is None:
            self.header(canvas, "All Types")
            caption = "one derivation, all 4 types"
        else:
            short = self.SHORT_NAMES.get(self.template.bip, "")
            self.header(canvas, "BIP%d %s" % (self.template.bip, short))
            caption = self.template.label
        canvas.text(MARGIN, HEADER_H + 8, caption, th.MUTED,
                    font=fonts.S)
        if self.fingerprint:
            canvas.text(MARGIN, HEADER_H + 28, "account id " + self.fingerprint,
                        th.ACCENT, font=fonts.S)

        caption = "receive address index"
        canvas.text((th.WIDTH - len(caption) * th.CHAR_W) // 2, self.STEPPER_Y - 18,
                    caption, th.MUTED)
        # The value is the one thing being chosen on this screen, so it gets
        # the hero face, centred between the stepper buttons at their height.
        idx_str = str(self.index)
        ix = (th.WIDTH - len(idx_str) * fonts.L.width) // 2
        iy = self.STEPPER_Y + (self.STEPPER_S - fonts.L.height) // 2
        canvas.text(ix, iy, idx_str, th.ACCENT, font=fonts.L)

        hint = "0 = first receive address"
        canvas.text((th.WIDTH - len(hint) * th.CHAR_W) // 2,
                    self.STEPPER_Y + self.STEPPER_S + 16, hint, th.MUTED)
        # The user's own tick for this index, restated without
        # interpretation: "marked" and nothing else. The device records
        # that the tick exists; what it means (checked, funded, anything)
        # is the user's bookkeeping, and wording it as a verdict here
        # would claim knowledge the device cannot have.
        if self.xpub is not None and self.index in acct.marks(self.xpub):
            canvas.text((th.WIDTH - 6 * th.CHAR_W) // 2,
                        self.STEPPER_Y + self.STEPPER_S + 34, "marked",
                        th.ACCENT)
        for b in self.buttons:
            b.draw(canvas, accent=(b is self.derive_button))


class DerivingScreen(Screen):
    """Not pushed onto the navigation stack -- drawn directly as a one-shot
    interim frame by AddressIndexScreen before its blocking derive_address()
    call. See that class's handle_tap().

    draw() paints the static frame once; update_progress() is then called
    repeatedly from inside the derive and repaints ONLY what changed: the new
    slice of the bar, the percentage when it moved, one activity dot, and the
    time estimate when its text changed. That partial-repaint discipline
    matters: a full draw() costs ~0.5s on real hardware, so redrawing
    everything per update would add minutes to an operation that already
    takes ~10, and would visibly flicker.

    Two things beyond the bar, both born of real freeze reports (a working
    device holding a near-still frame for ten minutes is indistinguishable
    from a dead one):

    * Motion: three small dots under the percentage, one lit, the lit one
      stepping right by one on every progress callback (~4.5s apart on the
      board -- see mp_shims/pbkdf2.py's _PROGRESS_EVERY). A slow, quiet
      cycle: it only has to prove the device is alive, not entertain. The
      dots are 6px fill_rect squares, not fill_circles: at ~1mm on this
      panel the shapes are indistinguishable, but a circle rasterises
      through circle_spans as 7 driver calls per dot and the driver's
      per-call overhead is ~3ms -- measured on the board, the two-dot
      repaint costs 42ms as circles and 9ms as squares.

    * A time estimate, measured, never assumed: the observed round rate since
      the first callback, projected over the remaining rounds. Withheld until
      ETA_MIN_PERCENT of the stretch has been timed, shown only in whole
      "about N min left" steps (rounded UP -- a pleasant surprise beats a
      pulled plug), and never allowed to sit at "1 min left": the last step
      is "nearly done", which also honestly covers the EC math that follows
      the stretch."""

    BAR_Y = HEADER_H + 124   # below five wrapped 8x16 lines of explanation
    BAR_H = 22

    # The estimate line and the activity dots, below the percentage. Fixed
    # 18-char field for the estimate so every repaint fully overwrites the
    # previous text ("about 10 min left" is 17 chars, the widest string).
    ETA_Y = BAR_Y + BAR_H + 42
    ETA_CHARS = 18
    DOT_Y = BAR_Y + BAR_H + 76   # dot centres
    DOT_S = 6                    # dot side (square: see the class docstring)
    DOT_GAP = 18                 # centre-to-centre; 3 dots span 42px

    # Withhold the estimate until this share of the stretch has been timed.
    # 5% is ~29s and 6-7 callbacks on the board: PBKDF2 rounds are uniform,
    # so the cumulative rate converges fast, and by 5% its error is a few
    # percent -- inside the whole-minute rounding of the display. Earlier
    # than that the number swings visibly and teaches the user to distrust
    # it; much later and it misses the window where "is this thing dead?"
    # is being decided.
    ETA_MIN_PERCENT = 5

    def __init__(self):
        self._t0 = None        # ticks_ms at the first progress callback
        self._d0 = 0           # rounds already done at that callback
        self._lit = None       # index of the lit activity dot, None = none yet
        self._bar_px = 0       # filled bar width already painted
        self._last_pct = None  # last percentage string painted
        self._last_eta = None  # last estimate string painted
        self._last_mins = None # last whole-minute estimate shown
        self._finished = False

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Deriving...")
        y = HEADER_H + 14
        for line in th.wrap_text(
            "Computing the seed from your mnemonic (PBKDF2, 2048 rounds). "
            "This takes several minutes on this hardware -- please wait.",
            (th.WIDTH - 2 * MARGIN) // fonts.S.width,
        ):
            canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
            y += 20
        # empty bar outline, filled in by update_progress()
        canvas.rect(MARGIN, self.BAR_Y, th.WIDTH - 2 * MARGIN, self.BAR_H, th.BUTTON_BORDER)
        # all three dots start dim; update_progress() lights one at a time
        for i in range(3):
            self._dot(canvas, i, th.BUTTON_BORDER)

    def _dot(self, canvas, i, color):
        s = self.DOT_S
        canvas.fill_rect(th.WIDTH // 2 + (i - 1) * self.DOT_GAP - s // 2,
                         self.DOT_Y - s // 2, s, s, color)

    def _eta_text(self, canvas, s):
        if s == self._last_eta:
            return
        self._last_eta = s
        pad = self.ETA_CHARS - len(s)
        left = pad // 2
        s = " " * left + s + " " * (pad - left)
        canvas.text((th.WIDTH - self.ETA_CHARS * fonts.S.width) // 2,
                    self.ETA_Y, s, th.MUTED, bg=th.BG, font=fonts.S)

    def clear_status(self, canvas):
        """Blank the estimate line and the dots, releasing everything below
        the percentage to the caller (the all-types arrival list paints
        there), and stop reacting to any further progress callbacks."""
        self._finished = True
        canvas.fill_rect(MARGIN, self.ETA_Y, th.WIDTH - 2 * MARGIN,
                         self.DOT_Y + self.DOT_S // 2 + 1 - self.ETA_Y, th.BG)

    def update_progress(self, canvas, done, total):
        if total <= 0 or self._finished:
            return
        now = ticks_ms()
        if self._t0 is None:
            self._t0 = now
            self._d0 = done
        # bar: paint only the newly earned slice, never the whole fill --
        # the repaint cost must not scale with how far along the stretch is
        inner_w = th.WIDTH - 2 * MARGIN - 2
        filled = (inner_w * done) // total
        if filled > self._bar_px:
            canvas.fill_rect(MARGIN + 1 + self._bar_px, self.BAR_Y + 1,
                             filled - self._bar_px, self.BAR_H - 2,
                             th.ENTROPY_BAR)
            self._bar_px = filled
        # The percentage is what a user staring at a ~10 minute operation is
        # actually watching, so it gets the primary face. A fixed-width
        # right-aligned field ("  7%" -> "100%") plus bg= means each repaint
        # fully overwrites the previous value with no leftover digits.
        pct = "%3d%%" % ((100 * done) // total)
        if pct != self._last_pct:
            self._last_pct = pct
            canvas.text((th.WIDTH - 4 * fonts.M.width) // 2,
                        self.BAR_Y + self.BAR_H + 12, pct, th.FG, bg=th.BG,
                        font=fonts.M)
        if done >= total:
            # Final callback (or a cache hit's single (1, 1)). The stretch is
            # over but the EC math that follows still takes seconds, so say
            # so honestly rather than freezing a full bar. Latch: later
            # callbacks (all-types cache hits) must not repaint over whatever
            # the caller draws here next.
            self._finished = True
            self._eta_text(canvas, "nearly done")
            canvas.present()
            return
        # motion: step the lit dot one place right, dimming the previous.
        # Two tiny fill_rect calls per update, nothing else moves.
        prev = self._lit
        self._lit = 0 if prev is None else (prev + 1) % 3
        if prev is not None:
            self._dot(canvas, prev, th.BUTTON_BORDER)
        self._dot(canvas, self._lit, th.ACCENT)
        # the estimate, once enough of the stretch has been timed to trust it
        seen = done - self._d0
        elapsed = ticks_diff(now, self._t0)
        if seen * 100 >= total * self.ETA_MIN_PERCENT and elapsed > 0 and seen > 0:
            remaining = (elapsed * (total - done)) // seen
            mins = (remaining + 59999) // 60000   # whole minutes, rounded UP
            if (self._last_mins is not None and mins > self._last_mins
                    and remaining <= self._last_mins * 60000 + 30000):
                # rounding wobble around a minute boundary, not a real
                # slowdown: hold the number rather than flick 8-9-8. A
                # genuine slowdown (>30s past the shown ceiling) still
                # raises the estimate, because honesty outranks smoothness.
                mins = self._last_mins
            self._last_mins = mins
            self._eta_text(canvas, "nearly done" if mins <= 1
                           else "about %d min left" % mins)
        canvas.present()


class AllAddressesScreen(MenuScreen):
    """All four address types from one derivation, one row each.

    This exists so choosing the wrong type can never masquerade as a wrong
    backup: instead of committing to BIP44/49/84/86 before the ten-minute
    stretch, the user scans four labelled rows and taps the one shaped like
    what their wallet shows. A full 62-char taproot address cannot fit a list
    row legibly, so each row is the type name over a truncated address --
    enough to recognise the match -- and tapping it opens the existing
    AddressDisplayScreen for the real character-by-character comparison.

    A MenuScreen, not a new layout: the rows are ordinary menu buttons (two
    centred lines each), which keeps this screen's cost close to zero on a
    board where screens.py growth is a boot-breaking change.
    """

    title = "All Types"
    BOTTOM_RESERVED = 14   # one hint line under the rows

    def __init__(self, mnemonic, index, results):
        self.mnemonic = mnemonic
        self.index = index
        items = []
        for t, path, addr in results:
            label = "BIP%d %s\n%s..%s" % (
                t.bip, SHORT_NAMES.get(t.bip, ""),
                addr[:10], addr[-4:])
            items.append((label,
                          lambda app, t=t, p=path, a=addr: app.push(
                              AddressDisplayScreen(mnemonic, t, index, p, a,
                                                   from_all=True))))
        super().__init__(items)
        self.buttons = list(self.buttons) + [self.back_button(None)]

    def draw(self, app, canvas):
        super().draw(app, canvas)
        canvas.text(MARGIN, th.HEIGHT - 12, "tap the row your wallet shows",
                    th.MUTED)


def draw_grouped_address(canvas, address, y, font, pitch, group=4):
    """An address in 4-char groups with alternating colour, wrapped to
    whatever a full line of groups holds at `font`.

    One function, two callers (the comparison screen and the address-QR
    screen), because the chunking IS the feature: groups give the eye a
    resumable position, the colour alternation makes a skipped group visible,
    and two screens chunking differently would break the reader's place when
    hopping between them. Geometry is derived from the face, never hardcoded
    -- see AddressDisplayScreen's history for why that rule is load-bearing.

    Returns the y just below the last line drawn.
    """
    gpl = (th.WIDTH - 2 * MARGIN + font.width) // ((group + 1) * font.width)
    per_line = group * gpl
    for i in range(0, len(address), per_line):
        x = MARGIN
        line = address[i:i + per_line]
        for j in range(0, len(line), group):
            group_index = (i + j) // group
            color = th.GROUP_ALT if group_index % 2 == 0 else th.FG
            canvas.text(x, y, line[j:j + group], color, font=font)
            x += (group + 1) * font.width
        y += pitch
    return y


class AccountKeyScreen(Screen):
    """Read-only account-key choices for a seed that was just verified.

    The canonical xpub and the matching SLIP-132 spelling are two encodings
    of the same public key.  Keeping this choice after address comparison,
    rather than presenting it as a verification result, preserves the
    security instruction: compare the receive address; use the key form only
    for wallet interoperability.  The record handed to AccountQRScreen is an
    ephemeral dictionary and is never passed to accounts.add().
    """

    def __init__(self, xpub, template, path):
        self.xpub = xpub
        self.template = template
        self.path = path
        self.rec = {"xpub": xpub, "bip": template.bip, "label": "direct check"}
        w = th.WIDTH - 2 * MARGIN
        alt = drv.SLIP132.get(template.bip)
        if alt:
            half = (w - BUTTON_GAP) // 2
            qr_row = [
                TapButton(MARGIN, 252, half, 44, "xpub QR",
                          lambda app: self._show_qr(app, False)),
                TapButton(MARGIN + half + BUTTON_GAP, 252, half, 44,
                          alt[0] + " QR",
                          lambda app: self._show_qr(app, True)),
            ]
        else:
            qr_row = [
                TapButton(MARGIN, 252, w, 44, "Show xpub QR",
                          lambda app: self._show_qr(app, False)),
            ]
        self.buttons = qr_row + [self.back_button(None)]

    def _show_qr(self, app, slip132):
        # Lazy cross-flow load: QR construction imports the relatively heavy
        # encoder, so pay for it only after the user chooses a form.
        qr_screen = flow_screen("accounts", "AccountQRScreen")
        app.push(qr_screen(self.rec, slip132=slip132))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Account Key")
        y = HEADER_H + 8
        canvas.text(MARGIN, y, self.path, th.MUTED, font=fonts.S)
        y += 22
        canvas.text(MARGIN, y, "READ-ONLY VIEW", th.GOOD, font=fonts.S)
        y += 24
        alt = drv.SLIP132.get(self.template.bip)
        if alt:
            body = (
                "This action stores nothing. xpub and %s are the same account key. "
                "%s changes only the version bytes and labels this BIP%d "
                "script type. Use the form a wallet requests. Verify the "
                "seed by comparing the receive address. Sharing either "
                "form reveals every address in this account."
            ) % (alt[0], alt[0], self.template.bip)
        elif self.template.bip == 86:
            body = (
                "This action stores nothing. xpub is the account-key form. SLIP-132 "
                "predates taproot, so there is no zpub form. Verify the seed "
                "by comparing the receive address. Sharing this key reveals "
                "every address in this account."
            )
        else:
            body = (
                "This action stores nothing. xpub is the standard account-key form "
                "for BIP44. Verify the seed by comparing the receive address. "
                "Sharing this key reveals every address in this account."
            )
        for line in th.wrap_text(body, (th.WIDTH - 2 * MARGIN) // th.CHAR_W):
            canvas.text(MARGIN, y, line, th.FG)
            y += 12
        for b in self.buttons:
            b.draw(canvas)


class AddressDisplayScreen(Screen):
    ADDR_FONT = fonts.M

    # This screen exists solely so an address can be compared character by
    # character against another device, and the layout serves that job:
    #
    #  * groups of 4, split by a space -- the same chunking hardware wallets
    #    use, because it gives the eye a resumable position ("third group,
    #    second char") instead of an unbroken 42-char run
    #  * alternating white/green groups, so a line-skip or group-skip while
    #    glancing between two screens is visible as a colour mismatch
    #  * bech32 is lowercase digits+letters, exactly what the 12x24 face
    #    covers with full-detail glyphs
    #
    # Everything is DERIVED, never hardcoded. A line budget wider than the
    # screen is not a cosmetic bug -- canvas.text() clamps to what fits and
    # SILENTLY DROPS the overflow, so the device would calmly display a
    # WRONG address; exactly what this device exists to catch. That is not
    # hypothetical: a hardcoded 18-char budget survived the landscape-to-
    # portrait move and dropped 4 characters per line while looking
    # perfectly plausible on screen (see tests/test_no_text_clipping.py).
    GROUP = 4
    GROUPS_PER_LINE = (th.WIDTH - 2 * MARGIN + ADDR_FONT.width) // (
        (GROUP + 1) * ADDR_FONT.width)   # groups + single-space separators
    LINE_PITCH = 30

    def __init__(self, mnemonic, template, index, path, address,
                 from_all=False, xpub=None):
        # `xpub` set means the enrolled-account path: stepping to the
        # neighbouring index is cheap (no seed, no PBKDF2), and the account
        # exists in the store, so the per-(account, index) tick has
        # somewhere to live. Both are meaningless on the seed path, where
        # nothing is enrolled and every index change costs a stretch.
        self.mnemonic = mnemonic
        self.template = template
        self.index = index
        self.path = path
        self.address = address
        self.xpub = xpub
        self.marked = xpub is not None and index in acct.marks(xpub)
        # "Done", not "Home": App._sync_back_labels() rewrites any button
        # literally labelled Home/Back to match stack depth, and this button
        # resets to home rather than popping -- it must not get relabelled
        # "Back" and lie about where it goes.
        #
        # "QR" is a plain tap, deliberately ungated. A receive address is
        # public -- it is the thing you hand a payer -- so borrowing the seed
        # QR's warning/hold/blank ceremony here would be security theatre
        # that teaches users to hold through warnings. The gate stays where
        # the secret is (QRExportScreen), and nowhere else.
        # From the all-types list a pop lands back on that list, not on the
        # index stepper, so the button says "Back" there instead of lying
        # about where it goes ("Change Index" stays on the per-type flow,
        # where the pop really does land on the stepper).
        row = [
            TapButton(MARGIN, th.HEIGHT - 48, 112, 40,
                      "Back" if from_all else "Change Index",
                      self._back_to_index),
            TapButton(MARGIN + 120, th.HEIGHT - 48, 40, 40, "QR",
                      lambda app: app.push(AddressQRScreen(self.address, self.path))),
            TapButton(th.WIDTH - 56 - MARGIN, th.HEIGHT - 48, 56, 40, "Done", self._to_menu),
        ]
        # Offered only on the seed path. Arriving here from an enrolled
        # account means there is no mnemonic to enrol and the account already
        # exists, so the button would be a no-op dressed as an action.
        self.key_button = None
        if mnemonic is not None:
            w = th.WIDTH - 2 * MARGIN
            half = (w - BUTTON_GAP) // 2
            self.key_button = TapButton(
                MARGIN, th.HEIGHT - 92, half, 40, "Account Key", on_tap=None)
            row.insert(0, self.key_button)
            row.insert(1, TapButton(
                MARGIN + half + BUTTON_GAP, th.HEIGHT - 92, half, 40,
                "Enrol Account",
                lambda app: app.push(EnrolScreen(mnemonic, template))))
        # Enrolled path only: the tick and the flip-through steppers, in the
        # slot the seed path gives to Enrol (the two can never coexist).
        # "Mark", deliberately neutral: the device records the tick and
        # nothing else -- it must never imply a ticked address is verified,
        # safe or funded, because it cannot know any of those. </> are
        # intercepted in handle_tap (they need `canvas` for the wait frame),
        # like the stepper's Derive button.
        self.mark_button = self.prev_button = self.next_button = None
        if xpub is not None:
            self.mark_button = TapButton(
                MARGIN, th.HEIGHT - 92, 104, 40,
                "Unmark" if self.marked else "Mark", self._toggle_mark)
            self.prev_button = TapButton(MARGIN + 112, th.HEIGHT - 92, 52, 40,
                                         "<", on_tap=None)
            self.next_button = TapButton(th.WIDTH - MARGIN - 52, th.HEIGHT - 92,
                                         52, 40, ">", on_tap=None)
            row += [self.mark_button, self.prev_button, self.next_button]
        self.buttons = harmonise_scale(row)

    def _back_to_index(self, app):
        app.pop()

    def _toggle_mark(self, app):
        self.marked = acct.toggle_mark(self.xpub, self.index)
        self.mark_button.label = "Unmark" if self.marked else "Mark"
        self.mark_button.font = self.mark_button._fit_font()

    def _step(self, app, canvas, step):
        """Derive the neighbouring index from the enrolled account and
        replace this screen with its display.

        Cheap enough to flip through (~3s from an xpub, no seed involved),
        which is the point: checking a run of successive addresses one tap
        at a time instead of a round trip through the index stepper for
        each. Bounds match the stepper's so the two ways of choosing an
        index can never disagree about what is reachable."""
        index = self.index + step
        if not 0 <= index <= 9:
            return
        _xpub_wait_frame(self, canvas)
        addr = drv.address_from_xpub(self.xpub, self.template, index=index)
        path = "%s/%d" % (self.path.rsplit("/", 1)[0], index)
        app.pop()
        # Keep the index stepper underneath honest: after flipping forward
        # three addresses, Change Index must land on the index being shown,
        # not the one it was left at.
        parent = app.screen
        if getattr(parent, "xpub", None) == self.xpub:
            parent.index = index
        app.push(AddressDisplayScreen(None, self.template, index, path, addr,
                                      xpub=self.xpub))

    def handle_tap(self, app, canvas, x, y):
        if self.key_button is not None and self.key_button.contains(x, y):
            _account_key_wait_frame(self, canvas)
            path, xpub = drv.account_xpub(
                self.mnemonic, self.template,
                passphrase=getattr(app, "passphrase", ""))
            app.push(AccountKeyScreen(xpub, self.template, path))
            return
        if self.xpub is not None:
            if self.next_button.contains(x, y):
                self._step(app, canvas, 1)
                return
            if self.prev_button.contains(x, y):
                self._step(app, canvas, -1)
                return
        super().handle_tap(app, canvas, x, y)

    def _to_menu(self, app):
        app.reset_to_home()

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Compare Address")
        canvas.text(MARGIN, HEADER_H + 8, self.path, th.MUTED, font=fonts.S)
        if self.marked:
            # the user's own tick, restated without interpretation -- see
            # accounts.marks for why no stronger word is allowed here
            canvas.text(th.WIDTH - MARGIN - 6 * fonts.S.width, HEADER_H + 8,
                        "marked", th.ACCENT, font=fonts.S)
        canvas.text(MARGIN, HEADER_H + 28,
                    "read it against your wallet:", th.MUTED)

        # Step the face down rather than let a long address run under the
        # buttons. A 62-character taproot address needs six lines at the 12x24
        # face and only five fit above the Enrol row, so the sixth was drawn
        # underneath it and the address was SILENTLY TRUNCATED on the one
        # screen whose whole job is character-by-character comparison. Someone
        # checking a taproot address against their wallet would have compared a
        # partial string and concluded it matched.
        #
        # Measured against the space actually available rather than keyed to
        # address type, so a longer address or a taller button row cannot
        # reintroduce it.
        top = HEADER_H + 46
        below = [b.y for b in self.buttons if b.y > top]
        avail = (min(below) if below else th.HEIGHT) - top - 6
        font, pitch = self.ADDR_FONT, self.LINE_PITCH
        for cand, cand_pitch in ((self.ADDR_FONT, self.LINE_PITCH), (fonts.S, 20)):
            gpl = (th.WIDTH - 2 * MARGIN + cand.width) // ((self.GROUP + 1) * cand.width)
            lines = -(-len(self.address) // (self.GROUP * gpl))
            font, pitch = cand, cand_pitch
            if lines * cand_pitch <= avail:
                break
        draw_grouped_address(canvas, self.address, top, font, pitch, self.GROUP)

        for b in self.buttons:
            b.draw(canvas, accent=True)


class AddressQRScreen(Screen):
    """The derived receive address as a QR, for scanning into a payer's
    wallet or checking against one.

    Deliberately NOT gated -- no warning, no hold, no auto-blank. A receive
    address is public by definition: handing it out is its entire purpose.
    The seed QR ceremony exists because that QR is the whole wallet; applying
    it here would be security theatre, would train users to tap through
    warnings, and would make a routine action annoying. A plain tap opens
    this screen, it sits on the navigation stack like any other, and it stays
    up until dismissed. tests/test_address_qr.py pins all of that.

    The address text stays on screen below the symbol, in the same 4-char
    alternating-colour groups as the comparison screen, because comparing it
    against the wallet is the actual job -- the QR is a convenience on top.

    Encoding: bech32 addresses are case-insensitive, so they are uppercased
    into QR alphanumeric mode, which is compact enough to buy a whole symbol
    version (42 chars: version 2 at 6px modules, versus version 3 in byte
    mode). Base58 addresses are case-significant and use byte mode.
    """

    QUIET = 4
    ADDR_FONT = fonts.S      # caption face: 5 groups per line, fits under the QR
    LINE_PITCH = 18

    def __init__(self, address, path):
        from .. import qr as qrm
        self.address = address
        self.path = path
        if address == address.lower():
            # lowercase in, so uppercasing loses nothing: bech32 territory
            try:
                self.size, self.mat = qrm.alphanumeric_qr(address.upper())
            except ValueError:      # lowercase but outside the alnum set
                self.size, self.mat = qrm.byte_qr(address.encode())
        else:
            # mixed case is payload (base58): byte mode, never case-folded
            self.size, self.mat = qrm.byte_qr(address.encode())
        self.buttons = [self.back_button(None)]

    def _text_lines(self):
        gpl = (th.WIDTH - 2 * MARGIN + self.ADDR_FONT.width) // (
            5 * self.ADDR_FONT.width)
        per_line = 4 * gpl
        return -(-len(self.address) // per_line)  # ceil

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Address QR")
        canvas.text(MARGIN, HEADER_H + 4, self.path, th.MUTED)
        y0 = HEADER_H + 14
        # The module size is derived so the symbol AND the full address text
        # always share the panel: the text is the point, the QR must not
        # squeeze it off. Every current address shape lands at 5-6px/module;
        # tests/test_address_qr.py holds the result to the 4px scan floor the
        # seed QR tests established, so a future longer address cannot
        # silently produce an unscannable symbol.
        text_h = self._text_lines() * self.LINE_PITCH
        cells = self.size + 2 * self.QUIET
        px = min(th.WIDTH // cells, (th.HEIGHT - y0 - text_h - 6) // cells)
        _, side, _ = draw_qr_symbol(canvas, self.size, self.mat, y0,
                                    quiet=self.QUIET, px=px)
        draw_grouped_address(canvas, self.address, y0 + side + 8,
                             self.ADDR_FONT, self.LINE_PITCH)
        for b in self.buttons:
            b.draw(canvas)
