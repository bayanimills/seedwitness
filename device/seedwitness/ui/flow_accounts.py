"""Accounts flow: the enrolled watch-only account list and everything done
to one account -- open for verification, rename, xpub QR export, delete --
plus the nickname keyboard (also used by enrolment in the address flow).

Load-on-demand: reached only through screens.flow_screen() at the navigation
point and unloaded (device only) when the session ends -- see screens.py's
docstring. Imports derive for xpub parsing/fingerprints -- cheap on its own;
the embit EC chain only loads (inside derive's functions) once an account is
actually opened or its QR exported, not for merely listing what is enrolled.
"""
from .. import accounts as acct
from .. import derive as drv
from . import fonts
from . import theme as th
from .screens import (
    BUTTON_GAP,
    HEADER_H,
    MARGIN,
    Screen,
    TapButton,
    _auto_label,
    draw_qr_symbol,
    flow_screen,
    harmonise_scale,
)


class NicknameScreen(Screen):
    """On-screen keyboard for an account nickname.

    Same pattern as WordEntryScreen -- a grid of hold-to-confirm keys with a
    magnified press preview -- rather than a second text-input invention.
    Differences are the payload's, not the pattern's:

      * charset is a-z plus 0-9, exactly 36 keys, a clean 6x6 grid. That set
        is chosen from the FONTS, not from taste: the 16x32 hero face covers
        only " +-0-9a-z" (ui/fonts), so any character allowed here renders
        everywhere a nickname could ever appear, including at hero size.
      * length is capped at MAX_LEN so the name always fits its list row at
        the caption face (see AccountRowButton) -- a nickname that truncates
        differently on different screens is worse than a short one.

    Save calls on_save(app, text) after popping; empty text is passed
    through, and the caller decides what empty means (fall back to the auto
    label, keep the old name).
    """

    CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
    MAX_LEN = 14
    COLS = 6
    GRID_TOP = HEADER_H + 64
    ACTION_H = 34
    PREVIEW_H = 32

    # Same brief hold as the word-entry keys and for the same reason: a 40px
    # key is narrower than the fingertip pressing it. This is an accuracy
    # aid, not a security gate -- nothing here is sensitive.
    SWEEP_MS = 120
    SWEEP_STEPS = 5

    def __init__(self, initial, on_save):
        self.value = initial[:self.MAX_LEN]
        self.on_save = on_save
        self.buttons = self._build_keys()

    def _build_keys(self):
        buttons = []
        key_w = th.WIDTH // self.COLS
        rows = -(-len(self.CHARS) // self.COLS)  # ceil; 6 for the 36-char set
        key_h = (th.HEIGHT - self.ACTION_H - 2 - self.GRID_TOP) // rows
        self.KEY_W = key_w
        self.KEY_H = key_h
        x_off = (th.WIDTH - self.COLS * key_w) // 2
        for i, ch in enumerate(self.CHARS):
            x = x_off + (i % self.COLS) * key_w
            y = self.GRID_TOP + (i // self.COLS) * key_h
            buttons.append(TapButton(x, y, key_w, key_h, ch, self._make_key(ch)))
        action_y = th.HEIGHT - self.ACTION_H
        third = th.WIDTH // 3
        buttons.append(TapButton(0, action_y, third, self.ACTION_H,
                                 "<- Del", self._backspace))
        buttons.append(TapButton(third, action_y, third, self.ACTION_H,
                                 "Save", self._save))
        buttons.append(TapButton(2 * third, action_y, th.WIDTH - 2 * third,
                                 self.ACTION_H, "Cancel", lambda app: app.pop()))
        return buttons

    def _make_key(self, ch):
        def handler(app):
            if len(self.value) < self.MAX_LEN:
                self.value += ch
        return handler

    def _backspace(self, app):
        self.value = self.value[:-1]

    def _save(self, app):
        app.pop()
        self.on_save(app, self.value)

    def _press_preview(self, canvas, b):
        """Magnified copy of the held key above the grid, same as the word
        keyboard: the fingertip covers the key that needs reading. The band
        sits over the hint line (transient, repainted on the next draw) and
        below the value line, which must stay readable while typing."""
        if len(b.label) != 1:
            return
        y = self.GRID_TOP - self.PREVIEW_H - 2
        canvas.fill_rect(0, y, th.WIDTH, self.PREVIEW_H, th.BG)
        canvas.rect(MARGIN, y, th.WIDTH - 2 * MARGIN, self.PREVIEW_H, th.ACCENT)
        canvas.text((th.WIDTH - fonts.M.width) // 2, y + 4, b.label, th.ACCENT,
                    bg=th.BG, font=fonts.M)
        canvas.present()

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Nickname")
        # the value being built, with a cursor so empty still shows where
        # input goes; primary face -- this is the thing being made
        canvas.text(MARGIN, HEADER_H + 4, "%s_" % self.value, th.ACCENT,
                    font=fonts.M)
        canvas.text(MARGIN, HEADER_H + 38, "a-z 0-9, up to %d" % self.MAX_LEN,
                    th.MUTED)
        for b in self.buttons:
            b.draw(canvas)


class DamagedAccountScreen(Screen):
    """An enrolled record that no longer names a derivation this build knows.

    Deliberately offers no "use anyway": there is no safe guess. Deriving with
    the wrong script type yields a valid-looking address that is not the
    user's, and on a device whose whole job is telling those apart, guessing
    would be the worst possible behaviour.
    """

    def __init__(self):
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Back", lambda app: app.pop()),
        ]

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Damaged Record")
        y = HEADER_H + 14
        for line in th.wrap_text(
                "This enrolled account is damaged and cannot be used. Delete "
                "it and enrol the account again. Nothing is wrong with your "
                "seed.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width):
            canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)


class AccountRowButton(TapButton):
    """A list row with two fields: the nickname in the caption face and the
    account fingerprint under it in the dense face, both left-aligned.

    A plain TapButton centres a single label, which cannot carry the
    fingerprint -- and the fingerprint has to be ON the row: it is shown once
    at enrolment, so without it here a user has no way to check that the
    account they are about to verify against is the one they enrolled.
    Existing primitives only; just a different layout inside the border.
    """

    def __init__(self, x, y, w, h, label, fp, on_tap):
        TapButton.__init__(self, x, y, w, h, label, on_tap, font=fonts.S)
        self.fp = fp

    def draw(self, canvas, accent=False, locked=False):
        border = th.ACCENT if accent else (self.edge or th.BUTTON_BORDER)
        canvas.fill_rect(self.x, self.y, self.w, self.h, th.BUTTON_BG)
        canvas.rect(self.x, self.y, self.w, self.h, border)
        color = th.ACCENT if accent else th.FG
        canvas.text(self.x + 6, self.y + 5, self.label, color,
                    bg=th.BUTTON_BG, font=fonts.S)
        canvas.text(self.x + 6, self.y + 26, self.fp, th.MUTED,
                    bg=th.BUTTON_BG)


class AccountListScreen(Screen):
    """Enrolled accounts. The fast, secretless way in.

    Each row is two targets: the row itself opens the account for
    verification (the primary job, unchanged), and the Edit corner opens
    management -- rename, xpub export, delete. Rows show the account
    fingerprint so what is enrolled can be checked against what was written
    down at enrolment.
    """

    ROW_H = 42
    ROW_PITCH = 50
    EDIT_W = 56

    def __init__(self):
        self.records = []
        self.buttons = [self.back_button(None)]

    def _rebuild(self, app):
        """Rebuilt on every draw, not once in __init__: delete and rename
        happen on screens stacked above this one, so by the time it is
        drawn again its rows may describe accounts that no longer exist."""
        self.records = acct.load()
        buttons = []
        y = HEADER_H + 40
        main_w = th.WIDTH - 2 * MARGIN - self.EDIT_W - BUTTON_GAP
        # The slice IS the store cap (accounts.MAX_ACCOUNTS), one constant on
        # purpose: a record this list cannot show cannot be opened, renamed
        # or deleted, and an undeletable xpub is a permanent privacy exposure
        # with the consent removed. A store over the cap (written by an older
        # build) still renders its first five rows rather than throwing; each
        # delete then surfaces the next hidden record.
        for rec in self.records[:acct.MAX_ACCOUNTS]:
            buttons.append(AccountRowButton(
                MARGIN, y, main_w, self.ROW_H,
                rec.get("label") or "account",
                drv.xpub_fingerprint(rec["xpub"]),
                (lambda r: lambda app: self._open(app, r))(rec)))
            buttons.append(TapButton(
                MARGIN + main_w + BUTTON_GAP, y, self.EDIT_W, self.ROW_H,
                "Edit",
                (lambda r: lambda app: app.push(AccountManageScreen(r)))(rec)))
            y += self.ROW_PITCH
        buttons.append(self.back_button(app))
        self.buttons = buttons

    def _open(self, app, rec):
        # Refuse, do not fall back. This used to default to PATH_TEMPLATES[2]
        # (BIP84) for any unrecognised "bip" value, while the header and path
        # were still built from the raw stored value. A corrupt or tampered
        # accounts.json therefore derived with one script type and displayed
        # another, producing an address that is not yours -- which is exactly
        # the failure this device exists to detect, arriving disguised as a
        # detection. accounts.json is plain unauthenticated JSON on flash, so
        # a damaged record is a thing that happens.
        template = None
        for t in drv.PATH_TEMPLATES:
            if t.bip == rec["bip"]:
                template = t
                break
        if template is None:
            app.push(DamagedAccountScreen())
            return
        # The xpub's CONTENT, not just its type. A well-formed string that is
        # not a valid extended key opened this screen happily and then raised
        # ValueError out of HDKey.from_base58 the moment the user asked to
        # derive, ending the session on the crash screen. This screen already
        # refuses an unknown bip and a missing account for the same reason:
        # a damaged record on unauthenticated flash is a thing that happens,
        # and DamagedAccountScreen exists to say so calmly.
        #
        # Checked here rather than in accounts.load() on purpose, and the cost
        # is bigger than "one hash": measured on the board, this import pulls
        # embit.base58 -> hashes -> util -> secp256k1 and costs 26,816 bytes,
        # because the ripemd160 shim falls through to the pure-Python EC
        # chain. That is affordable HERE (the same chain loads seconds later
        # at Derive anyway, and the xpub path pays no PBKDF2 stretch, so
        # nothing is competing for headroom) and would not be affordable in
        # load(), which runs on the home screen's path.
        #
        # The import sits OUTSIDE the try. A MemoryError or ImportError is the
        # device running out of room, not the record being damaged, and
        # reporting it as damage tells the user to delete a perfectly good
        # account to fix a transient. Only decode_check's ValueError means
        # "this data is bad".
        from embit import base58
        try:
            valid = len(base58.decode_check(rec["xpub"])) == 78
        except ValueError:
            valid = False
        if not valid:
            app.push(DamagedAccountScreen())
            return
        screen = flow_screen("address", "AddressIndexScreen")(
            None, template, xpub=rec["xpub"])
        if "account" not in rec:
            # Defaulting to 0 printed "m/84h/0h/0h" for an xpub that might be
            # any account. The path line is the user's only check that they
            # are looking at the right wallet, so inventing one is worse than
            # refusing.
            app.push(DamagedAccountScreen())
            return
        screen.account_path = "m/%dh/0h/%dh" % (rec["bip"], rec["account"])
        app.push(screen)

    def draw(self, app, canvas):
        self._rebuild(app)
        canvas.fill(th.BG)
        self.header(canvas, "Accounts")
        if not self.records:
            y = HEADER_H + 20
            for line in th.wrap_text(
                    "No accounts enrolled yet. Verify a seed, then choose "
                    "Enrol Account to check addresses without it in future.",
                    (th.WIDTH - 2 * MARGIN) // fonts.S.width):
                canvas.text(MARGIN, y, line, th.MUTED, font=fonts.S)
                y += 20
        else:
            canvas.text(MARGIN, HEADER_H + 12, "no seed needed", th.GOOD,
                        font=fonts.S)
        for b in self.buttons:
            b.draw(canvas)


class AccountManageScreen(Screen):
    """One enrolled account: what it is, and what can be done to it.

    Identified by xpub, and the record is re-read from the store on every
    draw rather than held: rename happens on a screen above this one, and a
    stale label here would look like the rename silently failed.
    """

    def __init__(self, rec):
        self.xpub = rec["xpub"]
        self.bip = rec["bip"]
        self.fp = drv.xpub_fingerprint(self.xpub)
        w = th.WIDTH - 2 * MARGIN
        # BIP49/84 accounts get a second QR button for the SLIP-132 spelling
        # (ypub/zpub) many wallets ask for. ONLY the form matching this
        # account's script type is offered -- a zpub minted from a BIP44
        # account would import as a valid-looking key watching the wrong
        # addresses. BIP44 has no alternate (xpub IS the form) and BIP86 has
        # none (SLIP-132 predates taproot; the QR screen says so).
        alt = drv.SLIP132.get(self.bip)
        if alt:
            half = (w - BUTTON_GAP) // 2
            qr_row = [
                TapButton(MARGIN, 156, half, 44, "xpub QR",
                          lambda app: app.push(AccountQRScreen(self._rec()))),
                TapButton(MARGIN + half + BUTTON_GAP, 156, half, 44,
                          alt[0] + " QR",
                          lambda app: app.push(
                              AccountQRScreen(self._rec(), slip132=True))),
            ]
        else:
            qr_row = [
                TapButton(MARGIN, 156, w, 44, "Show xpub QR",
                          lambda app: app.push(AccountQRScreen(self._rec()))),
            ]
        self.buttons = qr_row + [
            TapButton(MARGIN, 208, w, 44, "Rename", self._rename),
            TapButton(MARGIN, 260, w, 44, "Delete",
                      lambda app: app.push(AccountDeleteScreen(self._rec()))),
            self.back_button(None),
        ]

    def _rec(self):
        for r in acct.load():
            if r["xpub"] == self.xpub:
                return r
        # deleted underneath us (only reachable mid-flight); a minimal record
        # keeps the screens above from crashing on a missing key
        return {"xpub": self.xpub, "bip": self.bip, "label": ""}

    def _rename(self, app):
        xpub = self.xpub
        bip = self.bip
        app.push(NicknameScreen(
            "", lambda a, text: acct.add(text or _auto_label(bip), xpub, bip)))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Account")
        rec = self._rec()
        y = HEADER_H + 10
        canvas.text(MARGIN, y, rec.get("label") or "account", th.FG,
                    font=fonts.M)
        y += 32
        # The fingerprint is the row a user compares against the id shown at
        # enrolment -- accent, and the primary face, because it is the one
        # thing on this screen checked character by character.
        canvas.text(MARGIN, y, "id " + self.fp, th.ACCENT, font=fonts.M)
        y += 32
        canvas.text(MARGIN, y, _auto_label(self.bip), th.MUTED, font=fonts.S)
        for b in self.buttons:
            b.draw(canvas)


class AccountDeleteScreen(Screen):
    """Confirm deleting an enrolled account.

    Destructive, so it confirms -- but it does not overstate. Only a stored
    PUBLIC key is removed: no funds move, no seed is touched, and the same
    account can be re-enrolled from the seed at any time. The text says so,
    because a user who believes deletion is dangerous will keep records they
    meant to remove -- and every kept xpub is a standing privacy exposure.
    """

    # Menu-grade dwell. EntropyCapturedScreen already records why: an amber
    # button that commits on a plain tap teaches that SOME amber buttons
    # commit instantly, which is the wrong lesson on a device where the roll
    # grid's hold is load-bearing. Diagnostics gives even Acknowledge a dwell
    # because it changes persistent state, and this button destroys a stored record.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, rec):
        self.rec = rec
        self.fp = drv.xpub_fingerprint(rec["xpub"])
        self.buttons = harmonise_scale([
            TapButton(MARGIN, th.HEIGHT - 48, 110, 40, "Delete", self._delete),
            TapButton(th.WIDTH - 98 - MARGIN, th.HEIGHT - 48, 98, 40,
                      "Cancel", lambda app: app.pop()),
        ])

    def _delete(self, app):
        acct.remove(self.rec["xpub"])
        app.pop()   # this confirmation
        app.pop()   # the manage screen for the account that no longer exists

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Delete Account")
        y = HEADER_H + 10
        canvas.text(MARGIN, y, self.rec.get("label") or "account", th.FG,
                    font=fonts.M)
        y += 30
        canvas.text(MARGIN, y, "id " + self.fp, th.MUTED, font=fonts.S)
        y += 30
        for line in th.wrap_text(
                "This removes only the stored public key from this device. "
                "No funds are touched, and nothing is lost for good: "
                "enrolling the same seed again recreates this account "
                "exactly.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width):
            canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)


class AccountQRScreen(Screen):
    """The account xpub as a QR, for import into a watch-only wallet.

    Ungated like the address QR -- an xpub is public, and the enrolment flow
    already made the privacy trade explicitly -- but the exposure line is
    drawn on the screen anyway, because scanning it hands someone every
    address in the account with no way to take that back.

    111 base58 chars in byte mode is a version 6 symbol (41x41), the largest
    this device draws and the reason qr.py grew multi-block support: at the
    4px module floor it spans 196px of the 240px panel, quiet zone included.
    """

    QUIET = 4

    def __init__(self, rec, slip132=False):
        """slip132=True re-encodes the key in its SLIP-132 spelling (ypub for
        BIP49, zpub for BIP84) -- the SAME key, different four version bytes,
        offered because wallets asking for a zpub show one, and a user
        comparing our xpub against their zpub sees two correct values that
        look like a mismatch. The QR always encodes exactly the string this
        screen displays; encoding one form while labelling another would
        rebuild the very mismatch this option removes.

        The id stays computed from the canonical xpub regardless of the
        display form: the fingerprint names the account, not its spelling,
        and must match what was written down at enrolment.
        """
        from .. import qr as qrm
        self.label = rec.get("label") or "account"
        self.fp = drv.xpub_fingerprint(rec["xpub"])
        self.form, self.key = "xpub", rec["xpub"]
        if slip132:
            alt = drv.slip132_form(rec["xpub"], rec.get("bip"))
            if alt:
                self.form, self.key = alt
        # BIP86 gets an explanation instead of an unexplained gap: SLIP-132
        # predates taproot, so no zpub-style form exists for it.
        self.taproot_note = rec.get("bip") == 86
        self.size, self.mat = qrm.byte_qr(self.key.encode())
        self.buttons = [self.back_button(None)]

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Account " + self.form)
        canvas.text(MARGIN, HEADER_H + 4, "%s  id %s" % (self.label, self.fp),
                    th.MUTED)
        # The leading characters of exactly what the QR encodes, so the form
        # on screen is checkable against what the wallet displays.
        canvas.text(MARGIN, HEADER_H + 16, self.key[:26] + "..", th.FG)
        y0 = HEADER_H + 30
        sentence_h = 3 * 11 + 6 + (11 if self.taproot_note else 0)
        cells = self.size + 2 * self.QUIET
        px = min(th.WIDTH // cells, (th.HEIGHT - y0 - sentence_h - 6) // cells)
        _, side, _ = draw_qr_symbol(canvas, self.size, self.mat, y0,
                                    quiet=self.QUIET, px=px)
        y = y0 + side + 6
        for line in th.wrap_text(
                "Anyone who scans this can derive every address in this "
                "account, forever.",
                (th.WIDTH - 2 * MARGIN) // th.CHAR_W):
            canvas.text(MARGIN, y, line, th.WARN)
            y += 11
        if self.taproot_note:
            canvas.text(MARGIN, y, "taproot has no zpub form", th.MUTED)
        for b in self.buttons:
            b.draw(canvas)
