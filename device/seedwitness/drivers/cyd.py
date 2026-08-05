"""CYD (ESP32-2432S028R "Cheap Yellow Display") hardware bring-up: exact pin
wiring for the ili9341.Display + xpt2046.Touch drivers vendored alongside
this file.

Pin numbers verified against the canonical witnessmenow/ESP32-Cheap-Yellow-
Display PINS.md reference and cross-checked against a working MicroPython
CYD driver-init example (RandomNerdTutorials), fetched 2026-08-01:

  Display (ILI9341, SPI1 / HSPI):
    SCK=14  MOSI=13  CS=15  DC=2   Backlight=21
    RST: the board ties the display's reset line to the ESP32's own EN pin,
    not to a dedicated GPIO -- confirmed via a MicroPython community
    discussion documenting this exact board's quirk. rdagger's Display class
    requires a real Pin object for rst regardless (its __init__ always calls
    self.rst.init(...) with no None-check), so this uses GPIO22 -- otherwise
    unused on this board -- purely to satisfy the driver's API. Toggling it
    does nothing to the physical display (it's genuinely disconnected from
    the reset line); the display already came out of its own power-on reset
    via EN before this code ever runs, which is sufficient in practice (this
    is the documented community workaround, not a guess).

  Touch (XPT2046, SPI2 / VSPI, separate bus from the display):
    SCK=25  MOSI=32  MISO=39  CS=33  IRQ=36

  Resolution: 240x320 portrait, USB edge at the top.

The active profile below is measured on the target board. Touch calibration
can vary between CYD units; this release supports the checked-in bounds rather
than loading arbitrary per-unit calibration. The public hardware profile is
documented in docs/HARDWARE.md.
"""
from machine import SPI, Pin, SoftSPI

from .ili9341 import Display, color565  # noqa: F401  (color565 re-exported for callers)
from .xpt2046 import Touch

# Portrait: the USB/power connector is on a SHORT edge of the panel, so
# holding the device cable-up (keeping it clear of the hands) means the screen
# is taller than wide. This is also the ILI9341's native orientation.
WIDTH = 240
HEIGHT = 320

_DISPLAY_SCK = 14
_DISPLAY_MOSI = 13
_DISPLAY_CS = 15
_DISPLAY_DC = 2
_DISPLAY_BACKLIGHT = 21
_DISPLAY_DUMMY_RST = 22  # see module docstring: not physically wired to the panel

_TOUCH_SCK = 25
_TOUCH_MOSI = 32
_TOUCH_MISO = 39
_TOUCH_CS = 33
_TOUCH_IRQ = 36


def init_display():
    backlight = Pin(_DISPLAY_BACKLIGHT, Pin.OUT)
    backlight.value(1)

    spi = SPI(1, baudrate=60_000_000, sck=Pin(_DISPLAY_SCK), mosi=Pin(_DISPLAY_MOSI))
    display = Display(
        spi,
        cs=Pin(_DISPLAY_CS),
        dc=Pin(_DISPLAY_DC),
        rst=Pin(_DISPLAY_DUMMY_RST),
        width=WIDTH,
        height=HEIGHT,
        # Portrait, the panel's native orientation, so no MADCTL row/column
        # exchange is involved. Worth knowing why that matters here: in
        # LANDSCAPE this panel only addressed its full area at rotation=90 and
        # not at 270, even though both set the MV bit and both look like a
        # valid 320x240 frame in code. At 270 the X axis stopped near 240 and
        # the bottom 80 rows were never written, leaving stale pixels from
        # earlier frames on the glass. Measured with an on-screen ruler from a
        # freshly reset board, with this Display init the only one in the
        # session (this board ties the panel's reset line to EN rather than a
        # GPIO, so Display.reset() is a no-op and stacking inits in one
        # session leaves MADCTL unreliable -- always verify from a cold boot).
        #
        # 270, chosen by showing all four rotations at this 240x320 frame and
        # picking the one that read upright with the USB edge at the top. Note
        # rotation=0 was NOT it: the content came out a quarter turn off, so
        # this panel's MADCTL behaviour does not line up with a textbook
        # ILI9341 (rotation=270 also truncated the window back when the UI was
        # landscape). Settings here are chosen by measurement, not by reading
        # the datasheet.
        rotation=270,
        # This panel is RGB-ordered, not BGR. The driver defaults bgr=True,
        # which sets MADCTL's BGR bit and swaps red and blue on every single
        # colour. Verified directly with labelled swatches: at bgr=True a
        # band drawn (255,0,0) appeared blue, (0,0,255) appeared red, and the
        # bitcoin-orange accent (247,147,26) came out teal -- exactly
        # (26,147,247), the same numbers with the outer channels exchanged.
        #
        # Worth knowing: this looks like a palette problem and is not one.
        # Recolouring theme.py to compensate would have made the board look
        # right while the values stayed wrong, so the PIL-backed screenshots
        # (which use those same values, correctly) would have disagreed with
        # the device -- the same class of sim-versus-hardware divergence that
        # hid the black-box-behind-text bug.
        bgr=False,
    )
    # Kept on the display object so the canvas can reach it for idle blanking.
    # Seed words sit in the 16x32 face and the panel holds its last frame with
    # no refresh, so without this they stay readable at arm's length until
    # someone touches the board. The most likely real compromise of this device
    # is not an attacker; it is the user walking off to find a pen.
    display.backlight = backlight
    return display


class _PortraitTouch(Touch):
    """Touch mapped to the portrait display, with the Y axis inverted.

    The XPT2046 is a separate controller on its own bus and knows nothing
    about the ILI9341's rotation, so the raw axes are fixed to the glass while
    the display's are not. Measured on this unit at rotation=270: raw X rises
    with screen X (no swap needed), but raw Y FALLS as screen Y rises --
    1700 at the top of the screen down to 261 at the bottom.

    The inversion is applied here rather than by swapping y_min and y_max,
    which is the obvious-looking fix and is wrong: raw_touch() range-checks
    every sample with `if self.y_min <= y <= self.y_max`, so reversed bounds
    make that test unsatisfiable and silently reject every touch instead of
    flipping it. That failure is indistinguishable from a dead touchscreen.

    Also clamps. The vendored normalize() can return exactly width/height at
    the extremes (a raw reading at the calibration limit maps to 320 on a
    0..319 screen), and after inversion that becomes -1, which would sit
    outside every button and quietly swallow edge taps.
    """

    def normalize(self, x, y):
        x, y = super().normalize(x, y)
        y = self.height - 1 - y
        if x < 0:
            x = 0
        elif x >= self.width:
            x = self.width - 1
        if y < 0:
            y = 0
        elif y >= self.height:
            y = self.height - 1
        return x, y


def init_touch():
    # SoftSPI, NOT hardware SPI(2) -- this is the difference between touch
    # working and not working on this board.
    #
    # With hardware SPI(2) on these pins the XPT2046 never returned data:
    # every conversion read 0, on every channel, so no tap ever resolved to a
    # point and the app looked like it had no touchscreen at all. The pin map
    # was never the problem. Probing each candidate MISO pin with the chip's
    # single-ended TEMP/AUX channels (which answer without anything touching
    # the panel) showed GPIO39 returning live mid-scale values over SoftSPI
    # while hardware SPI on the identical pins returned flat zero:
    #
    #     hardware SPI(2) @1MHz   TEMP0=0    AUX=0    Y=0
    #     SoftSPI         @200kHz TEMP0=411  AUX=86   Y=1496
    #
    # Every other candidate pin read flat 0 or flat 4095 (nothing driving
    # them), confirming GPIO39 is correct and the bus was at fault. The likely
    # cause is that T_OUT is on GPIO39, one of the ESP32's input-only
    # GPIO34-39 pins, which does not route to the hardware SPI peripheral's
    # MISO through the GPIO matrix the way an ordinary pin does. Bit-banging
    # sidesteps the peripheral entirely.
    #
    # Speed is irrelevant here: the XPT2046 tops out around 2MHz anyway, and
    # this bus carries a handful of 3-byte transactions per tap.
    #
    # The Y axis is inverted relative to the display -- see _PortraitTouch.
    spi = SoftSPI(baudrate=200_000, polarity=0, phase=0,
                  sck=Pin(_TOUCH_SCK), mosi=Pin(_TOUCH_MOSI),
                  miso=Pin(_TOUCH_MISO))
    touch = _PortraitTouch(
        spi,
        cs=Pin(_TOUCH_CS),
        # int_pin is deliberately NOT passed. Confirmed on real hardware:
        # supplying it makes xpt2046.Touch.__init__ register an IRQ on
        # GPIO36 whose callback is self.int_press, which then calls
        # self.int_handler(x, y) -- but int_handler defaults to None, so
        # every touch edge raised "TypeError: 'NoneType' object isn't
        # callable" out of the ISR (it fired spuriously at boot even
        # untouched, GPIO36 being an input-only pin with no pull).
        # This app polls via HardwareCanvas.get_tap() -> Touch.get_touch()
        # and never uses the interrupt path, so the IRQ is pure downside:
        # int_press() also does SPI reads and sleep() inside interrupt
        # context, racing the polled reads on this same touch SPI bus.
        width=WIDTH,
        height=HEIGHT,
        # MEASURED on this unit, portrait rotation=270, by tapping four
        # crosshairs and fitting a line through the raw ADC readings:
        #
        #   screen( 34, 34) -> raw( 398,1725)   screen(206, 34) -> raw(1745,1676)
        #   screen( 34,286) -> raw( 285, 265)   screen(206,286) -> raw(1735, 257)
        #
        # Residual error is about +/-7px on X and +/-5px on Y, comfortably
        # inside the 34px minimum touch target the UI uses.
        #
        # These are NOT the commonly-cited defaults (100/1962/100/1900), which
        # were never verified against this board. A unit whose taps land
        # consistently offset is outside this release's calibrated profile.
        x_min=65, x_max=2016, y_min=67, y_max=1895,
    )
    return touch
