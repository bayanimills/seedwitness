"""The RESIDENT UI core: TapButton, Screen/MenuScreen, the home screen, the
crash screen, the demo gate, and the machinery that loads everything else on
demand.

Touch-native, not a port of the Pi build's joystick+button navigation --
every screen is tap targets you touch directly. Screen contract: draw(app,
canvas) and handle_tap(app, canvas, x, y). canvas is passed to handle_tap
(not just draw) because a couple of screens (deriving an address) need to
render an interim "please wait" state before a slow blocking call. Neither
this file nor App (app.py) import canvas.py or any driver module: they only
ever touch the `canvas` object handed to them, so the exact same code runs
against HardwareCanvas (device/main.py) or MockCanvas (sim/, tests/).

Why this file is small and the flows are elsewhere
--------------------------------------------------
This module used to hold every screen in the app plus module-scope imports
of derive (and through it the whole embit EC chain), entropy, mnemonic,
passphrase, attest and diag. All of it was resident from boot for every
flow, including flows that never touch it: measured on the board, the home
screen sat at ~10.7KB free with a largest contiguous run under 2KB, and
both primary jobs (SeedQR export, verify-with-derivation) died mid-flight
with MemoryError.

Now only this core is resident. Each user-facing flow lives in its own
module -- flow_generate, flow_verify, flow_address, flow_accounts,
flow_info -- imported at the moment of navigation (flow_screen below) and
unloaded again when the session ends (unload_flows, called by
App.reset_to_home and the crash handler; device only). Heavy dependencies
ride along: entropy/mnemonic load with the generate flow, derive+embit only
with the address and accounts flows, attest/diag only with info, qr only
inside the screens that actually export. Unloading genuinely works on this
port: del sys.modules[name] plus dropping the parent-package attribute
recovers 93-98% of a flow's RAM across repeated cycles; the residue is
one-time qstr interning.

A cross-flow reference is always flow_screen("flow", "ClassName") AT THE
NAVIGATION POINT, never a module-scope import. A missing or broken flow
module therefore raises ImportError/AttributeError inside handle_tap, which
the main loop routes to the crash screen -- loud, attributable, and never a
silently blank frame.
"""
import gc as _gc
import sys as _sys

from .. import accounts as acct
from . import fonts
from . import theme as th

try:  # MicroPython
    from time import sleep_ms as _sleep_ms
except ImportError:  # desktop CPython
    from time import sleep as _sleep

    def _sleep_ms(ms):
        _sleep(ms / 1000.0)

MARGIN = 8
HEADER_H = 36   # a 24px title with 6px of air above and below
BUTTON_H = 34
BUTTON_GAP = 8

# Demonstration mode. True while the session holds a demo-populated seed;
# synced from app.demo by App.draw before every frame. A module flag rather
# than a parameter because it is consumed inside Screen.header(), which every
# screen calls first -- so the DEMO stamp travels to every present AND future
# screen through one code path nobody has to remember. A demonstration seed
# later mistaken for a real one is the worst outcome this device can produce,
# so the marking is structural, not per-screen. Living HERE, in the resident
# core, is what lets the stamp survive any amount of flow loading and
# unloading: the flag cannot be unloaded with a flow because it was never in
# one.
DEMO = False
DEMO_BADGE_W = 48

# Set at boot from diag.txt and updated by the crash/acknowledgement paths.
# This boolean is resident; diag.py is not.  Keeping the full diagnostics
# module load-on-demand preserves the home screen's measured heap boundary.
DIAG_PENDING = False

# sentinel: "derive the face from the geometry" (None is a real choice --
# the built-in 8x8 -- so it cannot double as the default)
_AUTO = object()


# Standard die faces on a 3x3 grid, as (col, row) with the origin top-left.
# These are the real arrangements, not an invention: a 3 runs along the
# leading diagonal, a 6 is two columns of three. Getting them wrong would make
# the button look like a die and mean something else, which is worse than
# showing a digit.
PIP_LAYOUT = {
    1: ((1, 1),),
    2: ((0, 0), (2, 2)),
    3: ((0, 0), (1, 1), (2, 2)),
    4: ((0, 0), (2, 0), (0, 2), (2, 2)),
    5: ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
    6: ((0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2)),
}

# Short per-BIP names, used by the address flow's headers, the all-types
# rows, and the accounts flow's auto labels. In the core because two
# different flows need it and neither may import the other.
SHORT_NAMES = {44: "Legacy", 49: "Nested", 84: "SegWit", 86: "Taproot"}


def _auto_label(bip):
    """The generated fallback label for an enrolled account ("BIP84 SegWit").

    Honest about the derivation but blind to purpose: two accounts of the
    same type are indistinguishable under it, which is why enrolment offers a
    nickname and an empty nickname falls back to this rather than to "".
    """
    return "BIP%d %s" % (bip, SHORT_NAMES.get(bip, ""))


# ---------------------------------------------------------------------------
# Load-on-demand flows

def flow_screen(flow, name):
    """Class `name` from seedwitness.ui.flow_<flow>, importing it now.

    THE cross-flow reference mechanism: every navigation from one flow to a
    screen in another goes through here, at the moment of the tap. If the
    flow module is missing from the filesystem or broken, this raises
    ImportError (or AttributeError for a missing class) inside handle_tap,
    and the main loop's crash handler turns that into the crash screen with
    the module named on it. It can never silently render a blank frame.

    The collect before a fresh import is load-bearing on-device: a flow
    module is one of the largest single allocations the steady state makes,
    and it must land in a swept heap, not on top of the previous screen's
    garbage.
    """
    mod = "seedwitness.ui.flow_" + flow
    m = _sys.modules.get(mod)
    if m is None:
        _gc.collect()
        __import__(mod)
        m = _sys.modules[mod]
    return getattr(m, name)


_FLOW_MODULES = ("generate", "verify", "address", "accounts", "info")

# Everything only the flows need. Unloaded with them at session end so a new
# session starts from the same big, coherent heap a fresh boot has. Order
# matters loosely: children before their package, so the parent is still in
# sys.modules when the attribute is dropped.
_FLOW_DEPS = (
    "seedwitness.qr",
    "seedwitness.attest",
    "seedwitness.diag",
    "seedwitness.passphrase",
    "seedwitness.derive",
    "seedwitness.mnemonic",
    "seedwitness.entropy",
    "embit.bip39",
    "embit.bip32",
    "embit.script",
    "embit.ec",
    "embit.networks",
    "embit.base58",
    "embit.bech32",
    "embit.hashes",
    "embit.compact",
    "embit.misc",
    "embit.base",
    "embit.util.key",
    "embit.util.py_ripemd160",
    "embit.util.py_secp256k1",
    "embit.util.secp256k1",
    "embit.util",
    "embit.wordlists.bip39",
    "embit.wordlists.base",
    "embit.wordlists",
    "embit",
    "secp256k1",
    "flash_wordlist",
)


def _unload(name):
    mod = _sys.modules.pop(name, None)
    if mod is None:
        return
    # Both references must go. sys.modules keeps the module importable;
    # the attribute on the parent package keeps it REACHABLE, and either
    # one alone pins the whole module in RAM forever.
    head = name.rsplit(".", 1)
    if len(head) == 2:
        parent = _sys.modules.get(head[0])
        if parent is not None:
            try:
                delattr(parent, head[1])
            except AttributeError:
                pass


def unload_flows():
    """Give every load-on-demand module back to the heap.

    Only meaningful when nothing on the navigation stack still uses them --
    callers (App.reset_to_home, the crash handler) collapse the stack first.
    Measured on the MicroPython port this build targets: del sys.modules[x]
    plus dropping the parent attribute recovers 93-98% of a flow's RAM,
    repeatably, with no leak across cycles; the residue is one-time qstr
    interning.

    Device only. On desktop CPython (pytest, sim) modules stay put: tests
    import these modules directly and monkeypatch into them, and yanking
    them out from underneath the suite would break identity assumptions
    memory pressure never justifies on a machine with gigabytes free.
    """
    if _sys.implementation.name != "micropython":
        return
    for f in _FLOW_MODULES:
        _unload("seedwitness.ui.flow_" + f)
    for name in _FLOW_DEPS:
        _unload(name)


def release_module(name):
    """Give ONE reloadable module back to the heap mid-session, device only.

    For modules that hold no state and are re-imported at their next use
    (the qr encoder is the case in point: every user keeps only the drawn
    matrix, never the module). Used by the address flow to top up headroom
    right before a derivation -- the caller is responsible for the claim
    that nothing live still needs the module. Desktop CPython is exempt for
    the same reasons as unload_flows.
    """
    if _sys.implementation.name != "micropython":
        return
    _unload(name)
    _gc.collect()


# Aliases the old monolithic module exposed at module scope (it imported
# these packages directly; tests and tools still reach them as S.mn etc.).
_LAZY_MODULES = {
    "mn": "seedwitness.mnemonic",
    "drv": "seedwitness.derive",
    "ent": "seedwitness.entropy",
    "pph": "seedwitness.passphrase",
    "att": "seedwitness.attest",
    "dg": "seedwitness.diag",
}

# Where every flow-resident name lives, for module __getattr__: attribute
# access on THIS module (screens.QRExportScreen, `from ...screens import
# WordEntryScreen`) transparently loads the owning flow. This is what keeps
# the desktop test suite, the sim and the device smoke script source-
# compatible with the split -- and it is lazy, so merely importing this
# module still costs only the core.
_FLOW_OF = {
    "MethodSelectScreen": "generate",
    "WordCountScreen": "generate",
    "RollEntryScreen": "generate",
    "SeedSignerRollGateScreen": "generate",
    "RollCountConfirmScreen": "generate",
    "EntropyWarningScreen": "generate",
    "EntropyCapturedScreen": "generate",
    "WordListScreen": "generate",
    "ChecksumWordScreen": "generate",
    "GenerateCompleteScreen": "generate",
    "QRExportScreen": "generate",
    "VerifyEntryScreen": "verify",
    "ManualWordCountScreen": "verify",
    "WordEntryScreen": "verify",
    "BackupResultScreen": "verify",
    "InvalidSeedScreen": "verify",
    "PassphraseLengthScreen": "address",
    "PassphraseRollScreen": "address",
    "PassphraseWordsScreen": "address",
    "DerivationPathScreen": "address",
    "EnrolScreen": "address",
    "AddressIndexScreen": "address",
    "DerivingScreen": "address",
    "AllAddressesScreen": "address",
    "AccountKeyScreen": "address",
    "AddressDisplayScreen": "address",
    "AddressQRScreen": "address",
    "draw_grouped_address": "address",
    "NicknameScreen": "accounts",
    "DamagedAccountScreen": "accounts",
    "AccountRowButton": "accounts",
    "AccountListScreen": "accounts",
    "AccountManageScreen": "accounts",
    "AccountDeleteScreen": "accounts",
    "AccountQRScreen": "accounts",
    "VerifyBuildScreen": "info",
    "DiagScreen": "info",
    "AboutScreen": "info",
}


def __getattr__(name):
    flow = _FLOW_OF.get(name)
    if flow is not None:
        return flow_screen(flow, name)
    lazy = _LAZY_MODULES.get(name)
    if lazy is not None:
        __import__(lazy)
        return _sys.modules[lazy]
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Widgets

class TapButton:
    LINE_GAP = 4
    PAD = 10        # keep text clear of the border on every side

    def __init__(self, x, y, w, h, label, on_tap, font=_AUTO, pips=None):
        self.x, self.y, self.w, self.h = x, y, w, h
        # A newline splits the label across centred lines, which is what makes
        # a large face usable on a 240px-wide screen: "Generate New Seed" is
        # 204px on one line at 12px/char and would crowd the border, but sits
        # comfortably as two lines.
        self.label = label
        self.on_tap = on_tap
        # The face follows the button. Pass an explicit font only to force
        # one; otherwise it is derived from the geometry, so a button can be
        # resized or a label reworded without anyone remembering to re-tune a
        # font size somewhere else. That split is what let a hardcoded width
        # survive the landscape-to-portrait move and silently truncate an
        # address, so here the two cannot drift apart.
        self.font = self._fit_font() if font is _AUTO else font
        # Draw this die face instead of the label. The label is still set and
        # still sizes the button, so tests, harmonise_scale and anything
        # reading .label keep working -- and a face with no layout falls back
        # to the digit rather than rendering nothing.
        self.pips = pips if pips in PIP_LAYOUT else None

    # The type ladder a label may occupy, largest first: 16x32 hero, 12x24
    # primary, 8x16 caption, and the built-in 8x8 (font=None) as the floor
    # that always fits something. The hero face covers only digits/lowercase/
    # signs (see ui/fonts), so it is skipped for any label it cannot spell.
    LADDER = None  # filled in below the class; fonts aren't defined yet here

    def _fit_font(self):
        """Largest face whose text block fits inside the button."""
        lines = self.label.split("\n")
        longest = 0
        for line in lines:
            if len(line) > longest:
                longest = len(line)
        if longest == 0:
            return None
        avail_w = self.w - self.PAD
        avail_h = self.h - self.PAD
        for f in self.LADDER:
            if not f.has(self.label):
                continue
            need_w = longest * f.width
            need_h = len(lines) * f.height + (len(lines) - 1) * self.LINE_GAP
            if need_w <= avail_w and need_h <= avail_h:
                return f
        return None  # 8x8 floor

    @property
    def char_w(self):
        return self.font.width if self.font else th.CHAR_W

    @property
    def char_h(self):
        return self.font.height if self.font else th.CHAR_H

    def contains(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    # Optional standing-state edge, overriding the normal border when the
    # button represents something currently stored. Set by the caller, not
    # derived here, because a button should not have to know what the app
    # holds.
    edge = None

    def draw(self, canvas, accent=False, locked=False):
        """`accent` outlines the button (pressed, sweep in progress).
        `locked` fills it solid, the moment the choice is committed -- a
        colour change is the clearest signal that the hold succeeded and the
        finger can lift, distinct from the outline that merely means
        'registering'."""
        fill = th.ACCENT if locked else th.BUTTON_BG
        border = th.ACCENT if (accent or locked) else (self.edge or th.BUTTON_BORDER)
        canvas.fill_rect(self.x, self.y, self.w, self.h, fill)
        canvas.rect(self.x, self.y, self.w, self.h, border)
        if locked:
            color = th.BG            # dark text on the solid accent fill
        else:
            color = th.ACCENT if accent else th.FG
        if self.pips is not None:
            self._draw_pips(canvas, color)
            return
        lines = self.label.split("\n")
        cw = self.char_w
        chh = self.char_h
        block_h = len(lines) * chh + (len(lines) - 1) * self.LINE_GAP
        ty = self.y + (self.h - block_h) // 2
        for line in lines:
            tx = self.x + (self.w - len(line) * cw) // 2
            tx = max(tx, self.x + 2)
            # bg MUST be the button's own fill. Text on this display is never
            # transparent -- the driver blits a full glyph cell and defaults
            # its background to black -- so omitting this paints a black box
            # behind every label, on top of the grey button.
            canvas.text(tx, ty, line, color, bg=fill, font=self.font)
            ty += chh + self.LINE_GAP


def _draw_pips(self, canvas, color):
    """A die face, centred in the button.

    Sized from the SHORTER edge so a wide button gives a square face rather
    than an ellipse of dots. Spacing and radius both scale with the button,
    so the D6 grid and any future larger layout stay proportional without a
    second set of constants to keep in step.
    """
    size = self.w if self.w < self.h else self.h
    step = size * 2 // 7    # face spans 4/7 of the short edge, plus the pips
    r = size // 9
    if r < 2:
        r = 2               # below this a pip stops reading as a dot
    cx = self.x + self.w // 2
    cy = self.y + self.h // 2
    for col, row in PIP_LAYOUT[self.pips]:
        canvas.fill_circle(cx + (col - 1) * step, cy + (row - 1) * step, r, color)


TapButton._draw_pips = _draw_pips
del _draw_pips

TapButton.LADDER = (fonts.L, fonts.M, fonts.S)


def harmonise_scale(buttons):
    """Give a row/column of same-sized buttons one shared face.

    Each button can size its own label, but in a group that produces three
    different faces on one screen purely because the words differ in length,
    which reads as inconsistent rather than responsive. Dropping them all to
    the smallest that fits keeps the group uniform while still growing or
    shrinking with the buttons themselves. (Every face is strictly smaller
    than the ones above it in both dimensions, so the smallest chosen face
    is guaranteed to fit every button in the group.)
    """
    if not buttons:
        return buttons
    smallest = min(buttons, key=lambda b: b.char_h)
    for b in buttons:
        b.font = smallest.font
    return buttons


class Screen:
    buttons = ()

    # Hold-to-confirm. 0 means act immediately on press; any positive value is
    # the dwell in milliseconds before the action commits, during which the
    # button is outlined and a dial sweeps its perimeter. Lifting early
    # cancels. Screens choose their own dwell by how costly a mis-hit is: see
    # MenuScreen (brief, just enough to see what you touched) and
    # RollEntryScreen (longer, because a wrong roll silently corrupts the
    # entropy and cannot be detected afterwards).
    SWEEP_MS = 0
    SWEEP_STEPS = 10
    SWEEP_T = 4       # thickness of the dial stroke

    # How long the solid "locked in" fill stays up before the action runs.
    # Brief: it is an acknowledgement, not a delay to sit through.
    LOCK_MS = 90

    def handle_tap(self, app, canvas, x, y):
        for b in self.buttons:
            if b.contains(x, y):
                self._dispatch(app, canvas, b)
                return

    def _dispatch(self, app, canvas, b):
        """The full press ritual for one button: sweep if this screen has a
        dwell, locked flash, collect, fire. Factored out of handle_tap so
        screens that keep controls outside self.buttons (the roll screen's
        demo [!]) put them through the SAME ritual instead of quietly
        re-implementing a plain tap."""
        if self.SWEEP_MS:
            if not self._hold_to_confirm(canvas, b):
                b.draw(canvas)      # cancelled: wipe the dial
                return
            # committed: flip the button to solid so the change of
            # colour, not just a closed ring, says it counted
            b.draw(canvas, locked=True)
            canvas.present()
            lock = getattr(canvas, "hold_ms", self.LOCK_MS)
            if lock:
                _sleep_ms(self.LOCK_MS)
        # Collect before dispatch: on_tap is where the next screen is
        # constructed, and construction is the biggest allocation
        # burst the steady state has -- a dozen TapButtons, their
        # labels, the _fit_font ladder walk. The main loop's own
        # collect runs AFTER handle_tap returns, so without this the
        # burst lands on a heap still littered with the previous
        # draw's garbage. On a GC that never compacts, doing it now
        # is the difference between "fits" and the Generate Seed
        # MemoryError this codebase has already shipped once. Cost:
        # a few ms, on a path where the user just held a button for
        # 170ms anyway.
        _gc.collect()
        b.on_tap(app)

    def _hold_to_confirm(self, canvas, b):
        """Outline the pressed button and sweep a dial round it while held.

        Returns True if the finger stayed down for the whole sweep. Screens
        run on both the device and the desktop mock, so the dwell comes from
        the canvas (`hold_ms`) when it offers one -- the mock sets 0 so tests
        and screenshots still exercise the drawing without sleeping.
        """
        b.draw(canvas, accent=True)          # the "engaged" outline
        self._press_preview(canvas, b)
        hold = getattr(canvas, "hold_ms", self.SWEEP_MS)
        per_step = hold // self.SWEEP_STEPS
        for i in range(1, self.SWEEP_STEPS + 1):
            self._draw_sweep(canvas, b, i / self.SWEEP_STEPS)
            canvas.present()
            if per_step:
                _sleep_ms(per_step)
            # No getattr default here, and specifically not the old
            # `lambda: True`. That default meant "finger still down", so any
            # canvas without touch_active made every hold-to-confirm succeed
            # automatically -- on the roll screen, turning a brush into a
            # committed roll that silently corrupts the entropy and cannot be
            # detected afterwards. A missing implementation must break loudly,
            # not quietly agree.
            if not canvas.touch_active():
                return False
        return True

    def _press_preview(self, canvas, b):
        """Optional extra feedback while a button is held. Screens with targets
        smaller than a fingertip override this to show what is under the
        finger somewhere the finger is not covering."""

    def _draw_sweep(self, canvas, b, frac):
        """Progress around the button's perimeter, clockwise from top-left."""
        t = self.SWEEP_T
        remaining = int(2 * (b.w + b.h) * frac)
        seg = min(remaining, b.w)
        if seg > 0:
            canvas.fill_rect(b.x, b.y, seg, t, th.ACCENT)
        remaining -= b.w
        if remaining > 0:
            seg = min(remaining, b.h)
            canvas.fill_rect(b.x + b.w - t, b.y, t, seg, th.ACCENT)
            remaining -= b.h
        if remaining > 0:
            seg = min(remaining, b.w)
            canvas.fill_rect(b.x + b.w - seg, b.y + b.h - t, seg, t, th.ACCENT)
            remaining -= b.w
        if remaining > 0:
            seg = min(remaining, b.h)
            canvas.fill_rect(b.x, b.y + b.h - seg, t, seg, th.ACCENT)

    def draw(self, app, canvas):
        raise NotImplementedError

    def _header_right_limit(self, canvas):
        """Right edge the title must stop before.

        Anything sitting in the header band -- in practice the corner Back
        button -- claims that space, so the title is measured against what is
        left rather than the full screen width. Without this a long title
        runs underneath the button and both become unreadable.
        """
        limit = canvas.width - MARGIN
        for b in self.buttons:
            if b.y < HEADER_H:
                edge = b.x - 4
                if edge < limit:
                    limit = edge
        return limit

    def header(self, canvas, text):
        """Uppercase, left aligned, vertically centred in the header band.

        The face is fitted, not fixed, and fitted against the space actually
        free: most titles get the 12x24 primary face; a long title on a
        screen that reserves the Back corner drops to the 8x16 caption face
        rather than sliding under the button. Screens are expected to keep
        their titles short enough for 12x24 -- the fallback is a safety
        net, not a budget (see tests/test_no_text_clipping.py's header test).

        The rule under the band is BUTTON_BORDER, not MUTED: MUTED is a TEXT
        colour, and at full brightness a 1px structural line competes with
        the content it is meant to organise. BUTTON_BORDER was chosen (and
        verified on the panel) as exactly "bright enough to read as an edge
        on black", which is all a separator has to do.
        """
        text = text.upper()
        canvas.fill_rect(0, 0, canvas.width, HEADER_H, th.BG)
        tx = MARGIN
        if DEMO:
            # The demonstration stamp: a solid warning block ahead of every
            # title, the panel's highest-attention position. Drawn here, in
            # the one method every screen calls, so no screen can appear
            # without it while a demo seed is live -- a demo seed a user
            # later mistakes for a real one is worse than any crash, because
            # they might put money behind it. Long titles step down a face
            # in demo mode to make room; the stamp wins that trade.
            canvas.fill_rect(0, 0, DEMO_BADGE_W, HEADER_H, th.WARN)
            canvas.text(8, (HEADER_H - 8) // 2, "DEMO", th.BG, bg=th.WARN)
            tx = DEMO_BADGE_W + MARGIN
        avail = self._header_right_limit(canvas) - tx
        f = fonts.M if len(text) * fonts.M.width <= avail else fonts.S
        # last resort: clip to what fits rather than run under the button
        max_chars = max(1, avail // f.width)
        ty = (HEADER_H - f.height) // 2
        canvas.text(tx, ty, text[:max_chars], th.ACCENT, bg=th.BG, font=f)
        canvas.hline(0, HEADER_H, canvas.width, th.BUTTON_BORDER)

    # Tucked hard into the top-right corner rather than inset by MARGIN. Back
    # is a frequent, low-risk control, and a corner is the easiest target on a
    # touchscreen to hit without looking -- there is nothing beyond it to
    # overshoot into. The inset only cost accuracy and pushed it into the
    # content.
    BACK_W = 64
    BACK_H = 30
    BACK_INSET = 2

    @staticmethod
    def back_label_for(app):
        """"Home" when popping lands on the home screen, else "Back".

        Same control either way; the word just stops lying about where it
        goes. App.draw() keeps this current for buttons that were built
        before their screen knew its depth in the stack.
        """
        if app is not None and len(getattr(app, "stack", ())) <= 2:
            return "Home"
        return "Back"

    def back_button(self, app, target_x=None):
        x = target_x if target_x is not None else th.WIDTH - self.BACK_INSET - self.BACK_W
        return TapButton(x, self.BACK_INSET, self.BACK_W, self.BACK_H,
                         self.back_label_for(app), lambda app: app.pop())


class MenuScreen(Screen):
    """Vertical stack of full-width tap buttons under a header."""

    title = "Menu"

    # No per-menu font size: each button sizes its own text to fit (see
    # TapButton._fit_scale). Short labels on tall buttons come out large, the
    # long derivation-path labels stay small, and nothing has to be re-tuned
    # by hand when a button is resized or a label reworded.

    # Menus confirm on a hold too, but a brief one -- long enough to show what
    # is under your finger and to let a mis-hit be withdrawn, short enough
    # that navigating never feels like waiting. Much shorter than the roll
    # screen's, where a wrong value is unrecoverable rather than just a wrong
    # turn you can back out of.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    # Smallest button height worth keeping. Menus stretch their buttons to
    # fill the screen, but a long list must not shrink them below a reliable
    # touch target -- past this it falls back to a fixed height and simply
    # stacks from the top.
    MIN_BUTTON_H = 34

    # Space at the bottom the buttons must not grow into, for subclasses that
    # draw a footer under them (see DerivationPathScreen's passphrase note).
    BOTTOM_RESERVED = 0

    def __init__(self, items):
        """items: list of (label, callback) or (label, callback, weight).

        `weight` is the button's share of the vertical space, relative to the
        others (default 1.0). It exists so a secondary action can be visibly
        secondary -- the home screen's "About" is 0.5, half the height of the
        two that actually start a ceremony -- rather than every entry on a
        menu carrying equal visual weight regardless of importance.
        """
        self._items = items
        self.buttons = self._build_buttons()

    def _build_buttons(self):
        """Spread the buttons evenly down the whole screen.

        Portrait leaves a lot of vertical room, and a fixed 34px stack left
        the bottom two thirds of the panel empty while the targets stayed
        small. Dividing the space between the header and the bottom margin
        gives buttons that are both easier to hit and easier to read.
        """
        w = th.WIDTH - 2 * MARGIN
        items = [(it[0], it[1], it[2] if len(it) > 2 else 1.0) for it in self._items]
        n = len(items)
        top = HEADER_H + MARGIN
        available = th.HEIGHT - top - MARGIN - self.BOTTOM_RESERVED - (n - 1) * BUTTON_GAP
        total_weight = 0.0
        for _, _, weight in items:
            total_weight += weight
        buttons = []
        y = top
        for label, cb, weight in items:
            h = int(available * weight / total_weight)
            if h < self.MIN_BUTTON_H:
                # never shrink a target below what a fingertip can hit, even
                # if that means the column no longer fills the screen exactly
                h = self.MIN_BUTTON_H
            buttons.append(TapButton(MARGIN, y, w, h, label, cb))
            y += h + BUTTON_GAP
        return harmonise_scale(buttons)

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, self.title)
        for b in self.buttons:
            b.draw(canvas)


class HomeScreen(MenuScreen):
    title = "seedwitness"

    # Short labels on one line: "Roll a Seed" is 11 characters, which fits a
    # 224px button at the 12x24 face with room to spare.
    #
    # "Roll", never "Generate": the device does not make the seed, the dice
    # do. It performs a public, repeatable calculation and reads out which
    # words the rolls come to -- which is checkable anywhere. A device that
    # MAKES your seed must be trusted; one that READS OUT what your dice
    # decided can be verified. That distinction is the project's central
    # security argument, so no label or copy may put this device in the
    # sentence as the thing that generates/makes/creates the seed.
    #
    # Every destination lives in a load-on-demand flow module, resolved by
    # flow_screen at the moment of the tap -- the home screen is the boot
    # footprint, and it must not pay for any flow the user has not opened.

    def __init__(self):
        super().__init__([
            ("Roll a Seed",
             lambda app: app.push(flow_screen("generate", "MethodSelectScreen")())),
            ("Verify Seed",
             lambda app: app.push(flow_screen("verify", "VerifyEntryScreen")())),
            # The secretless path. Listed third because it only works once
            # something is enrolled, but it is the mode this device is best
            # at: 2.9s per address against 575s, and nothing on the board
            # worth stealing.
            ("Accounts",
             lambda app: app.push(flow_screen("accounts", "AccountListScreen")())),
            # secondary: informational, not one of the two things the device
            # is for, so it takes half the height of the other two
            # 0.6, not 0.5. At 0.5 these two land on 29px, get floored up to
            # the 34px minimum touch target, and the extra 10px pushes the
            # stack one pixel past the bottom of the panel -- caught by
            # test_all_buttons_lie_within_the_screen. 0.6 clears the floor on
            # its own, so the arithmetic and the minimum agree instead of
            # fighting.
            ("About",
             lambda app: app.push(flow_screen("info", "AboutScreen")()), 0.6),
            # Secondary like About: worth having on the front screen so it is
            # findable without a computer, but it is not one of the two jobs
            # the device exists for.
            ("Verify Build !" if DIAG_PENDING else "Verify Build",
             lambda app: app.push(flow_screen("info", "VerifyBuildScreen")()), 0.6),
        ])
        # Red-ish edge whenever anything is enrolled. An account xpub on this
        # device is a permanent privacy exposure the owner opted into, and the
        # home screen is the only place they will reliably see that it is
        # still there. Read at construction rather than in draw() so a corrupt
        # store cannot raise from inside a repaint.
        try:
            enrolled = bool(acct.load())
        except Exception:
            enrolled = False
        if enrolled:
            for b in self.buttons:
                if b.label == "Accounts":
                    b.edge = th.STATE_EDGE
        # A crash stays visible after Start Over and across power cycles until
        # a person reviews and acknowledges it in Diagnostics.  The text mark
        # is explicit; the warning edge makes it visible without relying on
        # punctuation alone.  This is only a boolean copied at boot -- reading
        # diag.txt here would keep diag.py resident during every ceremony.
        if DIAG_PENDING:
            for b in self.buttons:
                if b.label == "Verify Build !":
                    b.edge = th.WARN


DEMO_BTN_W = 24


def _demo_btn(fill):
    """The small [!] immediately left of the Back corner, shared by both
    ceremonies' entry pages. Tapping it opens the consent gate; `fill` runs
    only after the user agrees there."""
    x = th.WIDTH - Screen.BACK_INSET - Screen.BACK_W - 4 - DEMO_BTN_W
    return TapButton(x, Screen.BACK_INSET, DEMO_BTN_W, Screen.BACK_H, "!",
                     lambda app: app.push(DemoConfirmScreen(fill)))


class DemoConfirmScreen(Screen):
    """Agree-before-populate gate for demonstration mode.

    Demo mode exists because recording 50 real rolls just to try a feature is
    punishing. The gate is a full screen, not a hold: the user is consenting
    to something with a real failure mode (a demo seed later mistaken for a
    real one), so the terms are spelled out before anything is filled in.

    The filled values are FIXED and published in this source, deliberately
    not arbitrary: a demo that produced different words each run could be
    believed private, while one that says the same words for everyone, every
    time, is self-evidently not yours. The DEMO stamp every subsequent screen
    carries (see Screen.header) is what actually protects the user; the fixed
    value just removes the temptation to test it.

    on_confirm(app) is supplied by the caller and owns the whole transition:
    populate, set app.demo, and navigate. This screen only asks.
    """

    # The same brief hold a menu uses. Start Demo commits a whole populated
    # ceremony and used to fire on a plain tap -- the only amber commit in
    # the app that did -- which taught that some amber buttons commit
    # instantly, exactly the wrong reflex for the roll grid two taps away.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self, on_confirm):
        self.on_confirm = on_confirm
        self.buttons = harmonise_scale([
            TapButton(MARGIN, th.HEIGHT - 96, th.WIDTH - 2 * MARGIN, 40,
                      "Start Demo", lambda app: self.on_confirm(app)),
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Cancel", lambda app: app.pop()),
        ])

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Demonstration")
        y = HEADER_H + 12
        for line in th.wrap_text(
                "Fills the ceremony with a FIXED practice value: every "
                "screen, no dice. The result is public and NOT a seed for "
                "real funds.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width):
            canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
            y += 20
        y += 6
        for line in th.wrap_text(
                "Every screen will carry a DEMO mark. Never put money "
                "behind it.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width):
            canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
            y += 20
        for b in self.buttons:
            b.draw(canvas, accent=True)


def draw_qr_symbol(canvas, size, mat, y0, quiet=4, px=None):
    """One QR symbol: light panel (quiet zone included), dark modules as
    run-length fill_rects on top, horizontally centred. Existing primitives
    only -- both canvases already rasterise fill_rect identically.

    Shared by every screen that shows a QR (seed export, address, xpub) so
    the rasterisation cannot drift between them: the seed-export tests
    reconstruct the module grid from these exact draw calls, and the same
    guarantees then hold for the ungated public-value QRs for free. It lives
    in the core because those callers are in three different flow modules,
    none of which may import another.

    Returns (x0, side, px) so the caller can lay out around the symbol.
    """
    if px is None:
        px = th.WIDTH // (size + 2 * quiet)
    side = px * (size + 2 * quiet)
    x0 = (th.WIDTH - side) // 2
    canvas.fill_rect(x0, y0, side, side, th.FG)
    ox = x0 + quiet * px
    oy = y0 + quiet * px
    for r in range(size):
        base = r * size
        c = 0
        while c < size:
            if mat[base + c]:
                run = 1
                while c + run < size and mat[base + c + run]:
                    run += 1
                canvas.fill_rect(ox + c * px, oy + r * px, run * px, px, th.BG)
                c += run
            else:
                c += 1
    return x0, side, px


class CrashScreen(Screen):
    """Shown when the run loop catches an unhandled exception.

    Without it the app can unwind out of main.py, print a traceback to an
    unattended serial port, and leave the last frame lit while the application
    is no longer running. This screen makes that failure visible.

    So this screen has one job beyond apologising: make it impossible to
    mistake a dead app for a live one. It says the secret was cleared, because
    the handler clears it before constructing this, and a user whose ceremony
    just died needs to know whether to start over (they do).

    Only the exception type is shown. Exception messages can contain user
    input, including a seed word, so neither this screen nor diag.py reads
    ``str(exc)`` or ``exc.args``.

    In the RESIDENT core, never a flow module, by design: the crash handler
    must be able to construct this with the heap in ruins, and a crash screen
    that first needs a module import is a crash screen that can itself crash.
    """

    def __init__(self, exc, cleared=True):
        self.text = type(exc).__name__
        # False when clearing the seed cache itself threw. The screen must not
        # promise a cleanup that did not happen.
        self.cleared = cleared
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                      "Start Over", lambda app: app.reset_to_home()),
        ]

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Stopped")
        y = HEADER_H + 14
        for line in th.wrap_text(
                "Something went wrong and this ceremony has stopped. Any "
                "seed in memory has been dropped. No secret was written "
                "to flash." if self.cleared else
                "Something went wrong and this ceremony has stopped. The seed "
                "could NOT be cleared from memory. Power the device off now.",
                (th.WIDTH - 2 * MARGIN) // fonts.S.width):
            canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
            y += 20
        y += 8
        canvas.text(MARGIN, y, "Start over from the beginning", th.FG,
                    font=fonts.S)
        y += 30
        for line in th.wrap_text(self.text, (th.WIDTH - 2 * MARGIN) // th.CHAR_W):
            canvas.text(MARGIN, y, line, th.MUTED)
            y += 11
        for b in self.buttons:
            b.draw(canvas, accent=True)
