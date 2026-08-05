"""Info flow: the paged About help, the build-fingerprint screen and the
diagnostics breadcrumb reader.

Load-on-demand: reached only through screens.flow_screen() at the navigation
point and unloaded (device only) when the session ends -- see screens.py's
docstring. attest and diag are imported here and nowhere resident, so the
filesystem hasher and the breadcrumb reader cost heap only while someone is
actually looking at them.
"""
from .. import attest as att
from .. import diag as dg
from . import screens as core
from . import fonts
from . import theme as th
from .screens import (
    BUTTON_GAP,
    HEADER_H,
    MARGIN,
    Screen,
    TapButton,
    harmonise_scale,
)


class VerifyBuildScreen(Screen):
    """Show a fingerprint of the software this board is running.

    Computes on first draw rather than being handed a result, because the
    caller here is a plain menu callback with no canvas to paint an interim
    frame on (the DerivingScreen idiom needs one). Hashing the whole
    filesystem measured well under a second on this board -- ~555 KB/s over
    ~240 KB -- so a two-phase draw is enough and nobody sees a frozen screen.

    The caveat line is not decoration and should not be trimmed to fit a
    future layout. A device reporting its own hash is the thing under audit
    describing itself, and a user who reads this number as proof has been
    misled by the screen that was supposed to inform them. See attest.py.
    """

    def __init__(self):
        self.digest = None
        self.count = 0
        self.total = 0
        # Diagnostics lives behind this screen rather than on it: the
        # fingerprint layout already runs to within a few pixels of the
        # bottom button, and rather than crowding the one number a user
        # must compare by eye, the breadcrumb gets its own page. It sits
        # here, not on the home menu, because the person who wants it is
        # the person doing failure triage -- the same person this screen
        # already serves -- and the ceremony menus stay uncluttered.
        half = (th.WIDTH - 2 * MARGIN - BUTTON_GAP) // 2
        self.buttons = [
            TapButton(MARGIN, th.HEIGHT - 48, half, 40,
                      "Back", lambda app: app.pop()),
            TapButton(MARGIN + half + BUTTON_GAP, th.HEIGHT - 48, half, 40,
                      "Diagnostics", lambda app: app.push(DiagScreen())),
        ]

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Verify Build")
        if self.digest is None:
            canvas.text(MARGIN, HEADER_H + 40, "Hashing every file...", th.MUTED,
                        font=fonts.S)
            canvas.present()
            self.digest, self.count, self.total = att.fingerprint()
            # This digest is available safely only because the user has just
            # paid for a full Verify Build pass.  Persist its short prefix as
            # diagnostic context; record_build skips an unchanged value, so
            # repeat visits do not wear flash.
            dg.record_build(self.digest)
            canvas.fill(th.BG)
            self.header(canvas, "Verify Build")

        y = HEADER_H + 10
        canvas.text(MARGIN, y, "build fingerprint", th.MUTED, font=fonts.S)
        y += 26
        # Hex is 0-9a-f, which the hero face covers, so the number a user has
        # to compare by eye gets the largest type on the device. Two rows of
        # two groups: 16 hex chars at 16px would be 256px on a 240px screen.
        parts = att.short(self.digest).split(" ")
        for row in (parts[:2], parts[2:]):
            line = " ".join(row)
            canvas.text((th.WIDTH - len(line) * fonts.L.width) // 2, y, line,
                        th.ACCENT, font=fonts.L)
            y += fonts.L.height + 6
        y += 6
        canvas.text(MARGIN, y, "%d files, %d bytes" % (self.count, self.total),
                    th.MUTED, font=fonts.S)
        y += 26
        for line in th.wrap_text(
            "Compare against the published manifest. This is a self-check: "
            "tampered software could report anything. For a check that does "
            "not trust this device, read its flash with verify_device.py.",
                (th.WIDTH - 2 * MARGIN) // th.CHAR_W):
            canvas.text(MARGIN, y, line, th.WARN)
            y += 11
        for b in self.buttons:
            b.draw(canvas)


class DiagScreen(Screen):
    """Read the diagnostics breadcrumb back without a computer.

    Facts from diag.txt (see diag.py): cumulative boot/crash/reviewed counts,
    reset cause, the type/location and boot number of the last unhandled
    exception, and the last build prefix verified on-device. On-screen rather
    than serial-only, deliberately: the failures this exists for happen in the
    field, mid-ceremony, where attaching a serial console means breaking the
    air gap the ceremony depends on -- the same reason VerifyBuildScreen exists
    alongside verify_device.py. Serial readback still works (the file is plain
    text at /diag.txt), this screen just makes it optional.

    The privacy paragraph is content, not decoration: a user who sees the
    word "crash" on a seed device deserves to be told, right there, exactly
    what is and is not recorded.
    """

    # Acknowledging changes persistent state, so use the same brief standard
    # hold as menus rather than committing from an accidental brush.
    SWEEP_MS = 170
    SWEEP_STEPS = 8

    def __init__(self):
        if dg.has_unacknowledged():
            half = (th.WIDTH - 2 * MARGIN - BUTTON_GAP) // 2
            self.buttons = harmonise_scale([
                TapButton(MARGIN, th.HEIGHT - 48, half, 40,
                          "Acknowledge", self._acknowledge),
                TapButton(MARGIN + half + BUTTON_GAP, th.HEIGHT - 48,
                          half, 40, "Back", lambda app: app.pop()),
            ])
        else:
            self.buttons = [
                TapButton(MARGIN, th.HEIGHT - 48, th.WIDTH - 2 * MARGIN, 40,
                          "Back", lambda app: app.pop()),
            ]

    def _acknowledge(self, app):
        if not dg.acknowledge_crashes():
            return
        core.DIAG_PENDING = False
        # Rebuild this screen so the acknowledged state and single Back
        # button appear on the very next frame.  The breadcrumb is preserved.
        app.pop()
        app.push(DiagScreen())

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        self.header(canvas, "Diagnostics")
        rec = dg.read()
        budget = (th.WIDTH - 2 * MARGIN) // th.CHAR_W
        y = HEADER_H + 8
        crashes = rec.get("crashes", 0)
        acknowledged = rec.get("ack_crashes", 0)
        pending = crashes > acknowledged
        canvas.text(MARGIN, y,
                    "NEW CRASH - REVIEW" if pending else "no new crash",
                    th.WARN if pending else th.MUTED, font=fonts.S)
        y += 18
        for line in ("boots %d" % rec.get("boots", 0),
                     "crashes %d" % crashes,
                     "reviewed %d" % acknowledged):
            canvas.text(MARGIN, y, line, th.FG)
            y += 11
        y += 3
        canvas.text(MARGIN, y,
                    "boot " + rec.get("boot", "nothing recorded"),
                    th.FG, font=fonts.S)
        y += 18
        canvas.text(MARGIN, y, "last crash", th.MUTED)
        y += 12
        # type + location can run to three lines at the caption face; the
        # record's own length caps (diag._clean) bound it there
        for line in th.wrap_text(rec.get("crash", "nothing recorded"),
                                 budget):
            canvas.text(MARGIN, y, line, th.FG, font=fonts.S)
            y += 18
        if "last_crash_boot" in rec:
            canvas.text(MARGIN, y,
                        "at boot %d" % rec["last_crash_boot"], th.MUTED,
                        font=fonts.S)
            y += 18
        if "build" in rec:
            canvas.text(MARGIN, y, "build " + rec["build"], th.MUTED,
                        font=fonts.S)
            y += 18
        y += 2
        for line in th.wrap_text(
            "No clock is assumed. No messages or user data are recorded.",
                budget):
            canvas.text(MARGIN, y, line, th.MUTED)
            y += 11
        for b in self.buttons:
            b.draw(canvas)


class AboutScreen(Screen):
    """Paged help, one topic per page.

    Paged rather than scrolled, deliberately. There is no framebuffer here --
    the driver writes straight to the panel and a full repaint costs about
    half a second -- so a scroll would have to repaint the text area for every
    step and would crawl. The ILI9341 does have a hardware vertical scroll,
    but it operates on the panel's native axis, which this rotation does not
    line up with. Discrete pages need one repaint per press, always land on a
    whole topic, and never leave a line half off the screen.

    Paragraphs, not pre-broken lines: draw() wraps to whatever width the
    screen has. An empty string is a blank spacer.
    """

    PAGES = [
        ("What This Is", [
            # "Works out", never "makes": the dice decide the seed, this
            # device only performs the public calculation that reads it out.
            "Works out the seed your dice decide, and checks one you "
            "already have.",
            "",
            "It is NOT a wallet. It cannot spend and never signs.",
            "",
            # The most important sentence on the device, and the answer to the
            # August 2026 Coldcard failure: firmware 4.0.1-5.0.3 quietly used
            # a software PRNG instead of the hardware TRNG, giving ~40-bit
            # seeds on Mk3, and owners had no way to tell. The documented
            # mitigation was adding 50 private dice rolls, which is this
            # device's entire ceremony. What protects you is not trusting the
            # randomness here; it is that you supplied it and can recompute
            # the result somewhere else.
            "Your dice decide the seed, not this device. Same rolls, same "
            "words, anywhere. So you can check it.",
            "",
            "No camera, no network. See Trust for the limits.",
        ]),
        ("Roll a Seed", [
            "Roll a real die or flip a coin, and tap what you rolled. 50 "
            "rolls of a D6 give the 128 bits a 12-word seed needs.",
            "",
            "The rolls are hashed with SHA256, which evens out bias in the "
            "die, then become standard BIP39 words.",
            "",
            "Write the words down. They are shown once and never stored.",
        ]),
        ("Verify Seed", [
            "Type in a seed you already have. This device works out the "
            "receive address it should produce.",
            "",
            "Compare that with the address your main wallet shows.",
            "",
            "Matching means both agree. Different means stop, and find out "
            "why before sending funds.",
        ]),
        # This warning belongs on the device as well as in SECURITY.md: the
        # user is standing here when deciding what counts as an independent
        # comparison. It gets its own page because the complete caveat needs
        # more room than the neighbouring topics provide.
        ("Compare", [
            "Compare against a wallet built on different code.",
            "",
            "This device and SeedSigner share the same BIP39 library. A bug "
            "in it would give both the same wrong address, and agreement "
            "would look like proof.",
            "",
            "Two devices agreeing is only a check if they were built "
            "separately.",
        ]),
        ("Trust", [
            "Small enough to read: rolls, SHA256, BIP39, address. Same "
            "rolls, same seed, anywhere.",
            "",
            "It cannot tell a fair die from a loaded one, or real rolls "
            "from 50 taps of one number. That part is on you.",
            "",
            # Replaces "No wifi, no bluetooth, no camera. Nothing leaves this
            # screen." from page 1, which was false twice: the radios are
            # present and cannot be safely switched off (_radios_off boot-loops
            # the board; see SECURITY.md), and USB is a full debug port. That
            # line invited a user to treat this as an airgap the hardware
            # cannot provide, on the screen they read to decide how far to
            # trust it.
            "It HAS wifi and bluetooth it cannot disable, and USB is a debug "
            "port. Offline is where you keep it, not what it is.",
        ]),
    ]

    NAV_H = 34

    # Navigation is by TOPIC, not by position: the two buttons name where they
    # go ("Roll?", "Verify?") instead of saying "Next". On a four-page
    # help section "Next" tells you nothing about whether the thing you want
    # is one press away or three, whereas a named button is a direct answer to
    # the question the reader actually has. Each page offers the two topics it
    # is not.
    NAV_LABELS = ["What?", "Roll?", "Verify?", "Compare?", "Trust?"]

    # The two things the device actually does get direct buttons at the top,
    # always in the same place on every page. What/Trust are context around
    # them and are reached by stepping. Two is the limit at a readable size:
    # four across a 240px screen leaves 50px each.
    TOP_LINKS = (1, 2)
    TOP_H = 30

    def _nav_targets(self):
        """Pages reachable in one press: the two pinned topics plus either
        neighbour. Used by the reachability test."""
        targets = list(self.TOP_LINKS)
        for p in (self.page - 1, self.page + 1):
            if 0 <= p < len(self.PAGES) and p not in targets:
                targets.append(p)
        return [t for t in targets if t != self.page]

    def __init__(self, page=0):
        self.page = page
        self.buttons = self._build_buttons()

    def _build_buttons(self):
        buttons = []
        # --- primary topics, pinned across the top ---
        top_y = HEADER_H + 6
        tw = (th.WIDTH - 2 * MARGIN - BUTTON_GAP) // 2
        for i, target in enumerate(self.TOP_LINKS):
            x = MARGIN + i * (tw + BUTTON_GAP)
            buttons.append(TapButton(x, top_y, tw, self.TOP_H,
                                     self.NAV_LABELS[target],
                                     lambda app, t=target: self._go(app, t)))
        harmonise_scale(buttons)
        # --- sequential paging along the bottom ---
        nav_y = th.HEIGHT - self.NAV_H - MARGIN
        nav = []
        if self.page > 0:
            nav.append(TapButton(MARGIN, nav_y, tw, self.NAV_H, "< Prev",
                                 lambda app: self._go(app, self.page - 1)))
        if self.page < len(self.PAGES) - 1:
            nav.append(TapButton(MARGIN + tw + BUTTON_GAP, nav_y, tw, self.NAV_H,
                                 "Next >", lambda app: self._go(app, self.page + 1)))
        harmonise_scale(nav)
        return buttons + nav + [self.back_button(None)]

    BODY_TOP = HEADER_H + 6 + TOP_H + 8

    def _go(self, app, target):
        # replace rather than stack, so Back always leaves About entirely
        # instead of walking back through every page already read
        app.pop()
        app.push(AboutScreen(target))

    def draw(self, app, canvas):
        canvas.fill(th.BG)
        title, lines = self.PAGES[self.page]
        self.header(canvas, title)
        y = self.BODY_TOP
        budget = (th.WIDTH - 2 * MARGIN) // th.CHAR_W
        for para in lines:
            if not para:
                y += 7
                continue
            for line in th.wrap_text(para, budget):
                canvas.text(MARGIN, y, line, th.FG, bg=th.BG)
                y += 13
        # page position, tucked under the body rather than beside the header
        canvas.text(MARGIN, th.HEIGHT - self.NAV_H - MARGIN - 13,
                    "%d of %d" % (self.page + 1, len(self.PAGES)),
                    th.MUTED, bg=th.BG)
        for b in self.buttons:
            # the pinned topic you are already reading is filled in, so the
            # top row doubles as a position indicator
            here = (b.label in self.NAV_LABELS
                    and b.label == self.NAV_LABELS[self.page])
            b.draw(canvas, accent=not here, locked=here)
