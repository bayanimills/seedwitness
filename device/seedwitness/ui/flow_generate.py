"""Roll-a-Seed flow: source and length selection, the roll grid, reveal,
checksum word, ceremony completion and SeedQR export.

Load-on-demand: nothing imports this module at boot. It is reached only
through screens.flow_screen() at the navigation point, and unloaded (device
only) when the session ends -- see screens.py's docstring for the residency
rules and why that split exists. This module may import the entropy and
mnemonic machinery because a ceremony needs it; it must NOT import derive or
the embit EC chain, which belong to the address flow.
"""
from .. import entropy as ent
from .. import mnemonic as mn
from . import fonts
from . import theme as th
from .screens import (
    BUTTON_GAP,
    DEMO_BTN_W,
    HEADER_H,
    MARGIN,
    MenuScreen,
    Screen,
    TapButton,
    _demo_btn,
    _sleep_ms,
    draw_qr_symbol,
    flow_screen,
    harmonise_scale,
)


class MethodSelectScreen(MenuScreen):
    # "Choose Source", not "Entropy Source": with the Back corner reserved
    # the header band fits 13 characters of the 12x24 face, and dropping to
    # the caption face for one screen would break the header rhythm
    title = "Choose Source"

    def __init__(self):
        super().__init__([
            ("Dice: D6", lambda app: app.push(WordCountScreen(ent.D6))),
            ("Dice: D8", lambda app: app.push(WordCountScreen(ent.D8))),
            ("Dice: D12", lambda app: app.push(WordCountScreen(ent.D12))),
            ("Coin Flip", lambda app: app.push(WordCountScreen(ent.COIN))),
        ])
        self.buttons = list(self.buttons) + [self.back_button(None)]


class WordCountScreen(MenuScreen):
    title = "Seed Length"

    def __init__(self, source):
        self.source = source
        super().__init__([
            ("12 words", lambda app: self._start(app, 12)),
            ("24 words", lambda app: self._start(app, 24)),
        ])
        self.buttons = list(self.buttons) + [self.back_button(None)]

    def _start(self, app, word_count):
        app.session = ent.RollSession(self.source, word_count)
        app.push(RollEntryScreen(app.session))


class RollEntryScreen(Screen):
    """Tap the number you actually rolled -- no cursor to move, no separate
    confirm step. This is the one screen where touch is a strict UX upgrade
    over the Pi build's joystick up/down-then-select."""

    GRID_TOP = 84          # below the header, count/progress row, instruction
    OUTPUT_LINES = 4       # a full sha256 is 64 hex chars; 4 lines always fits

    def __init__(self, session):
        self.session = session
        self.buttons = self._build_grid()

    @classmethod
    def _output_top(cls):
        """Top of the output block. Shared by the grid and draw() so the two
        cannot disagree about where the roll buttons are allowed to end."""
        return th.HEIGHT - (14 + cls.OUTPUT_LINES * 12 + 4)

    def _build_grid(self):
        """Roll buttons fill everything between the instruction and the output.

        This is the most-tapped control in the app -- one press per roll, 50
        rolls for a 12-word seed -- so the targets are made as large as the
        screen allows rather than a fixed 34px. Columns are chosen to keep the
        buttons close to square at each die size.
        """
        source = self.session.source
        sides = source.sides
        cols = 3 if sides > 8 else 2
        rows = -(-sides // cols)  # ceil
        btn_w = (th.WIDTH - 2 * MARGIN - (cols - 1) * BUTTON_GAP) // cols
        avail_h = self._output_top() - 8 - self.GRID_TOP
        btn_h = (avail_h - (rows - 1) * BUTTON_GAP) // rows
        buttons = []
        for i in range(sides):
            value = i + 1
            col = i % cols
            row = i // cols
            x = MARGIN + col * (btn_w + BUTTON_GAP)
            y = self.GRID_TOP + row * (btn_h + BUTTON_GAP)
            # no explicit scale: a single digit in a large button sizes itself
            # right up, and the denser D12 grid settles smaller on its own
            # A D6 shows its face, not its number. The point of the ceremony
            # is that the user copies what is physically in front of them, and
            # matching a pip pattern to a pip pattern is a recognition task
            # where reading a digit and picking the matching digit is a
            # translation one. Fewer translations, fewer wrong rolls -- and a
            # wrong roll here silently corrupts the entropy and cannot be
            # detected afterwards.
            #
            # Only the D6. A D12 or D20 has numerals on its faces, so digits
            # ARE what the user is looking at, and pips would be the
            # translation instead.
            buttons.append(
                TapButton(x, y, btn_w, btn_h, source.face_label(value),
                          self._make_roll_handler(value),
                          pips=value if source.use_pips else None)
            )
        # "1" and "12" would otherwise size differently in identical buttons
        return harmonise_scale(buttons)

    def _make_roll_handler(self, value):
        def handler(app):
            self.session.add_roll(value)
            if self.session.is_seedsigner_compatible:
                app.push(SeedSignerRollGateScreen(self.session))
                return
            if self.session.is_complete:
                self._finish_or_warn(app, self.session.final_entropy())
        return handler

    def _finish_or_warn(self, app, entropy):
        concern = self.session.assess()
        if concern is not None:
            app.push(EntropyWarningScreen(self.session, entropy, concern))
            return
        self._finish(app, entropy)

    def _finish(self, app, entropy):
        mnemonic = mn.entropy_to_mnemonic(entropy)
        app.last_mnemonic = mnemonic
        app.pop()
        app.push(EntropyCapturedScreen(self.session, entropy, mnemonic))

    # A wrong roll silently corrupts the entropy -- nothing downstream can
    # detect it, the seed is simply not the one the dice produced -- so this
    # dwell is deliberately longer than a menu's, giving a brush or a mis-hit
    # time to be withdrawn. The sweep itself lives on Screen.
    SWEEP_MS = 420
    SWEEP_STEPS = 14

    # The demo [!]: immediately left of the Back corner, and ONLY while the
    # ceremony is untouched. Once a single real roll is in, it disappears --
    # synthetic rolls must never be injectable into a genuine ceremony
    # halfway through, where they would silently dilute real entropy.
    def _demo_button(self):
        return _demo_btn(self._fill_demo)

    def _fill_demo(self, app):
        """Populate the ceremony with the fixed practice pattern (1,2,3,...
        cycling through the die's faces) and continue through the NORMAL
        completion path. The pattern is published here in source: same rolls,
        same words, for everyone, every time -- recognisably nobody's secret,
        while still exercising the real rolls-to-words pipeline the demo
        exists to show."""
        s = self.session
        while not s.is_complete:
            s.add_roll(len(s.rolls) % s.source.sides + 1)
        entropy = s.final_entropy()
        mnemonic = mn.entropy_to_mnemonic(entropy)
        app.demo = True
        app.last_mnemonic = mnemonic
        app.pop()      # the confirmation gate
        app.pop()      # this roll screen
        app.push(EntropyCapturedScreen(s, entropy, mnemonic))

    def handle_tap(self, app, canvas, x, y):
        if not self.session.rolls:
            d = self._demo_button()
            if d.contains(x, y):
                # Through the same hold-to-confirm ritual as every roll
                # button on this screen. This used to fire on a plain tap,
                # which taught exactly the wrong reflex two inches from a
                # grid where a tap that commits early silently corrupts
                # entropy: every control here holds, or the hold means
                # nothing.
                self._dispatch(app, canvas, d)
                return
        back = self.back_button(app)
        if back.contains(x, y):
            back.on_tap(app)
            return
        super().handle_tap(app, canvas, x, y)

    def _header_right_limit(self, canvas):
        # this screen's Back is drawn directly rather than kept in
        # self.buttons, so reserve its corner explicitly -- plus the demo
        # [!] while it is showing
        limit = th.WIDTH - self.BACK_INSET - self.BACK_W - 4
        if not self.session.rolls:
            limit -= DEMO_BTN_W + 4
        return limit

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Rolling %s" % self.session.source.name)
        self.back_button(app).draw(canvas)
        if not self.session.rolls:
            self._demo_button().draw(canvas)

        # The count is the one number the user tracks across a 50-roll
        # ceremony, so it gets the primary face; the slim bar beside it makes
        # the same progress readable at a glance without reading anything.
        n, target = len(self.session.rolls), self.session.target_rolls
        count = "%d/%d" % (n, target)
        canvas.text(MARGIN, HEADER_H + 6, count, th.FG, font=fonts.M)
        bar_x = MARGIN + (len("00/00") + 1) * fonts.M.width  # fixed, no jitter
        bar_w = th.WIDTH - MARGIN - bar_x
        bar_y = HEADER_H + 6 + (fonts.M.height - 6) // 2  # centred on the count
        canvas.rect(bar_x, bar_y, bar_w, 6, th.BUTTON_BORDER)
        fill_w = int((bar_w - 2) * min(n / target, 1.0))
        if fill_w > 0:
            canvas.fill_rect(bar_x + 1, bar_y + 1, fill_w, 4, th.ENTROPY_BAR)

        canvas.text(MARGIN, HEADER_H + 36, "tap the value you rolled:", th.MUTED)
        for b in self.buttons:
            b.draw(canvas)

        # Avalanche-effect live output -- NOT the final seed, purely to show
        # that every single roll completely reshuffles the eventual key. That
        # point only lands if enough of the digest is visible to see it change
        # wholesale, so the full 64-char sha256 is wrapped across lines rather
        # than truncated to fit one.
        top = self._output_top()
        canvas.hline(0, top - 6, th.WIDTH, th.BUTTON_BORDER)
        canvas.text(MARGIN, top, "output:", th.MUTED)
        per_line = (th.WIDTH - 2 * MARGIN) // th.CHAR_W
        # TRUNCATED, and this is a security fix, not a layout one.
        #
        # Showing the full 64-hex sha256 of the rolls so far made this panel a
        # brute-forceable commitment to the roll prefix. One photograph taken
        # at the ceremony midpoint lets an attacker invert the prefix and
        # search only the tail: measured at roughly 2^66 total work against a
        # nominally 128-bit D6 12-word seed, and the coin ceremony is worse
        # because it runs to 128 flips over an hour with this digest lit
        # between every one of them. The screen was inviting the user to stare
        # at (and photograph) precisely the value that breaks the ceremony.
        #
        # 8 hex chars caps the leak at 32 bits, leaving ~97 bits residual, and
        # loses nothing pedagogically: watching 32 bits reshuffle completely on
        # every roll makes the avalanche point just as well as watching 256.
        # A nonce would be the textbook fix and is not available here, because
        # there is no trustworthy RNG on this device by design.
        PREVIEW_HEX = 8
        digest = (self.session.live_preview_hash()[:PREVIEW_HEX]
                  if self.session.rolls else "")
        if not digest:
            digest = "-" * (per_line * self.OUTPUT_LINES)
        y = top + 14
        for i in range(0, min(len(digest), per_line * self.OUTPUT_LINES), per_line):
            canvas.text(MARGIN, y, digest[i:i + per_line], th.MUTED)
            y += 12


class SeedSignerRollGateScreen(Screen):
    """At roll 99, choose interoperability or one more roll.

    The choice happens before entropy is finalised. Adding the 100th roll
    changes the SHA256 input and therefore the entire mnemonic; the two paths
    are alternatives, never two labels for the same seed.
    """

    # These are normal menu-grade hold controls, not the zero-dwell Screen
    # default. A brush at the exact decision boundary must not choose which
    # seed the ceremony produces.
    # Alias the shared menu dwell rather than copying its current numbers:
    # these are standard long-press choices and must continue to behave like
    # them if the menu interaction is tuned after panel testing.
    SWEEP_MS = MenuScreen.SWEEP_MS
    SWEEP_STEPS = MenuScreen.SWEEP_STEPS

    def __init__(self, session):
        self.session = session
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN, 40,
                      "Use 99: SeedSigner", self._choose_99),
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Add 100th Roll", self._choose_100th),
        ]

    def _choose_99(self, app):
        app.push(RollCountConfirmScreen(self.session, use_99=True))

    def _choose_100th(self, app):
        app.push(RollCountConfirmScreen(self.session, use_99=False))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "99 Rolls")
        y = HEADER_H + 12
        lines = (
            ("SeedSigner stops here.", th.FG),
            ("Use 99 to reproduce this", th.MUTED),
            ("seed on a SeedSigner.", th.MUTED),
            ("", th.MUTED),
            ("Or add one roll to meet", th.MUTED),
            ("the full 256-bit input rule.", th.MUTED),
            ("Roll 100 makes a new seed.", th.WARN),
        )
        for line, color in lines:
            if line:
                canvas.text(MARGIN, y, line, color, font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)


class RollCountConfirmScreen(Screen):
    """Second, explicit confirmation of the roll-99 fork."""

    SWEEP_MS = MenuScreen.SWEEP_MS
    SWEEP_STEPS = MenuScreen.SWEEP_STEPS

    def __init__(self, session, use_99):
        self.session = session
        self.use_99 = use_99
        label = "Confirm 99 Rolls" if use_99 else "Continue to Roll 100"
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN, 40,
                      label, self._confirm),
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Back", lambda app: app.pop()),
        ]

    def _confirm(self, app):
        if self.use_99:
            entropy = self.session.seedsigner_entropy()
            app.pop()  # this confirmation
            app.pop()  # the roll-count gate
            # The roll screen owns the common warning/finalisation path. It is
            # active again after the two choice screens are removed.
            app.screen._finish_or_warn(app, entropy)
            return
        app.pop()  # this confirmation
        app.pop()  # the gate; roll entry is now active for roll 100

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Confirm Choice")
        y = HEADER_H + 16
        if self.use_99:
            lines = (
                ("Finalise with 99 rolls?", th.FG),
                ("This seed can be reproduced", th.MUTED),
                ("on a SeedSigner.", th.MUTED),
                ("", th.MUTED),
                ("You cannot add roll 100", th.WARN),
                ("after revealing the words.", th.WARN),
            )
        else:
            lines = (
                ("Return for roll 100?", th.FG),
                ("That roll changes the hash", th.MUTED),
                ("and makes a different seed.", th.MUTED),
                ("", th.MUTED),
                ("It will not reproduce on", th.WARN),
                ("SeedSigner's 99-roll flow.", th.WARN),
            )
        for line, color in lines:
            if line:
                canvas.text(MARGIN, y, line, color, font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)


class EntropyWarningScreen(Screen):
    """Advisory gate for visibly patterned physical input.

    A statistical warning is not proof that the dice were bad, so continuing
    remains possible. It is still an explicit hold decision: silently turning
    an obviously hand-entered pattern into plausible-looking words is the
    failure this screen exists to interrupt.
    """

    SWEEP_MS = MenuScreen.SWEEP_MS
    SWEEP_STEPS = MenuScreen.SWEEP_STEPS

    def __init__(self, session, entropy, concern):
        self.session = session
        self.entropy = entropy
        self.concern = concern
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN, 40,
                      "Use These Rolls", self._continue),
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Start Over", self._restart),
        ]

    def _continue(self, app):
        app.pop()  # warning; roll entry is active again
        app.screen._finish(app, self.entropy)

    def _restart(self, app):
        # Reuse the same RollSession and RollEntryScreen so no flow import or
        # allocation burst is needed at the point the user is discarding
        # secret input. Clearing in place also keeps app.session consistent.
        self.session.rolls[:] = []
        app.last_mnemonic = None
        app.pop()

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Check Your Rolls")
        y = HEADER_H + 12
        canvas.text(MARGIN, y, "Pattern noticed", th.WARN, font=fonts.M)
        y += 34
        budget = max(1, (th.WIDTH - 2 * MARGIN) // fonts.S.width)
        for line in th.wrap_text(self.concern.message, budget):
            canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
            y += 20
        y += 4
        for line in th.wrap_text(
                "This does not prove bad dice. Check how you rolled before continuing.",
                budget):
            canvas.text(MARGIN, y, line, th.MUTED, font=fonts.S)
            y += 18
        for b in self.buttons:
            b.draw(canvas, accent=True)


class EntropyCapturedScreen(Screen):
    # A brief menu-grade hold. This and the two screens after it were the
    # only amber commit buttons in the app that fired on a plain tap, which
    # taught that some amber buttons commit instantly -- the wrong lesson on
    # a device where the roll grid's hold is load-bearing. Menu dwell, not
    # the roll grid's: a mis-hit here is a wrong turn, not corrupted entropy.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, session, entropy, mnemonic):
        self.session = session
        self.entropy = entropy
        self.mnemonic = mnemonic
        self.buttons = [TapButton(th.WIDTH - 160 - MARGIN, th.HEIGHT - 44, 160, 34,
                                   "Reveal Words", self._reveal)]

    def _reveal(self, app):
        app.pop()
        app.push(WordListScreen(self.mnemonic, page=0))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Entropy Captured")
        y = HEADER_H + 12
        for line in (
            "Source: %s" % self.session.source.name,
            "Rolls used: %d" % len(self.session.rolls),
            "Entropy: %d bits" % (len(self.entropy) * 8),
        ):
            canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
            y += 20
        y += 8
        # "truncated" stays in the label: a 12-word seed keeps only the first
        # 128 of sha256's 256 bits, and this screen must not imply otherwise
        canvas.text(MARGIN, y, "sha256(rolls), truncated:", th.MUTED)
        y += 14
        # 16 hex chars per line. The old layout asked for 32-char lines,
        # which is 256px at the 8px font on a 240px screen -- both canvases
        # clamp, so the entropy display was silently missing characters.
        # Grouped 4x4 it fits with room and is checkable against a notebook.
        digest = self.entropy.hex()
        for i in range(0, len(digest), 16):
            chunk = digest[i:i + 16]
            grouped = " ".join(chunk[j:j + 4] for j in range(0, len(chunk), 4))
            canvas.text(MARGIN, y, grouped, th.MUTED)
            y += 12
        for b in self.buttons:
            b.draw(canvas, accent=True)


class WordListScreen(Screen):
    WORDS_PER_PAGE = 6

    # Same menu-grade hold as EntropyCapturedScreen, same reason.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, mnemonic, page):
        self.words = mnemonic.split()
        self.mnemonic = mnemonic
        self.page = page
        self.display_words = self.words[:-1]  # final word gets its own screen
        self.total_pages = max(1, -(-len(self.display_words) // self.WORDS_PER_PAGE))
        self.buttons = self._build_buttons()

    def _build_buttons(self):
        buttons = []
        if self.page > 0:
            buttons.append(TapButton(MARGIN, th.HEIGHT - 48, 96, 40, "< Prev", self._prev))
        next_label = "Next >" if self.page < self.total_pages - 1 else "Continue"
        buttons.append(TapButton(th.WIDTH - 112 - MARGIN, th.HEIGHT - 48, 112, 40,
                                  next_label, self._next))
        return buttons

    def _prev(self, app):
        app.pop()
        app.push(WordListScreen(self.mnemonic, self.page - 1))

    def _next(self, app):
        app.pop()
        if self.page < self.total_pages - 1:
            app.push(WordListScreen(self.mnemonic, self.page + 1))
        else:
            app.push(ChecksumWordScreen(self.mnemonic))

    # Row geometry: six 16x32 words at a 36px pitch fill the space between
    # the header and the nav row with a 4px visual gap that Spleen's own
    # in-cell leading stretches to ~14px of white. The number gutter is
    # fixed-width so every word starts on the same column regardless of
    # whether its index is one digit or two.
    ROW_TOP = HEADER_H + 12
    ROW_PITCH = 36
    # 40, not 52: the index gutter only needs room for two 8px digits plus a
    # little air, and the old value left 16px of dead space against the left
    # margin while pushing every word toward the middle of the panel. Words
    # are what this screen is for, so they start as far left as the gutter
    # allows.
    WORD_X = 40

    # Every BIP39 word is uniquely determined by its first four letters -- the
    # wordlist is built that way, which is why hardware wallets accept a
    # four-letter prefix and why this device's own word entry resolves on four
    # characters. Splitting the word there makes the part that actually
    # matters visible: a user copying "abandon" only has to get "aban" right,
    # and one checking a written backup only has to compare four letters per
    # word rather than up to eight.
    #
    # A gap rather than a colour or weight change: this face has one weight,
    # and colouring the prefix would collide with the address screen's
    # striping, where alternating colour already means something else.
    PREFIX_LEN = 4
    PREFIX_GAP = 6

    def _draw_word(self, canvas, x, y, word):
        canvas.text(x, y, word[:self.PREFIX_LEN], th.FG, font=fonts.L)
        rest = word[self.PREFIX_LEN:]
        if rest:
            canvas.text(x + self.PREFIX_LEN * fonts.L.width + self.PREFIX_GAP,
                        y, rest, th.FG, font=fonts.L)

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Words (page %d/%d)" % (self.page + 1, self.total_pages))
        start = self.page * self.WORDS_PER_PAGE
        # Seed words are the whole point of this screen: they get copied down
        # by hand onto the backup, and an error here is permanent. They render
        # in the largest face on the device (16x32 -- the hero subset covers
        # all of BIP39's lowercase), with the index small and muted beside
        # them so the eye reads "word", not "row of a table".
        for i in range(start, min(start + self.WORDS_PER_PAGE, len(self.display_words))):
            y = self.ROW_TOP + (i - start) * self.ROW_PITCH
            num = "%2d" % (i + 1)
            canvas.text(self.WORD_X - 12 - len(num) * fonts.S.width, y + 8,
                        num, th.MUTED, font=fonts.S)
            self._draw_word(canvas, self.WORD_X, y, self.display_words[i])
        if self.page == self.total_pages - 1:
            # How the words are typed back in, said once, with the words.
            # A BIP39 phrase entered as a single line is lowercase words
            # with single spaces between them -- the same silent gap the
            # passphrase screen closes (see PassphraseWordsScreen). On the
            # last page only: both supported lengths leave it 5 rows (11
            # and 23 display words at 6 per page), so the note has room;
            # the guard keeps a future full final page from pushing it
            # into the nav row.
            shown = len(self.display_words) - self.page * self.WORDS_PER_PAGE
            y = self.ROW_TOP + shown * self.ROW_PITCH + 2
            if y + 19 <= th.HEIGHT - 52:
                canvas.text(MARGIN, y, "typed back: all lowercase,", th.MUTED)
                canvas.text(MARGIN, y + 11, "single spaces between words",
                            th.MUTED)
        for b in self.buttons:
            b.draw(canvas, accent=True)


class ChecksumWordScreen(Screen):
    # Same menu-grade hold as EntropyCapturedScreen, same reason.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, mnemonic):
        self.mnemonic = mnemonic
        # single source of truth for this bit-math -- mnemonic.py's
        # checksum_breakdown(), not a local reimplementation that could
        # silently drift from it
        b = mn.checksum_breakdown(mnemonic)
        self.leftover = b.leftover_entropy_bits
        self.checksum_bits = b.checksum_bits
        self.word_index = b.word_index
        self.word = b.word
        self.buttons = [TapButton(th.WIDTH - 130 - MARGIN, th.HEIGHT - 48, 130, 40,
                                   "Continue", self._continue)]

    def _continue(self, app):
        app.pop()
        app.push(GenerateCompleteScreen(self.mnemonic))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Word #%d (final)" % self.word_index)
        # the checksum word gets written on the backup like the others, so it
        # renders in the same hero face as the word list, centred
        wx = (th.WIDTH - len(self.word) * fonts.L.width) // 2
        canvas.text(wx, HEADER_H + 14, self.word, th.ACCENT, font=fonts.L)

        y = HEADER_H + 58
        for line in th.wrap_text(
            "This word isn't purely random -- it blends leftover entropy with a checksum:",
            (th.WIDTH - 2 * MARGIN) // th.CHAR_W,
        ):
            canvas.text(MARGIN, y, line, th.FG)
            y += 14

        bar_y = y + 8
        bar_w = th.WIDTH - 2 * MARGIN
        ent_w = int(bar_w * self.leftover / 11)
        canvas.fill_rect(MARGIN, bar_y, ent_w, 20, th.ENTROPY_BAR)
        canvas.fill_rect(MARGIN + ent_w, bar_y, bar_w - ent_w, 20, th.CHECKSUM_BAR)
        # bg must match the bar each label sits on: text is never transparent
        # here, so without it these would be black boxes punched into the bars
        # -- and with th.BG as the text colour, black on black, invisible.
        canvas.text(MARGIN + 2, bar_y + 6, "%d ent" % self.leftover, th.BG,
                    bg=th.ENTROPY_BAR)
        cs_label = "%d checksum" % self.checksum_bits
        canvas.text(MARGIN + bar_w - len(cs_label) * th.CHAR_W - 2, bar_y + 6,
                    cs_label, th.BG, bg=th.CHECKSUM_BAR)

        y = bar_y + 28
        for line in th.wrap_text(
            "Checksum = first bits of SHA256(entropy). Catches about 15 of every 16 single-word errors.",
            (th.WIDTH - 2 * MARGIN) // th.CHAR_W,
        ):
            canvas.text(MARGIN, y, line, th.MUTED)
            y += 14

        for b in self.buttons:
            b.draw(canvas, accent=True)


class GenerateCompleteScreen(MenuScreen):
    title = "Ceremony Complete"

    def __init__(self, mnemonic):
        self.mnemonic = mnemonic
        word_count = len(mnemonic.split())
        # "Verify Address" not "Verify this seed now": the shorter label is
        # what lets the whole menu hold the 12x24 face (harmonise_scale drags
        # every button down to the longest label's face), and it names what
        # actually happens next -- deriving an address to compare.
        #
        # Confirm Backup and Verify Address live in other flow modules
        # (verify and address), loaded at the moment of the tap: see
        # screens.flow_screen for why the reference is late-bound and what
        # happens when the module is missing (a loud ImportError into the
        # crash screen, never a silent blank).
        super().__init__([
            ("Confirm Backup",
             lambda app: app.push(flow_screen("verify", "WordEntryScreen")(
                 word_count, target_mnemonic=self.mnemonic))),
            ("Verify Address",
             lambda app: app.push(flow_screen("address", "DerivationPathScreen")(
                 self.mnemonic))),
            # SeedQR export (DEVPLAN 4.1). Offered here, after the words have
            # been shown, because the QR is a machine-readable copy of the
            # same secret -- it must never be an alternative to writing the
            # words down, only an addition. The screen it opens is a hard
            # gate, not the QR itself.
            ("Export QR", lambda app: app.push(QRExportScreen(self.mnemonic))),
            ("Done", lambda app: app.reset_to_home()),
        ])


class QRExportScreen(Screen):
    """Warning gate and hold-to-reveal for SeedQR export (DEVPLAN 4.1).

    A seed QR on screen is a machine-readable broadcast of the entire
    wallet: any camera in the room captures it perfectly in a fraction of a
    second, out of focus, from an angle. Words at least require a legible
    photograph and transcription. So the QR sits behind three gates, all
    three specified in DEVPLAN 4.1 and all three load-bearing:

      * this explicit warning screen first;
      * hold-to-reveal, reusing the app's one hold gesture
        (Screen._hold_to_confirm) with the longest dwell in the app --
        revealing is the single most dangerous action this device offers;
      * a short auto-blank timeout, after which the QR stops displaying.

    The QR is never pushed onto the navigation stack. It is drawn directly
    while the hold-gated reveal executes (the DerivingScreen idiom) and
    blanked before handle_tap returns, so no Back path, redraw, or crash
    frame can ever land on a lit seed QR: the only way to see it is to hold
    the button again, through the warning, every time.

    Two formats. CompactSeedQR (the raw entropy bytes, binary mode) is
    primary: densest symbol, and it is exactly what SeedSigner expects.
    Standard SeedQR (word indices as 4-digit decimals) is offered because
    some readers and paper workflows only speak it.

    Import is impossible -- this board has no camera -- and the screen says
    so, because "QR" on a seed device otherwise implies a scan-in path that
    does not exist here.
    """

    # Longest hold in the app, deliberately above the roll screen's 420ms:
    # a mis-held roll corrupts one ceremony, a mis-held reveal broadcasts
    # the finished wallet. test_qr_export_flow pins this ordering.
    SWEEP_MS = 900
    SWEEP_STEPS = 18

    # Auto-blank. Long enough to aim a SeedSigner camera (they lock on in
    # a second or two), far too short to walk away from.
    SHOW_MS = 20000
    STEP_MS = 200
    QUIET = 4      # quiet-zone modules each side: the spec minimum, and a
                   # QR drawn edge to edge often will not scan at all

    COMPACT_Y = 180
    STANDARD_Y = 246

    def __init__(self, mnemonic):
        self.mnemonic = mnemonic
        w = th.WIDTH - 2 * MARGIN
        # on_tap=None: handle_tap() intercepts these by identity, because the
        # reveal needs `canvas` (to draw the QR frame and run the blanking
        # dwell), which a plain on_tap(app) callback cannot reach.
        self.compact_btn = TapButton(MARGIN, self.COMPACT_Y, w, 58,
                                     "Compact QR", on_tap=None)
        self.standard_btn = TapButton(MARGIN, self.STANDARD_Y, w, 42,
                                      "Standard QR", on_tap=None)
        self.buttons = [self.compact_btn, self.standard_btn]

    def _header_right_limit(self, canvas):
        # Back is drawn directly rather than kept in self.buttons (it must
        # not inherit this screen's 900ms hold), so reserve its corner
        return th.WIDTH - self.BACK_INSET - self.BACK_W - 4

    def handle_tap(self, app, canvas, x, y):
        # Back stays a plain tap: leaving must always be easier than
        # revealing.
        back = self.back_button(app)
        if back.contains(x, y):
            back.on_tap(app)
            return
        for b in self.buttons:
            if b.contains(x, y):
                # The gate. A plain tap -- finger lifting before the sweep
                # closes -- must never reveal; test_qr_export_flow holds this
                # screen to that.
                if not self._hold_to_confirm(canvas, b):
                    b.draw(canvas)      # cancelled: wipe the dial
                    return
                b.draw(canvas, locked=True)
                canvas.present()
                if getattr(canvas, "hold_ms", self.LOCK_MS):
                    _sleep_ms(self.LOCK_MS)
                self._reveal(app, canvas, compact=(b is self.compact_btn))
                return

    def _reveal(self, app, canvas, compact):
        """Draw the QR, hold it for SHOW_MS or until a tap, then blank it.

        Runs entirely inside this tap, DerivingScreen-style: nothing is
        pushed, so nothing can navigate back to a lit QR. Imported lazily so
        the encoder's code and GF tables cost heap only once someone
        actually exports.
        """
        from .. import qr as qrm
        if compact:
            size, mat = qrm.compact_seedqr(mn.mnemonic_to_entropy(self.mnemonic))
        else:
            size, mat = qrm.standard_seedqr(mn.word_indices(self.mnemonic))
        self._draw_qr_frame(canvas, size, mat, compact)
        canvas.present()
        # The finger that held to reveal is usually still down; consume that
        # release so it cannot instantly count as tap-to-hide.
        canvas.wait_release()
        # The mock canvas advertises hold_ms=0, the signal it uses everywhere
        # for "draw everything, sleep never" -- tests and screenshots still
        # exercise the frame without sitting through 20 real seconds.
        total = self.SHOW_MS if getattr(canvas, "hold_ms", 1) else 0
        bar_w = th.WIDTH - 2 * MARGIN
        waited = 0
        while waited < total:
            _sleep_ms(self.STEP_MS)
            waited += self.STEP_MS
            if canvas.touch_active():
                break               # tap to hide early
            # countdown bar drains right-to-left toward the blank
            remaining = bar_w * (total - waited) // total
            canvas.fill_rect(MARGIN + remaining, self._bar_y,
                             bar_w - remaining, 6, th.BG)
            canvas.present()
        # Auto-blank: repaint the warning screen over the QR. This is the
        # timeout the DEVPLAN requires, not a courtesy redraw.
        self.draw(app, canvas)
        canvas.present()

    def _draw_qr_frame(self, canvas, size, mat, compact):
        """The revealed seed QR frame. The symbol itself is the shared
        draw_qr_symbol; everything sensitive about this screen (the warning,
        the hold, the auto-blank) lives in draw()/_reveal, not here."""
        canvas.fill(th.BG)
        self.header(canvas, "Compact SeedQR" if compact else "SeedQR")
        words = len(self.mnemonic.split())
        what = "raw entropy" if compact else "word indices"
        canvas.text(MARGIN, HEADER_H + 6, "%d words as %s" % (words, what),
                    th.MUTED)
        y0 = HEADER_H + 22
        _, side, _ = draw_qr_symbol(canvas, size, mat, y0, quiet=self.QUIET)
        # countdown bar, drained by _reveal as the blank approaches
        self._bar_y = y0 + side + 6
        canvas.fill_rect(MARGIN, self._bar_y, th.WIDTH - 2 * MARGIN, 6,
                         th.ACCENT)
        canvas.text(MARGIN, self._bar_y + 10, "tap to hide", th.MUTED)

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Export QR")
        self.back_button(app).draw(canvas)
        y = HEADER_H + 8
        for line in th.wrap_text(
            "Any camera in the room can steal this seed in an instant, "
            "even blurred, at an angle. Check nothing is watching the "
            "screen.",
            (th.WIDTH - 2 * MARGIN) // fonts.S.width,
        ):
            canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
            y += 20
        y += 4
        for line in th.wrap_text(
            "Hold a format to reveal. The code hides itself after %d "
            "seconds." % (self.SHOW_MS // 1000),
            (th.WIDTH - 2 * MARGIN) // th.CHAR_W,
        ):
            canvas.text(MARGIN, y, line, th.MUTED)
            y += 11
        self.compact_btn.draw(canvas, accent=True)
        self.standard_btn.draw(canvas)
        # No scan-in path exists: the board has no camera. Said here because
        # a QR feature on a seed device otherwise implies one.
        canvas.text(MARGIN, 296, "no camera on this device:", th.MUTED)
        canvas.text(MARGIN, 307, "export only, words go in", th.MUTED)
