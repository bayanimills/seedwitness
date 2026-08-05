"""MicroPython boot entrypoint -- this is what runs automatically when the
CYD powers on (MicroPython auto-executes main.py after boot.py).

On the real board's filesystem, device/embit/, device/seedwitness/, and the
flattened contents of device/mp_shims/ all live at the filesystem root
together (see seedwitness/compat.py's docstring for why mp_shims is kept
separate in this repo but flattened at deploy time) -- so no sys.path
manipulation is needed here the way the desktop test/sim runners need it.

THE IMPORT ORDER BELOW IS LOAD-BEARING -- do not "tidy" it. See the comment
above the embit import.
"""
import gc

import machine

# MicroPython boots the ESP32 at 160MHz; this part is rated for 240MHz and the
# whole workload here is CPU-bound interpreted Python. The dominant cost by far
# is mnemonic_to_seed()'s PBKDF2 (2048 rounds of pure-Python HMAC-SHA512).
# Measured on this board: 885s at 160MHz vs 618s at 240MHz, a 1.43x win for
# one line.
# Nothing here depends on clock-derived timing (no WiFi/BT, no PWM, the SPI
# baudrates are set explicitly), so raising it is safe.
machine.freq(240_000_000)

# Collect DURING the import burst, not only when the heap is exhausted.
# By default this port's GC runs only on allocation failure, so the boot
# imports interleave long-lived module objects with transient compile/import
# garbage -- and on a GC that never compacts, that interleaving is permanent
# fragmentation. A 16KB allocation threshold makes collections run steadily
# through the burst, so the transients are swept before the next long-lived
# allocation is placed and the survivors pack tightly. Measured on a
# constrained-heap experiment matching this port: the post-boot largest
# contiguous block rose from 33,303 to 45,830 bytes at identical free
# totals. It also keeps the load-on-demand flow imports (seedwitness.ui.
# flow_*) landing in a coherent heap mid-session, which is what the whole
# split relies on.
gc.threshold(16384)

# Hold the radios off, and say so where it can be checked.
#
# No code in this firmware uses WiFi or BT, and MicroPython does not bring them
# up on its own -- measured on this board, both interfaces read inactive at
# boot. But the About screen tells the user there is no wifi and no bluetooth,
# and "nothing switched them on" is a weaker statement than "they are off".
# Claiming the stronger one means enforcing it.
#
# Cost: bringing up the driver far enough to deactivate it permanently
# consumes about 12KB of heap that a soft reset does not give back (it is
# ESP-IDF allocation, outside MicroPython's GC). That is a real price on a
# board with ~30KB spare, and it is paid here, first, while the heap is
# unfragmented, rather than mid-session.
def _radios_off():
    try:
        import network
        for iface in (network.STA_IF, network.AP_IF):
            w = network.WLAN(iface)
            if w.active():
                w.active(False)
    except Exception:
        pass
    try:
        import bluetooth
        ble = bluetooth.BLE()
        if ble.active():
            ble.active(False)
    except Exception:
        pass


# NOT CALLED -- calling this boot-loops the board.
#
# Deploying `_radios_off()` here made the device reset repeatedly: it printed
# its line, then `rst:0xc (SW_CPU_RESET)`, over and over. That is a hard CPU
# reset, not a Python exception, so it is something inside the ESP-IDF WiFi
# bring-up rather than a MemoryError we could catch. Both interfaces already
# read inactive at boot on this board, so the call was defensive rather than
# corrective, and it is not worth an unbootable device.
#
# Left in place, unused and documented, because the underlying issue is real:
# see SECURITY.md. The About screen's wifi/bluetooth claim currently rests
# on "no code uses them", which is verifiable by reading this firmware, and
# not on "the hardware has been switched off".
# _radios_off()

# embit is deliberately NOT imported here any more.
#
# The early `from embit import bip39` dated from when the BIP39 wordlist was
# a ~65KB in-RAM list that had to land in an unfragmented heap or the board
# boot-looped. The wordlist moved to flash long ago, and the split of
# screens.py into load-on-demand flow modules (seedwitness/ui/flow_*.py)
# finished the job: embit now loads with the first flow that actually
# derives (address/accounts) and is unloaded again when the session ends.
# Keeping it here would make every boot and every non-deriving flow pay
# ~40KB of permanent residency for code only two flows use -- measured on
# this board, that residency is most of the difference between the home
# screen booting with ~11KB free and booting with room to run a ceremony.
#
# `import seedwitness` applies the MicroPython stdlib shims via its
# __init__ -> compat (hashlib.pbkdf2_hmac etc.) before anything touches
# hashlib; the shims are the one piece that must be resident from the start.
import seedwitness  # noqa: F401,E402  (side effect: installs compat shims)

gc.collect()

# Record why this boot happened (machine.reset_cause()) before the UI comes
# up. A watchdog or brownout reset is indistinguishable from a clean power
# cycle once the home screen is drawn, and that distinction is exactly what
# a "the device rebooted on its own" field report needs. record_boot never
# raises. It persists the incremented boot counter on every boot and only
# changes the reset-cause field when needed; the bounded record cannot grow.
from seedwitness import diag  # noqa: E402

diag.record_boot()
diag_pending = diag.has_unacknowledged()

# record_boot was diag's only job at boot; the info flow and the crash
# handler re-import it on demand (cheap, and both run with a collected
# heap). Dropping BOTH references -- sys.modules and the package attribute
# -- is what actually frees it; either one alone pins the module forever.
import sys  # noqa: E402

del sys.modules["seedwitness.diag"]
del diag
delattr(seedwitness, "diag")
gc.collect()

from seedwitness.ui.app import run_on_hardware  # noqa: E402
from seedwitness.ui import screens as _screens  # noqa: E402

# diag.py was deliberately unloaded before the resident UI was imported.
# Carry only its one-bit alert state across that boundary; a full diagnostic
# read remains load-on-demand behind Verify Build.
_screens.DIAG_PENDING = diag_pending
del diag_pending

run_on_hardware()
