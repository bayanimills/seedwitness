"""App state machine: a plain stack-based screen navigator, tap-driven.
Doesn't import canvas.py or any driver -- run_on_hardware() (device/main.py)
and the desktop test/sim runners each construct their own canvas and hand it
in, so this file and screens.py are identical on both platforms.
"""
import gc
import sys

try:  # MicroPython
    from time import sleep_ms as _sleep_ms
    from time import ticks_add as _ticks_add
    from time import ticks_diff as _ticks_diff, ticks_ms as _ticks_ms
except ImportError:  # desktop CPython
    from time import monotonic as _mono, sleep as _sleep

    def _sleep_ms(ms):
        _sleep(ms / 1000.0)

    def _ticks_ms():
        return int(_mono() * 1000)

    def _ticks_add(t, delta):
        return t + delta

    def _ticks_diff(a, b):
        return a - b

from . import screens as scr
from . import theme as th
from .screens import HEADER_H, MARGIN, CrashScreen, HomeScreen


class App:
    def __init__(self):
        self.stack = [HomeScreen()]
        self.session = None  # active RollSession, if a generate ceremony is in progress
        self.last_mnemonic = None  # most recently generated OR verified-valid mnemonic
        # Generated Diceware passphrase for this session, or "" for none.
        # Cleared with everything else on returning home: it is as sensitive
        # as the seed, and a passphrase left set would silently derive a
        # different wallet for whatever the next person checks.
        self.passphrase = ""
        # True while the live session holds a demonstration-populated seed.
        # Synced to screens.DEMO before every frame (see draw), which is
        # what stamps DEMO on every screen; cleared wherever the session
        # ends, so the mark can never outlive the seed it warns about --
        # nor end before it, since the seed cannot survive reset either.
        self.demo = False

    @property
    def screen(self):
        return self.stack[-1]

    def push(self, screen):
        self.stack.append(screen)

    def pop(self):
        """Back. Landing on the home screen ends the session, exactly as Done
        does.

        This used to just pop, and that was a real leak. The corner button
        RELABELS itself to "Home" once the stack is two deep (see
        _sync_back_labels), so the control the user reads as "Home" was not
        the control that cleared anything. Backing out of the verify flow left
        the mnemonic in app.last_mnemonic and the stretched seed in
        derive._cached_seed, indefinitely, while the display showed an
        innocuous home screen.

        Two payoffs for whoever picked the device up next: "Use Last Generated
        Seed" on the verify menu served it back from cache in seconds instead
        of ten minutes, and over the USB REPL the mnemonic sat at a fixed,
        guessable module global. Returning home must drop the session cache;
        for every Back path, it did not.

        Clearing on the way to depth 1 rather than at depth >1 keeps Back
        cheap while it is still navigation, and makes the last Back mean what
        the label says.
        """
        if len(self.stack) > 2:
            self.stack.pop()
        elif len(self.stack) == 2:
            self.reset_to_home()

    def reset_to_home(self):
        self.session = None
        self.last_mnemonic = None
        self.passphrase = ""
        self.demo = False
        # Returning to the home screen ends the session, so drop the cached
        # PBKDF2 seed with it -- it is the most sensitive value the device
        # holds and must not outlive the flow that produced it. Guarded on
        # sys.modules because derive is now load-on-demand (it arrives with
        # the address/accounts flows): if it was never imported, no seed
        # cache exists, and importing the whole embit chain here just to
        # clear nothing would drag ~40KB into a heap this method exists to
        # empty.
        if "seedwitness.derive" in sys.modules:
            from ..derive import clear_seed_cache
            clear_seed_cache()
        self.stack = [HomeScreen()]
        # The stack collapse above dropped the last live references into the
        # flow modules; hand their pages back to the heap so the next
        # ceremony starts from the same big coherent heap a fresh boot has.
        # No-op on desktop CPython -- see unload_flows.
        scr.unload_flows()
        gc.collect()

    def handle_tap(self, canvas, x, y):
        self.screen.handle_tap(self, canvas, x, y)

    def draw(self, canvas):
        scr.DEMO = self.demo   # the header's DEMO stamp follows the session
        self._sync_back_labels()
        self.screen.draw(self, canvas)
        canvas.present()

    def _sync_back_labels(self):
        """Relabel a Back button that actually goes home.

        Screens build their buttons in __init__, before they know how deep in
        the stack they sit, so the label cannot be decided there. Doing it
        here means one place gets it right for every screen instead of each
        one remembering. The face is refitted because "Home" and "Back" are
        different lengths.
        """
        want = self.screen.back_label_for(self)
        for b in getattr(self.screen, "buttons", ()):
            if b.label in ("Back", "Home") and b.label != want:
                b.label = want
                b.font = b._fit_font()


def _prewarm_fonts():
    """Allocate each face's lazy RGB565 scratch cell now, at boot.

    Font.get_letter allocates its cell (S 256 B, M 576 B, L 1024 B) on the
    first glyph drawn with that face. On this build the heap finishes
    booting with ~20KB free but shattered -- measured on the board, the
    largest contiguous run after boot is under 1280 bytes -- so a lazy
    KB-scale allocation that lands mid-session, or even during the first
    full draw, is a MemoryError waiting for the worst moment (the field
    crash breadcrumb "crash MemoryError" is exactly this class). Same cure
    as the display driver's cached clear buffer: pay for every recurring
    or lazy draw-path buffer up front, while boot still has contiguous
    heap, so the steady-state draw path allocates nothing large at all.

    Fail-soft: if a cell cannot allocate even now, keep booting -- the
    face falls back to its lazy path and the crash handler covers the
    rest. On the desktop FrameBuffer is unavailable and get_letter is
    never otherwise called; the try covers that too.
    """
    from . import fonts
    for f in (fonts.S, fonts.M, fonts.L):
        try:
            f.get_letter(" ", 0)
        except Exception:
            pass


# The brand wordmark bitmap, generated by tools/build_mpy.sh from the
# reviewable device/data/wordmark.txt (same flash-backed treatment as
# fonts.bin and the wordlists). Module constant so the sim and tests can
# point it at the build tree; resolved at call time inside _splash_frame.
WORDMARK_FILE = "/wordmark.bin"


def _read_wordmark(path):
    """The wordmark's segments: [(w, h, packed_rows), ...] -- 'seed' then
    'witness', packed as theme.mask_row_spans expects. ~1.1KB transient."""
    f = open(path, "rb")
    try:
        if f.read(4) != b"SWM1":
            raise ValueError("bad wordmark magic")
        segs = []
        for _ in range(f.read(1)[0]):
            hdr = f.read(4)
            w = hdr[0] | (hdr[1] << 8)
            h = hdr[2] | (hdr[3] << 8)
            if not (0 < w <= th.WIDTH and 0 < h <= th.HEIGHT):
                raise ValueError("wordmark segment out of range")
            data = f.read(((w + 7) >> 3) * h)
            if len(data) != ((w + 7) >> 3) * h:
                # a truncated file must fail HERE, where the caller catches
                # it, not as an IndexError inside the blit
                raise ValueError("wordmark truncated")
            segs.append((w, h, data))
        if not segs:
            raise ValueError("empty wordmark")
        if len(segs) < 2 or segs[0][1] != segs[1][1]:
            # The two horizontal logo segments share one baseline. A third,
            # shorter segment is the italic motto at the bottom of the frame.
            raise ValueError("wordmark logo segment heights differ")
        return segs
    finally:
        f.close()


def _splash_frame(canvas):
    """The boot wordmark: the brand wordmark bitmap, 'seed' in the amber
    accent and 'witness' in white, with 'ready' beneath. Drawn directly, not
    a Screen on the stack -- it costs
    no class, holds no state, and can never be navigated back to.

    It is also a diagnostic mark, not just branding: this frame can only
    appear after every import has completed (fonts, screens, the embit
    chain), so a board that hangs BEFORE the splash died importing, and one
    that hangs after it died in the app. On a device whose recurring failure
    is a lit panel over a dead program, that boundary is worth a frame.

    If wordmark.bin is missing or damaged the splash falls back to the same
    words set in the hero face (which covers ' +-0-9a-z' by design): the
    diagnostic value of the frame outranks the brand treatment, so a board
    without the asset still boots and still shows the boundary.
    """
    from . import fonts
    canvas.fill(th.BG)
    segs = None
    try:
        segs = _read_wordmark(WORDMARK_FILE)
    except (OSError, ValueError, IndexError):
        # IndexError belongs here: _read_wordmark indexes the bytes it reads
        # without length-checking them, so a wordmark.bin truncated at the
        # wrong byte (b"SWM1", b"SWM1\x01", b"SWM1\x02\xff\xff") raises
        # IndexError rather than ValueError. This runs on the BOOT path,
        # before any crash containment exists, so an uncaught one unwinds out
        # of run_on_hardware() and leaves a lit panel, a dead app and a live
        # REPL, which is this project's worst named failure mode. The splash
        # is decoration: never let it stop the device from starting.
        pass
    if segs and sum(s[0] for s in segs[:2]) <= canvas.width:
        logo = segs[:2]
        wm_w = sum(s[0] for s in logo)
        wm_h = segs[0][1]
        x = (canvas.width - wm_w) // 2
        y = (canvas.height - wm_h) // 2 - 24
        # seed amber, witness white -- the owner's specified treatment,
        # matching what the text fallback below has always done
        for (w, h, data), color in zip(logo, (th.ACCENT, th.FG)):
            canvas.blit_mask(x, y, w, h, data, color, th.BG)
            x += w
        base = y + wm_h
    else:
        x = (canvas.width - 11 * fonts.L.width) // 2
        y = (canvas.height - fonts.L.height) // 2 - 24
        canvas.text(x, y, "seed", th.ACCENT, font=fonts.L)
        canvas.text(x + 4 * fonts.L.width, y, "witness", th.FG, font=fonts.L)
        base = y + fonts.L.height
    canvas.text((canvas.width - 5 * fonts.S.width) // 2,
                base + 16, "ready", th.MUTED, font=fonts.S)
    if segs and len(segs) >= 3:
        w, h, data = segs[2]
        canvas.blit_mask((canvas.width - w) // 2, canvas.height - h - 10,
                         w, h, data, th.MUTED, th.BG)
    else:
        motto = "Vires in Numeris"
        canvas.text((canvas.width - len(motto) * fonts.S.width) // 2,
                    canvas.height - fonts.S.height - 10,
                    motto, th.MUTED, font=fonts.S)
    canvas.present()


def run_on_hardware():
    """Boot-time entrypoint on a real CYD board. See device/main.py."""
    from ..drivers.cyd import init_display, init_touch
    from .canvas import HardwareCanvas

    display = init_display()
    touch = init_touch()
    canvas = HardwareCanvas(display, touch)
    _prewarm_fonts()
    # Splash before App() exists: tap to clear. get_press returns None on
    # its ~2s timeout, so the wait is a bounded poll, and the release is
    # swallowed so the clearing tap cannot double as a press on the home
    # screen that replaces it.
    _splash_frame(canvas)
    _wait_for_splash_tap(canvas)
    canvas.wait_release()
    _main_loop(App(), canvas)


SPLASH_MAX_MS = 20000        # give up waiting and go to the menu anyway


def _wait_for_splash_tap(canvas):
    """Hold the splash until the user taps, but never forever and never hot.

    Two bugs live in the obvious version of this loop, and both were found on
    real hardware:

    `while canvas.get_press() is None: pass` polls the touch controller flat
    out. The XPT2046 sits on a SoftSPI bus that is BIT-BANGED IN PYTHON, and
    get_touch() only returns a point once five consecutive samples agree
    within tolerance. A loop with no pause never lets the bus settle, so the
    samples never agree and a perfectly good tap never registers: the splash
    simply refuses to clear. Sleeping between polls is not a politeness, it is
    what makes the reading work.

    And an unbounded wait makes the splash a single point of failure. If the
    touch controller is dead or miscalibrated, a device that will not leave its
    first screen is indistinguishable from a bricked one, whereas the menu
    behind it might still have told the user something useful. So it gives up
    after SPLASH_MAX_MS and continues. Losing the tap-to-clear gesture is a
    far smaller failure than losing the device.
    """
    start = _ticks_ms()
    while _ticks_diff(_ticks_ms(), start) < SPLASH_MAX_MS:
        if canvas.get_press() is not None:
            return True
        _sleep_ms(20)
    return False


def _hard_reset():
    """Reboot the board. The one situation that calls this is a crash whose
    CrashScreen cannot itself be drawn (two draw failures in a row, after a
    gc.collect between them) -- at that point continuing means either an
    unusable UI or unwinding to a live REPL with the panel frozen on its
    last frame, and a reset is strictly better than both: RAM is cleared,
    so no seed survives it, and the device comes back at a working home
    screen. A one-line function so desktop tests can intercept it; `machine`
    only exists on the board."""
    import machine
    machine.reset()


def _crash(app, exc):
    """The crash path, shared by every guarded stretch of the main loop.

    An unhandled exception here used to unwind out of main.py,
    print a traceback and leave a LIVE REPL with the mnemonic
    still sitting in derive._cached_key. A MemoryError mid-derive
    is an ordinary failure on a board with tens of KB free, and
    it turned into full disclosure.

    Clear FIRST, before anything that might allocate, because the
    handler itself can fail. Then show the failure: a crashed app
    otherwise leaves its last frame lit on the glass, which is
    visually identical to a working device (this has already
    happened once on this project, and it cost six days).
    """
    cleared = True
    app.demo = False
    try:
        # Guarded like reset_to_home: derive is load-on-demand now, and
        # importing the embit chain from inside a MemoryError handler --
        # the likeliest exception on this path -- would be asking a broken
        # heap for its largest allocation. Not loaded means no cache to
        # clear, which is the honest meaning of cleared=True.
        if "seedwitness.derive" in sys.modules:
            from ..derive import clear_seed_cache
            clear_seed_cache()
    except Exception:
        # Swallowing this silently was a false assurance: the
        # crash screen went on to tell the user "any seed in
        # memory has been cleared" whether or not it had been.
        # Carry the truth to the screen instead.
        cleared = False
    app.session = None
    app.last_mnemonic = None
    app.passphrase = ""
    # Drop the dead screens and their flow modules before anything tries to
    # allocate: when the crash IS a MemoryError, these pages are the
    # difference between drawing a crash screen and wedging. The stack is
    # rebuilt with the CrashScreen at the end of this handler.
    app.stack = []
    try:
        scr.unload_flows()
    except Exception:
        pass
    # Persist the breadcrumb AFTER clearing, so the recorder can
    # never observe live secrets, and never let it raise: this
    # handler is the one path guaranteed to be running when things
    # are already wrong. record_crash writes type name and code
    # location only -- by construction it cannot see the exception
    # message (see diag.py) -- so a future ValueError carrying a
    # user's word cannot end up on flash.
    #
    # Collect BEFORE recording, not only after. The recorder needs
    # real allocations (print_exception into a StringIO, the file
    # write), and when the crash being recorded is itself a
    # MemoryError the heap is by definition too broken to grant
    # them -- observed on the board as a "Something went wrong"
    # screen with diag.txt holding no crash line at all, i.e. the
    # one artefact meant to explain the failure not surviving it.
    # The handler just dropped the session references, so this
    # collect is what turns that garbage back into headroom in
    # time for the write.
    gc.collect()
    try:
        from .. import diag
        if diag.record_crash(exc):
            # Resident one-bit alert; diag itself stays load-on-demand.  A
            # successful persistent write is what makes acknowledgement
            # meaningful after Start Over.
            scr.DIAG_PENDING = True
    except Exception:
        pass
    gc.collect()
    app.stack = [CrashScreen(exc, cleared)]


# The press dot: a small accent rectangle, top right, lit the moment the
# loop accepts a touch and wiped by the cycle's full redraw. It answers two
# questions the panel otherwise cannot: "did my touch land?" (press, dot
# lights; press, no dot -> the panel or calibration is at fault, not the
# app) and "is the app alive?" (this project's recurring failure is a hung
# or crashed app holding its last frame, visually identical to a working
# device -- the dot is drawn from inside the loop, so it can only light if
# the loop is still turning).
#
# Drawn HERE and not in any Screen.draw deliberately: the loop repaints only
# on change, so a per-screen dot would be dead exactly when it matters, and
# no screen can forget a dot it never has to draw.
#
# No idle blink, also deliberately. get_press's ~2s timeout would give the
# loop a free tick, but this panel has no double buffering -- every write is
# visible -- so an idle tick is a periodic flicker in the corner of a screen
# someone is transcribing seed words from, the same human-factors trade that
# removed idle blanking. Liveness stays available on demand: touch the
# glass; no dot means the loop is dead.
#
# Geometry: flush above the header rule at the right edge. This is the one
# top-right slot free on EVERY screen: titles are clamped to x <= 232
# (WIDTH - MARGIN), and the corner Back button, where a screen has one,
# spans (174..237, 2..31) -- rows 32..35 below it are clear. Verified by
# test_press_dot.py against those rects.
PRESS_DOT_W = 6
PRESS_DOT_H = 4


def _press_dot(canvas, on):
    """on=True lights it; on=False paints the slot back to background --
    needed by the overlong-hold path, where the dot outlives the redraw
    that normally wipes it and must go dark the moment the lift is seen."""
    canvas.fill_rect(canvas.width - PRESS_DOT_W - 1, HEADER_H - PRESS_DOT_H,
                     PRESS_DOT_W, PRESS_DOT_H, th.ACCENT if on else th.BG)
    canvas.present()


# ---------------------------------------------------------------------------
# Sleep mode.
#
# Idle blanking was built at ~30s and removed the same day, on the argument
# that a panel which blanks mid-transcription is a control users defeat by
# never letting the device idle, and a control users defeat is worse than
# none. These timings answer that objection instead of repeating it: nobody
# is still copying words after a full hour without touching the glass -- the
# ceremony is over or abandoned -- and the five-minute countdown makes the
# transition visible and preventable, one touch anywhere. A control that
# announces itself and yields to a single tap cannot take a working user by
# surprise, so it cannot teach anyone to work around it.
#
# Entering sleep ENDS THE SESSION, exactly as Done/Home does: mnemonic,
# cached PBKDF2 seed, passphrase, demo flag, all dropped (reset_to_home).
# After an hour unattended the device must not still be holding a seed for
# whoever picks it up next, and the countdown is what makes clearing safe to
# do without asking. The sleeping frame says so in as many words, so a user
# who walks back is told why their ceremony is gone rather than left to
# wonder. (The countdown notice is itself a full frame, so whatever was on
# the panel -- possibly seed words -- is already covered five minutes before
# sleep proper.)
#
# The sleeping frame is never static: a small Zzz block redraws at a new
# position every ~5 seconds. Liveness first, burn-in second. This project's
# recurring failure is a lit panel over a dead program (three freeze
# reports, and once six days of outage that looked healthy throughout); a
# static sleep frame would be indistinguishable from exactly that, while a
# frame that keeps moving can only be produced by a loop that is still
# turning. The motion rides the idle tick get_press() already provides (its
# ~2s touch timeout), so it adds no polling and no sleeps; and each move
# repaints only the block's own rectangle -- a full draw is ~0.5s on this
# panel and is paid exactly twice, once for the countdown notice and once
# for the black frame at sleep entry.
#
# Positions come from an LCG stepped over a ticks_ms seed, not from random/
# urandom: this device deliberately contains no PRNG at all
# (tests/test_no_prng_in_key_path.py), and a drifting Zzz does not justify
# introducing one. The LCG is not random and does not need to be; it only
# has to visit enough different places to spread wear and prove motion.
#
# The wake touch (and a countdown-cancelling touch) is swallowed, never
# dispatched: the user is reaching for a dark panel or a notice and cannot
# see what is under the finger -- same rule as the splash tap.
#
# Module constants rather than literals inside the loop so a bench run can
# drive the whole cycle in seconds (the loop reads them each tick); the
# shipped values are asserted in tests/test_sleep_mode.py.
SLEEP_AFTER_MS = 60 * 60 * 1000   # no touch for this long -> sleep
SLEEP_WARN_MS = 5 * 60 * 1000     # of which: the visible countdown at the end
ZZZ_STEP_MS = 5000                # the sleeping Zzz moves this often

_CD_TIME_Y = 128                  # top of the countdown's remaining-time figure

# The sleeping block: an M-face "Zzz" with two S-face caption lines under it
# ("session cleared" / "touch to wake"). One bounding rectangle so a move is
# one erase plus three text calls, nothing else.
_ZZZ_W = 15 * 8                   # widest caption line, "session cleared"
_ZZZ_H = 24 + 26 + 16             # Zzz cell + caption offsets (30/50) + S cell


def _countdown_text(remaining_ms):
    """Remaining time as e.g. "4m 59s". Seconds rounded UP so the figure
    never shows 0 while time actually remains, and never a negative. Fixed
    six characters for every real value (m is a single digit), so each
    repaint fully overwrites the last. No colon: the L face that renders
    this covers ' +-0-9a-z' only, and 'm'/'s' read better anyway."""
    secs = (remaining_ms + 999) // 1000
    if secs < 0:
        secs = 0
    return "%dm %02ds" % (secs // 60, secs % 60)


def _countdown_time(canvas, txt):
    """Repaint ONLY the remaining-time figure -- the per-tick update. bg= so
    the fixed-width field overwrites its predecessor; nothing else on the
    countdown frame ever changes, so nothing else is ever repainted."""
    from . import fonts
    canvas.text((th.WIDTH - len(txt) * fonts.L.width) // 2, _CD_TIME_Y,
                txt, th.FG, bg=th.BG, font=fonts.L)
    canvas.present()


def _countdown_frame(canvas, remaining_ms):
    """The full countdown notice, drawn ONCE when the countdown begins.
    Direct-drawn like the splash, never a Screen on the stack: it holds no
    state, takes no taps (any touch cancels it in the loop), and must not
    be navigable. Returns the time string painted, which the loop keeps so
    an idle tick landing inside the same second repaints nothing."""
    from . import fonts
    canvas.fill(th.BG)
    title = "sleeping in"
    canvas.text((th.WIDTH - len(title) * fonts.M.width) // 2, 72, title,
                th.ACCENT, bg=th.BG, font=fonts.M)
    hint = "touch to stay awake"
    canvas.text((th.WIDTH - len(hint) * fonts.S.width) // 2, 200, hint,
                th.FG, bg=th.BG, font=fonts.S)
    y = 240
    for line in th.wrap_text(
            "Sleeping ends this session and clears any seed from memory.",
            (th.WIDTH - 2 * MARGIN) // fonts.S.width):
        canvas.text(MARGIN, y, line, th.WARN, font=fonts.S)
        y += 20
    txt = _countdown_text(remaining_ms)
    _countdown_time(canvas, txt)
    return txt


def _zzz_step(canvas, z, now):
    """Move the sleeping mark: erase the old block, paint the new one.
    Partial repaint only -- two rectangles' worth of pixels, never a
    full-screen draw (see the sleep-mode note; the discipline is
    DerivingScreen.update_progress's).

    z is (lcg, x, y, last_move_ticks); x < 0 means nothing painted yet.
    The next position steps a glibc-constants LCG (no PRNG on this device,
    by policy); the low bits feed x and higher bits y so the two do not
    march in step, and a step that lands exactly where the block already
    sits steps again -- the position must visibly CHANGE, because the
    change is the liveness signal."""
    from . import fonts
    lcg, x, y, _ = z
    if x >= 0:
        canvas.fill_rect(x, y, _ZZZ_W, _ZZZ_H, th.BG)
    nx, ny = x, y
    while nx == x and ny == y:
        lcg = (lcg * 1103515245 + 12345) & 0x3FFFFFFF
        nx = lcg % (th.WIDTH - _ZZZ_W + 1)
        ny = (lcg >> 8) % (th.HEIGHT - _ZZZ_H + 1)
    canvas.text(nx, ny, "Zzz", th.ACCENT, bg=th.BG, font=fonts.M)
    canvas.text(nx, ny + 30, "session cleared", th.MUTED, bg=th.BG,
                font=fonts.S)
    canvas.text(nx, ny + 50, "touch to wake", th.MUTED, bg=th.BG,
                font=fonts.S)
    canvas.present()
    return (lcg, nx, ny, now)


def _sleep_frame(canvas, now):
    """Enter sleep: black the panel (the second and last full repaint sleep
    ever performs) and paint the first Zzz block. Returns the zzz state the
    loop threads through _zzz_step. Seeded from the tick counter -- entry
    time varies by the milliseconds of an hour of human behaviour, which is
    plenty for a wear-spreading start point and involves no RNG."""
    canvas.fill(th.BG)
    canvas.present()
    return _zzz_step(canvas, ((now | 1) & 0x3FFFFFFF, -1, -1, now), now)


def _draw_guarded(app, canvas):
    """app.draw with the containment every loop-driven repaint needs: a draw
    that raises becomes the CrashScreen, and a CrashScreen that cannot draw
    either resets the board (RAM cleared, no seed survives) rather than
    unwinding to a frozen panel over a live REPL. See _crash."""
    try:
        app.draw(canvas)
    except Exception as exc:
        _crash(app, exc)
        try:
            # _crash just collected, so the CrashScreen draws into a
            # freshly swept heap; on the reproduced freeze that is
            # ~22KB free and succeeds.
            app.draw(canvas)
        except Exception:
            # Even the crash screen cannot draw. Failing safe beats
            # wedging: reset clears RAM (no seed survives) and boots
            # back to a working home screen. The breadcrumb was
            # already written by _crash before this point.
            _hard_reset()


def _main_loop(app, canvas):
    """The tap-dispatch loop, taken out of run_on_hardware so tests can run
    it against a scripted canvas -- the freeze this loop is written to
    survive only ever manifested inside the real loop, and every REPL-driven
    repro missed it for exactly that reason.
    """
    # Draw once up front, then ONLY after a tap has actually changed something.
    # Redrawing every iteration (the previous behaviour) made the panel visibly
    # flash about every 2 seconds on real hardware: get_tap() returns None on
    # its ~2s touch timeout, and each screen's draw() begins with a full-screen
    # canvas.fill(), so the idle loop was repainting the whole display forever.
    # There is no double buffering here (present() is a no-op; the ili9341
    # driver writes straight to the panel), so every repaint is a visible
    # clear-then-redraw. Nothing on screen animates on its own -- state only
    # advances through handle_tap() -- so drawing on change is sufficient.
    # Dispatch on PRESS, not on release, so a screen can react while the
    # finger is still down -- the roll screen outlines the value under the
    # finger and sweeps a confirm dial around it. wait_release() afterwards
    # keeps one physical touch to one action; without it a resting finger
    # would re-fire the control it is sitting on.
    # Idle handling is SLEEP MODE, not the 30-second blanking that was built
    # and removed earlier -- see the sleep-mode note above the constants for
    # the timings, the countdown that makes them safe, and why the sleeping
    # frame must keep moving. The whole feature runs on the idle tick
    # get_press() already provides (~2s touch timeout), which this loop used
    # to discard.
    app.draw(canvas)
    # True while a dispatched touch has not yet been seen to lift. In this
    # state presses are never dispatched -- on the roll screen a re-dispatch
    # commits the same roll twice and silently corrupts the entropy -- but
    # the loop itself keeps turning in bounded steps. The first version of
    # this guard was `while not canvas.wait_release(): ...` inline, which is
    # bounded per slice but unbounded in total: a finger that stayed down,
    # or a panel stuck reporting touch, span it forever with nothing
    # redrawn and nothing dispatched -- the project's original freeze
    # rebuilt one layer up (fourth field report, long-press on a Verify
    # Seed menu item). As a state of the main loop instead, every
    # iteration is bounded by one wait_release timeout, a stuck panel
    # degrades to "touches are ignored until it clears" rather than a dead
    # device, and the dot stays lit the whole time saying the touch is
    # still registered.
    swallowing = False
    # Sleep-mode state, all loop-local: 0 awake, 1 countdown showing,
    # 2 asleep. last_touch is the ticks_ms at which a finger was last known
    # to be on the glass; cd_txt is the countdown figure last painted (so a
    # tick landing inside the same second repaints nothing); zzz is
    # _zzz_step's state while asleep.
    sleep_state = 0
    last_touch = _ticks_ms()
    cd_txt = None
    zzz = None
    while True:
        if swallowing:
            if canvas.wait_release():
                swallowing = False
                _press_dot(canvas, False)   # lift finally seen: dot off
            last_touch = _ticks_ms()   # the finger is, or was just, down
            gc.collect()
            continue
        press = canvas.get_press()
        if press is None:
            # The idle tick. Guarded like every other loop-driven draw: an
            # OOM painting the countdown or the Zzz must reach the crash
            # screen, not unwind out of main.py to a frozen panel.
            now = _ticks_ms()
            idle = _ticks_diff(now, last_touch)
            try:
                if sleep_state == 0:
                    if idle >= SLEEP_AFTER_MS - SLEEP_WARN_MS:
                        sleep_state = 1
                        cd_txt = _countdown_frame(canvas,
                                                  SLEEP_AFTER_MS - idle)
                elif sleep_state == 1:
                    if idle >= SLEEP_AFTER_MS:
                        # An hour untouched: the ceremony is over or
                        # abandoned. End the session exactly as Done/Home
                        # does, then hold the sleeping frame. See the
                        # sleep-mode note.
                        sleep_state = 2
                        app.reset_to_home()
                        zzz = _sleep_frame(canvas, now)
                    else:
                        txt = _countdown_text(SLEEP_AFTER_MS - idle)
                        if txt != cd_txt:
                            cd_txt = txt
                            _countdown_time(canvas, txt)
                else:
                    due = _ticks_add(zzz[3], ZZZ_STEP_MS)
                    if _ticks_diff(now, due) >= 0:
                        # Anchor the next move to the schedule, not to this
                        # tick, so the ~2s tick granularity averages out to
                        # the 5s cadence instead of stretching every gap to
                        # 6s -- unless the loop fell a whole step behind,
                        # in which case resync rather than burst.
                        if _ticks_diff(now, due) >= ZZZ_STEP_MS:
                            due = now
                        zzz = _zzz_step(canvas, zzz, due)
            except Exception as exc:
                sleep_state = 0
                # Restart idleness, or the very next tick would walk back
                # into the path that just failed: a crash every 2 seconds,
                # each one writing a diag breadcrumb to flash.
                last_touch = now
                _crash(app, exc)
                _draw_guarded(app, canvas)
            continue
        last_touch = _ticks_ms()
        if sleep_state:
            # A touch during the countdown or during sleep is a wake, never
            # an action: the user is reaching for a notice or a dark panel
            # and cannot see what is under the finger, so it is swallowed
            # exactly as the splash tap is. Cancelling the countdown
            # restores the screen it covered, session intact; waking from
            # sleep lands on the home screen the sleep transition reset to.
            sleep_state = 0
            gc.collect()
            _draw_guarded(app, canvas)
            if not canvas.wait_release():
                _press_dot(canvas, True)
                swallowing = True
            continue
        # Light the dot before dispatch, so even a press that changes no
        # state (a miss between buttons) still visibly registered. It stays
        # lit for the whole press -- through the hold-to-confirm sweep and
        # any release wait -- and the redraw below wipes it.
        _press_dot(canvas, True)
        try:
            app.handle_tap(canvas, press[0], press[1])
        except Exception as exc:
            # See _crash for why nothing may unwind past this loop.
            _crash(app, exc)
        # Collect once per interaction, before the redraw. This is the fix
        # for the third freeze (held-down letter key on Verify Seed), and it
        # is load-bearing, not hygiene. A finger that stays down makes every
        # cycle burn wait_release's full timeout at ~100 raw_touch polls per
        # second, each allocating a tuple, plus a full-screen redraw -- and
        # nothing in this loop ever collected, so the garbage compounded
        # across cycles. Measured on the real board: free RAM went 23120 ->
        # 18512 -> 9840 in two held cycles, and on the third the ili9341
        # clear()'s 3840-byte line buffer failed to allocate even after the
        # allocator's emergency collect (the heap was too fragmented to give
        # one contiguous run). With this collect in place the same held-key
        # loop is flat at ~22KB free indefinitely. Cost: a few ms per tap.
        gc.collect()
        # This draw used to sit OUTSIDE the guarded region, so a MemoryError
        # here unwound out of main.py -- panel frozen on its last frame,
        # seed cache intact, live REPL on the USB port, and no diag
        # breadcrumb, because the crash handler only wrapped handle_tap.
        # The user's report ("held b, device froze") and diag.txt ("boot
        # PWRON_RESET", no crash line) are exactly that escape. Every
        # loop-driven repaint now goes through the same containment.
        _draw_guarded(app, canvas)
        # The redraw above deliberately came BEFORE this release wait. The
        # previous ordering waited out the release first, so during a long
        # press the panel kept showing the PRE-tap screen: the menu item you
        # were holding, frozen, for as long as your finger stayed down --
        # several seconds of dead glass indistinguishable from the real
        # freezes this device has had. Now the result of the tap is on the
        # panel within the same cycle, finger down or not.
        #
        # One bounded wait for the lift. Released: this press is over, and
        # the redraw already wiped the dot. Still held: re-light the dot on
        # top of the fresh frame -- "your touch is still registered, lift
        # when ready" -- and hand the release-edge duty to the swallowing
        # state at the top of the loop, which keeps the loop turning.
        if not canvas.wait_release():
            _press_dot(canvas, True)
            swallowing = True
