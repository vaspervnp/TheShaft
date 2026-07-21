#!/usr/bin/env python3
"""sprite_gen.py -- ASCII-art -> Amstrad CPC Mode 0 masked sprite data (rasm defb lines).

Usage:
    python3 tools/sprite_gen.py [artfile.txt]

Art format: one text line per sprite scanline, one character per pixel.
    .        transparent pixel (background shows through)
    0-9,a-f  pen number (Mode 0 has 16 pens)

All lines must be the same, EVEN width (Mode 0 packs 2 pixels per byte).
If no art file is given, the built-in test sprite (the mechanic) is used.

Output: interleaved (MASK,DATA) byte pairs, one defb line per scanline,
ready to paste into a rasm source file.  The sprite routine computes:
    screen_byte = (screen_byte AND mask) OR data

Mode 0 pixel-to-bit layout (the CPC interleaves pen bits across the byte):
    byte bit:   7  6  5  4  3  2  1  0
    meaning:   L0 R0 L2 R2 L1 R1 L3 R3
    (Ln = bit n of the LEFT pixel's pen, Rn = bit n of the RIGHT pixel's pen)
So a LEFT pixel occupies bits 7,5,3,1 (mask #AA) and a RIGHT pixel
occupies bits 6,4,2,0 (mask #55).
"""

import sys

MECHANIC = """\
..7777..
.777777.
.788887.
.788887.
..8888..
..3333..
.333333.
83333338
83333338
.333333.
.333333.
.33..33.
.33..33.
.33..33.
.22..22.
.22..22.
"""


def left_pixel(pen):
    """Encode a pen (0-15) into the LEFT-pixel bit positions of a Mode 0 byte."""
    return ((pen & 1) << 7) | ((pen & 2) << 2) | ((pen & 4) << 3) | ((pen & 8) >> 2)


def right_pixel(pen):
    """RIGHT pixel uses the same layout shifted right one bit."""
    return left_pixel(pen) >> 1


def convert(art, label="sprite"):
    rows = [r for r in art.splitlines() if r.strip()]
    width = len(rows[0])
    if width % 2:
        sys.exit("error: sprite width must be even (2 pixels per Mode 0 byte)")
    if any(len(r) != width for r in rows):
        sys.exit("error: all art rows must be the same width")

    out = [f"{label}:"
           f"   ; {width}x{len(rows)} px = {width // 2} bytes x {len(rows)} lines,"
           f" (mask,data) interleaved"]
    for row in rows:
        pairs = []
        for i in range(0, width, 2):
            cl, cr = row[i], row[i + 1]
            mask = data = 0
            if cl == '.':
                mask |= 0xAA                       # keep background in left pixel
            else:
                data |= left_pixel(int(cl, 16))    # solid: mask bits stay 0
            if cr == '.':
                mask |= 0x55                       # keep background in right pixel
            else:
                data |= right_pixel(int(cr, 16))
            pairs.append(f"#{mask:02X},#{data:02X}")
        out.append(f"        defb {', '.join(pairs)}   ; {row}")
    return "\n".join(out)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--flip"]
    flip = "--flip" in sys.argv
    if args:
        with open(args[0]) as f:
            art = f.read()
        name = args[0].rsplit("/", 1)[-1].split(".")[0]
    else:
        art, name = MECHANIC, "spr_mechanic"
    if flip:                       # mirror horizontally: art-level reverse
        art = "\n".join(line[::-1] for line in art.splitlines())
        name += "_flip"
    print(convert(art, name))
