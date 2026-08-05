"""Host side of the golden-frame check: render the same cases through
MockCanvas and compare against what a real panel produced.

    python tools/golden_frame.py --port COM9          # capture and compare
    python tools/golden_frame.py --capture cap.txt    # compare a saved capture
    python tools/golden_frame.py --save-png out/      # write both bitmaps to look at

Exit non-zero if any case differs. On a difference it reports how many pixels
disagree and, with --save-png, writes device/mock/diff PNGs so the divergence
can be looked at rather than guessed at.

See device_tests/golden_frame.py for why this exists and why it compares
monochrome shape rather than colour.
"""
import argparse
import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))
sys.path.insert(0, str(ROOT / "sim"))

CAPTURE_W = 240
CAPTURE_H = 40
TEXT_X = 4
TEXT_Y = 4

CASES = (
    ("XS", None, "seedwitness 0123"),
    ("S", "S", "seedwitness 0123"),
    ("M", "M", "seedwitness 0123"),
    ("L", "L", "0123 abcd"),
    # Not text. fill_circle is the one shape primitive the UI draws (die pips),
    # and two rasterisers would disagree pixel-for-pixel, so it is rendered
    # through theme.circle_spans on both sides and checked here like a glyph.
    ("PIPS", "pips", None),
    # blit_mask (the splash wordmark): pseudo-random bits from the same
    # expression on both sides -- see device_tests/golden_frame.py.
    ("MASK", "mask", None),
)

MASK_W, MASK_H = 100, 30


def mask_case_data():
    stride = (MASK_W + 7) // 8
    return bytes((i * 73 + 41) & 0xFF for i in range(stride * MASK_H))


def mock_bitmaps():
    from mock_canvas import MockCanvas          # noqa: E402
    from seedwitness.ui import fonts            # noqa: E402

    faces = {"S": fonts.S, "M": fonts.M, "L": fonts.L}
    out = {}
    for name, face_key, text in CASES:
        c = MockCanvas()
        c.fill((0, 0, 0))
        if face_key == "pips":
            for i, r in enumerate((3, 5, 8)):
                c.fill_circle(30 + i * 60, 20, r, (255, 255, 255))
        elif face_key == "mask":
            c.blit_mask(4, 4, MASK_W, MASK_H, mask_case_data(),
                        (255, 255, 255), (0, 0, 0))
        else:
            c.text(TEXT_X, TEXT_Y, text, (255, 255, 255),
                   font=faces.get(face_key) if face_key else None)
        px = bytearray(CAPTURE_W * CAPTURE_H)
        img = c.image.convert("RGB")
        for y in range(CAPTURE_H):
            for x in range(CAPTURE_W):
                if img.getpixel((x, y)) != (0, 0, 0):
                    px[y * CAPTURE_W + x] = 1
        out[name] = pack(px)
    return out


def pack(px):
    out = bytearray((CAPTURE_W * CAPTURE_H + 7) // 8)
    for i in range(CAPTURE_W * CAPTURE_H):
        if px[i]:
            out[i >> 3] |= 0x80 >> (i & 7)
    return bytes(out)


def unpack(blob):
    px = bytearray(CAPTURE_W * CAPTURE_H)
    for i in range(CAPTURE_W * CAPTURE_H):
        if blob[i >> 3] & (0x80 >> (i & 7)):
            px[i] = 1
    return px


def capture_from_device(port):
    script = ROOT / "device_tests" / "golden_frame.py"
    p = subprocess.run([sys.executable, "-m", "mpremote", "connect", port,
                        "run", str(script)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("device capture failed:\n%s\n%s" % (p.stdout, p.stderr))
    return p.stdout


def parse_capture(text):
    out = {}
    for line in text.splitlines():
        if line.startswith("CASE "):
            _, name, payload = line.split(None, 2)
            out[name] = base64.b64decode(payload)
    return out


def save_png(dirpath, name, dev, mock):
    from PIL import Image
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    dp, mp = unpack(dev), unpack(mock)
    for label, px in (("device", dp), ("mock", mp)):
        img = Image.new("RGB", (CAPTURE_W, CAPTURE_H))
        img.putdata([(255, 255, 255) if v else (0, 0, 0) for v in px])
        img.resize((CAPTURE_W * 2, CAPTURE_H * 2), Image.NEAREST).save(
            d / ("%s_%s.png" % (name, label)))
    img = Image.new("RGB", (CAPTURE_W, CAPTURE_H))
    img.putdata([(255, 60, 60) if a != b else (40, 40, 40) if a else (0, 0, 0)
                 for a, b in zip(dp, mp)])
    img.resize((CAPTURE_W * 2, CAPTURE_H * 2), Image.NEAREST).save(
        d / ("%s_diff.png" % name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="serial port to capture from, e.g. COM9")
    ap.add_argument("--capture", help="use a saved capture instead of a board")
    ap.add_argument("--save-png", help="write device/mock/diff PNGs here")
    args = ap.parse_args()

    if args.capture:
        dev = parse_capture(Path(args.capture).read_text())
    elif args.port:
        dev = parse_capture(capture_from_device(args.port))
    else:
        ap.error("pass --port or --capture")

    mock = mock_bitmaps()
    failures = 0
    for name, _, _ in CASES:
        if name not in dev:
            print("  MISSING     %-3s no capture from the device" % name)
            failures += 1
            continue
        d, m = dev[name], mock[name]
        if d == m:
            print("  OK          %-3s" % name)
            continue
        differing = sum(bin(a ^ b).count("1") for a, b in zip(d, m))
        print("  MISMATCH    %-3s %d pixels differ" % (name, differing))
        failures += 1
        if args.save_png:
            save_png(args.save_png, name, d, m)

    print()
    if failures:
        print("FAIL: the sim does not match the panel for %d case(s)" % failures)
        return 1
    print("PASS: the sim renders what the panel renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
