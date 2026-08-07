"""Verify-a-Seed flow: entry choice, the on-screen keyboard, checksum
rejection, and backup-confirmation results.

Load-on-demand: reached only through screens.flow_screen() at the navigation
point and unloaded (device only) when the session ends -- see screens.py's
docstring. Needs the mnemonic machinery (prefix search, checksum); must NOT
import derive or the embit EC chain, which load only when a derivation is
actually requested (the address flow).
"""
from .. import mnemonic as mn
from . import fonts
from . import theme as th
from .screens import (
    BUTTON_GAP,
    HEADER_H,
    MARGIN,
    MenuScreen,
    Screen,
    TapButton,
    _demo_btn,
    flow_screen,
    harmonise_scale,
)


class VerifyEntryScreen(MenuScreen):
    title = "Verify Seed"

    def __init__(self):
        super().__init__(self._items_for(None))

    def _items_for(self, app):
        items = []
        # Labels are split across lines so TapButton can size them up: on one
        # line, "Enter Seed Manually" is 19 characters (228px at 12px/char)
        # and would fall to the caption face; as two lines it holds 12x24.
        if app is not None and getattr(app, "last_mnemonic", None):
            items.append(
                ("Use Last\nRolled Seed",
                 lambda app: app.push(flow_screen("address", "DerivationPathScreen")(
                     app.last_mnemonic)))
            )
        items.append(("Enter Seed\nManually",
                      lambda app: app.push(ManualWordCountScreen())))
        return items

    # The demo [!], left of the Back corner: typing 12 or 24 words to try
    # the verify flow is as punishing as 50 rolls. This is the path's entry
    # page, so by construction nothing real has been typed yet -- the demo
    # can never displace a half-entered genuine seed.
    def _demo_button(self):
        return _demo_btn(self._fill_demo)

    def _fill_demo(self, app):
        """Continue the verify flow with the canonical BIP39 test mnemonic
        (12x "abandon..." + "about"). Fixed and published in the BIP texts
        themselves, so its derived addresses can even be checked against the
        specs -- and being the best-known mnemonic in Bitcoin, it is
        unmistakably nobody's wallet."""
        m = " ".join(["abandon"] * 11) + " about"
        app.demo = True
        app.last_mnemonic = m
        app.pop()      # the confirmation gate
        app.push(flow_screen("address", "DerivationPathScreen")(m))

    def draw(self, app, canvas):
        self._items = self._items_for(app)
        # back_button(app), not (None): this list is rebuilt every frame,
        # AFTER App.draw() has already run _sync_back_labels, so a None here
        # discarded the sync and left "Back" on a pop that ends the session.
        self.buttons = self._build_buttons() + [self._demo_button(),
                                                self.back_button(app)]
        super().draw(app, canvas)


class ManualWordCountScreen(MenuScreen):
    title = "Seed Length"

    def __init__(self):
        super().__init__([
            ("12 words", lambda app: app.push(WordEntryScreen(12))),
            ("24 words", lambda app: app.push(WordEntryScreen(24))),
        ])
        self.buttons = list(self.buttons) + [self.back_button(None)]


class WordEntryScreen(Screen):
    """On-screen keyboard word entry -- the touch-native replacement for the
    Pi build's letter-cursor scroller. Tap letters to build a prefix, tap a
    matching candidate to confirm the word. Used both for verifying an
    existing seed (target_mnemonic=None) and for backup confirmation
    (target_mnemonic=the mnemonic just generated, compared word-for-word)."""

    LETTERS = "abcdefghijklmnopqrstuvwxyz"
    # 7 keys per row, not 13. In the old 320-wide landscape layout 13 columns
    # gave 24px keys; at 240 wide that becomes 18px, about 3mm -- below what
    # can be hit reliably with a fingertip on a resistive panel. Portrait has
    # height to spare, so the keyboard trades columns for rows: 7 columns of
    # 34px across 4 rows. Every key is then a comfortable target, and the
    # alphabet still fits above the action row.
    KEYS_PER_ROW = 7
    KEY_W = th.WIDTH // KEYS_PER_ROW
    ACTION_H = 34

    # Keys confirm on a hold, like everything else, but the briefest one in the
    # app. A key is 34px wide -- narrower than the fingertip pressing it -- so
    # you cannot see what you hit, which is exactly the case a confirm step is
    # for: a wrong letter here means re-entering a seed word. Kept very short
    # because a word is up to 8 presses and a seed is 12 or 24 words.
    SWEEP_MS = 120
    SWEEP_STEPS = 5

    # The preview band: a magnified copy of the key under the finger, drawn
    # well above the keyboard where the hand is not covering the panel.
    PREVIEW_H = 44

    def __init__(self, word_count, words=None, target_mnemonic=None):
        self.word_count = word_count
        self.words = words or []
        self.prefix = ""
        self.target_mnemonic = target_mnemonic
        self.buttons = self._build_keyboard()

    def _build_keyboard(self):
        """Keys grow into whatever the candidate list leaves free.

        The height was fixed at 34px, which left a band of dead space between
        the candidates and the keys. It is now derived from the gap between
        the candidate rows and the action row, so the keys are as tall as the
        layout allows. 7 columns is the minimum that still fits 26 letters in
        4 rows -- 6 columns would need a 5th row and end up with SHORTER keys,
        so wider columns would cost more height than they gain.

        The final row holds 5 letters, not 7, and is centred rather than left
        aligned so the leftover space reads as deliberate.
        """
        buttons = []
        n = len(self.LETTERS)
        rows = -(-n // self.KEYS_PER_ROW)  # ceil
        cand_bottom = self.CAND_TOP + 2 * (self.CAND_H + self.CAND_GAP)
        avail = th.HEIGHT - self.ACTION_H - 2 - cand_bottom
        key_h = avail // rows
        self.KEY_H = key_h  # remembered for the touch-target assertions
        top = cand_bottom
        # centre the whole block horizontally: 7 * 34 = 238 of 240
        x_off = (th.WIDTH - self.KEYS_PER_ROW * self.KEY_W) // 2
        for i, letter in enumerate(self.LETTERS):
            col = i % self.KEYS_PER_ROW
            row = i // self.KEYS_PER_ROW
            in_row = min(self.KEYS_PER_ROW, n - row * self.KEYS_PER_ROW)
            # centre a short final row
            row_off = x_off + (self.KEYS_PER_ROW - in_row) * self.KEY_W // 2
            x = row_off + col * self.KEY_W
            y = top + row * key_h
            buttons.append(TapButton(x, y, self.KEY_W, key_h, letter, self._make_letter(letter)))
        action_y = th.HEIGHT - self.ACTION_H
        half = th.WIDTH // 2
        buttons.append(TapButton(0, action_y, half, self.ACTION_H, "<- Back", self._backspace))
        buttons.append(TapButton(half, action_y, th.WIDTH - half, self.ACTION_H, "Cancel",
                                  lambda app: app.pop()))
        return buttons

    def _make_letter(self, letter):
        def handler(app):
            self.prefix += letter
            cands = self._candidates()
            if len(cands) == 1:
                self._confirm_word(app, cands[0])
        return handler

    def _backspace(self, app):
        if self.prefix:
            self.prefix = self.prefix[:-1]
        elif self.words:
            self.words.pop()

    def _candidates(self):
        if not self.prefix:
            return []
        # binary search, not a scan -- on-device the wordlist is on flash, so
        # scanning would cost 2048 file reads per keystroke (see mnemonic.py)
        return mn.words_with_prefix(self.prefix, 4)

    def _confirm_word(self, app, word):
        # Guard, not decoration: this used to be reached only when the list was
        # short, but if `words` ever arrives at the target length without
        # submitting -- as it did when a failed attempt was restored -- then
        # `==` never matches again, every further tap appends another word, and
        # the screen wedges with no way forward. `>=` cannot get stuck.
        if len(self.words) >= self.word_count:
            return
        self.words.append(word)
        self.prefix = ""
        if len(self.words) >= self.word_count:
            candidate_mnemonic = " ".join(self.words)
            app.pop()
            if self.target_mnemonic is not None:
                app.push(BackupResultScreen(
                    success=mn.mnemonics_match(candidate_mnemonic, self.target_mnemonic),
                    target_mnemonic=self.target_mnemonic,
                ))
            elif mn.validate_mnemonic(candidate_mnemonic):
                app.last_mnemonic = candidate_mnemonic
                app.push(flow_screen("address", "DerivationPathScreen")(
                    candidate_mnemonic))
            else:
                app.push(InvalidSeedScreen(self.word_count, words=self.words))

    def handle_tap(self, app, canvas, x, y):
        for b in self._candidate_buttons():
            if b.contains(x, y):
                b.on_tap(app)
                return
        super().handle_tap(app, canvas, x, y)

    # Candidates go 2 across, 2 down -- NOT 4 across. On a 240px screen four
    # columns give 50px buttons, but the longest BIP39 word is 8 characters,
    # which is 64px even at the 8x16 caption face. Every long candidate
    # ("shallow", "abandon") therefore spilled past its own button and over
    # its neighbour. Two columns give 108px, room for any word in the list.
    # CAND_GAP is tighter than the global BUTTON_GAP: those 4px are what buy
    # the keyboard rows enough height to hold the 12x24 face for their keys.
    CAND_COLS = 2
    CAND_H = 30
    CAND_GAP = 6
    CAND_TOP = 72

    def _press_preview(self, canvas, b):
        """Show the held key, magnified, above the keyboard.

        The sweep alone is not enough here: a fingertip covers a 34px key
        completely, so any feedback drawn on the key itself is under the hand
        that needs to read it. This puts a 32px copy of the letter -- with the
        word it is building -- in the candidate band near the top of the
        screen, which is always visible while typing.

        Only letter keys get this; Back and Cancel are wide and self-evident.
        """
        if len(b.label) != 1:
            return
        y = self.CAND_TOP
        canvas.fill_rect(0, y, th.WIDTH, self.PREVIEW_H, th.BG)
        canvas.rect(MARGIN, y, th.WIDTH - 2 * MARGIN, self.PREVIEW_H, th.ACCENT)
        canvas.text((th.WIDTH - fonts.L.width) // 2, y + 6, b.label, th.ACCENT,
                    bg=th.BG, font=fonts.L)
        # what the word becomes if this key commits
        nxt = self.prefix + b.label
        canvas.text(MARGIN + 4, y + self.PREVIEW_H - 12, nxt[-24:], th.MUTED, bg=th.BG)
        canvas.present()

    def _candidate_buttons(self):
        cands = self._candidates()
        buttons = []
        w = (th.WIDTH - 2 * MARGIN - (self.CAND_COLS - 1) * BUTTON_GAP) // self.CAND_COLS
        for i, word in enumerate(cands):
            col = i % self.CAND_COLS
            row = i // self.CAND_COLS
            x = MARGIN + col * (w + BUTTON_GAP)
            y = self.CAND_TOP + row * (self.CAND_H + self.CAND_GAP)
            buttons.append(TapButton(x, y, w, self.CAND_H, word,
                                     lambda app, w=word: self._confirm_word(app, w)))
        return harmonise_scale(buttons)

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        title = "Confirm" if self.target_mnemonic is not None else "Word"
        self.header(canvas, "%s %d/%d" % (title, len(self.words) + 1, self.word_count))

        entered = " ".join(self.words[-3:]) if self.words else "(none yet)"
        canvas.text(MARGIN, HEADER_H + 2, "...%s" % entered, th.MUTED)
        # the prefix being typed, with a cursor so an empty prefix still
        # shows where input goes; caption face -- the candidates and the
        # press preview are the big feedback, this is the running state
        canvas.text(MARGIN, HEADER_H + 14, "%s_" % self.prefix, th.ACCENT,
                    font=fonts.S)

        cands = self._candidates()
        if self.prefix and not cands:
            canvas.text(MARGIN, self.CAND_TOP + 6,
                        "no match: backspace to fix", th.WARN)
        elif not self.prefix:
            canvas.text(MARGIN, self.CAND_TOP + 6,
                        "tap letters to spell a word", th.MUTED)
        else:
            for b in self._candidate_buttons():
                b.draw(canvas, accent=True)

        for b in self.buttons:
            b.draw(canvas)


class BackupResultScreen(Screen):
    # Menu-grade hold: these are amber commit buttons, and an amber button
    # that fires on a plain tap teaches the wrong reflex for the roll grid
    # (see EntropyCapturedScreen in flow_generate).
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, success, target_mnemonic):
        self.success = success
        self.target_mnemonic = target_mnemonic
        self.word_count = len(target_mnemonic.split())
        if success:
            self.buttons = [TapButton(th.WIDTH - 130 - MARGIN, th.HEIGHT - 48, 130, 40,
                                       "Continue", self._continue)]
        else:
            self.buttons = harmonise_scale([
                TapButton(MARGIN, th.HEIGHT - 48, 130, 40, "Try Again", self._retry),
                TapButton(th.WIDTH - 84 - MARGIN, th.HEIGHT - 48, 84, 40, "Skip", self._continue),
            ])

    def _continue(self, app):
        app.pop()

    def _retry(self, app):
        app.pop()
        app.push(WordEntryScreen(self.word_count, target_mnemonic=self.target_mnemonic))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        if self.success:
            self.header(canvas, "Backup Confirmed")
            canvas.text((th.WIDTH - 5 * fonts.M.width) // 2, HEADER_H + 24,
                        "MATCH", th.GOOD, font=fonts.M)
            y = HEADER_H + 68
            for line in th.wrap_text(
                "Every word matches the seed your rolls decided. Your written backup is correct.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width,
            ):
                canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
                y += 20
        else:
            self.header(canvas, "Backup MISMATCH")
            canvas.text((th.WIDTH - 8 * fonts.M.width) // 2, HEADER_H + 24,
                        "MISMATCH", th.WARN, font=fonts.M)
            y = HEADER_H + 68
            for line in th.wrap_text(
                "What you re-entered does not match the seed your rolls decided. "
                "Check your written backup for a transcription error.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width,
            ):
                canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
                y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)


class InvalidSeedScreen(Screen):
    title = "Invalid Seed"

    # Menu-grade hold, same rationale as BackupResultScreen -- and "Start
    # Over" here discards a 12-or-24-word entry, which deserves a deliberate
    # hold more than most.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, word_count, words=None):
        self.word_count = word_count
        # What was typed, so a retry does not start from nothing. Dropping
        # this is not a cosmetic simplification: without it "Try Again" means
        # retyping all twelve or twenty-four words because of one typo, on a
        # resistive touchscreen, which is how a user ends up transcribing from
        # a hurried second copy of the phrase.
        self.words = list(words or [])
        # Plain Screen, not MenuScreen: a menu spreads its buttons over the
        # whole height, straight through the explanation this screen exists
        # to show. Actions sit at the bottom, message above them.
        #
        # Try Again is full-width and first because it is what the user almost
        # always wants; Start Over and Cancel share the row below, since
        # discarding work should take a more deliberate tap than resuming it.
        self.buttons = harmonise_scale([
            TapButton(MARGIN, th.HEIGHT - 92, th.WIDTH - 2 * MARGIN, 40,
                      "Try Again", lambda app: self._retry(app)),
            TapButton(MARGIN, th.HEIGHT - 48, 118, 40, "Start Over",
                      lambda app: self._restart(app)),
            TapButton(th.WIDTH - 90 - MARGIN, th.HEIGHT - 48, 90, 40,
                      "Cancel", lambda app: app.reset_to_home()),
        ])

    def _retry(self, app):
        """Resume entry with everything except the last word.

        A checksum failure gives no clue which word is wrong, so the cheapest
        useful guess is that the most recent one is at fault and everything
        before it is kept. Backspace walks further back from there.
        """
        # pop InvalidSeedScreen itself before pushing a fresh WordEntryScreen,
        # otherwise repeated failed retries pile up on the stack forever and
        # "Back" from a later screen lands on a stale "Invalid Seed" frame
        app.pop()
        app.push(WordEntryScreen(self.word_count, words=self.words[:-1]))

    def _restart(self, app):
        app.pop()
        app.push(WordEntryScreen(self.word_count))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, self.title)
        y = HEADER_H + 12
        for line in th.wrap_text(
            "Checksum did not verify. This is not a valid BIP39 seed -- "
            "check for a typo or transcription error.",
            (th.WIDTH - 2 * MARGIN) // fonts.S.width,
        ):
            canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)
