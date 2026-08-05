# CYD hardware profile

SeedWitness targets the ESP32-2432S028R, commonly sold as the Cheap Yellow
Display (CYD). The application runs on stock ESP32 MicroPython and uses the
on-board ILI9341 display and XPT2046 resistive touchscreen. It does not use the
SD-card socket.

The UI is 240 x 320 portrait with the USB edge at the top. `device/main.py`
sets the ESP32 CPU to 240 MHz; display and touch clocks are configured
independently by the drivers.

## Pin map

| Function | GPIO | Driver setting |
|---|---:|---|
| Display SCK | 14 | SPI 1 |
| Display MOSI | 13 | SPI 1 |
| Display CS | 15 |  |
| Display DC | 2 |  |
| Display backlight | 21 | Active high |
| Display dummy reset | 22 | Required by the driver; not wired to panel reset |
| Touch SCK | 25 | SoftSPI |
| Touch MOSI | 32 | SoftSPI |
| Touch MISO | 39 | SoftSPI |
| Touch CS | 33 |  |
| Touch IRQ | 36 | Not registered; input is polled |

The panel reset line is tied to the ESP32 enable line. GPIO22 exists only to
satisfy the vendored display driver's requirement for a `Pin` object; toggling
it does not reset the panel.

The XPT2046 uses a separate, 200 kHz software SPI bus. On the supported board,
hardware SPI returned zero on the touch controller's MISO path while SoftSPI
on the same pins returned valid readings. The application polls for stable
touches and deliberately does not install the vendored IRQ handler.

## Display and touch settings

The supported display profile uses:

- `rotation=270` for upright portrait output with the USB edge at the top;
- `bgr=False` for the panel's observed RGB colour order; and
- `width=240`, `height=320`.

The checked-in touch calibration is `x=65..2016`, `y=67..1895`. Calibration
can vary between units. The supported profile uses those measured bounds and
does not load per-unit calibration; a board whose taps are consistently offset
is outside the supported hardware profile.

## Clock and persistence

Application timeouts use MicroPython's monotonic `ticks_ms()` API, not wall
clock time. Its value is relative to boot and wraps, so code must compare it
with `ticks_diff()` and construct deadlines with `ticks_add()`. Power loss
resets this time base.

The board has no application-configured source of trustworthy calendar time.
Diagnostics therefore use persistent counters and boot sequence numbers rather
than timestamps. Persistent application data lives on the MicroPython flash
filesystem.

## Security properties

The ESP32 contains Wi-Fi and Bluetooth radios, and the stock MicroPython debug
interface remains available over USB. Secure boot, flash encryption and
tamper resistance are not provided by this profile. These are operating and
threat-model constraints, not pin-configuration details; see
[`SECURITY.md`](../SECURITY.md) before entering wallet material.
