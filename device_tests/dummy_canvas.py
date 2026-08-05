"""No-op Canvas for exercising screens.py/app.py's control flow under real
MicroPython without needing PIL (unavailable there) or real display hardware.
Records nothing, draws nothing -- purely there to catch MicroPython
syntax/stdlib incompatibilities in the UI layer itself (the crypto core is
covered by run_all.py; this covers the screens/app code that wraps it)."""


class DummyCanvas:
    def __init__(self, width=320, height=240):
        self.width = width
        self.height = height

    def fill(self, color):
        pass

    def fill_rect(self, x, y, w, h, color):
        pass

    def rect(self, x, y, w, h, color):
        pass

    def hline(self, x, y, w, color):
        pass

    def fill_circle(self, cx, cy, r, color):
        pass

    def text(self, x, y, s, color, bg=None, scale=1, font=None):
        pass

    def present(self):
        pass

    # hold-to-confirm support: a synthetic press counts as held, and no dwell
    hold_ms = 0

    def touch_active(self):
        return True

    def wait_release(self):
        # True, matching HardwareCanvas's contract: a synthetic finger lifts
        # the instant it is asked to. None would read as a release timeout
        # to _main_loop's release-edge check.
        return True

    def get_press(self):
        return None

    def get_tap(self):
        return None
