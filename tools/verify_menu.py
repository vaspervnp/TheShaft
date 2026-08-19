#!/usr/bin/env python3
"""Execute the title screen's attract pages (the real assembled
binary) and check what the player will see.

ms_pages paints the CURRENT side into BOTH buffers -- the hardware
flip must show the same page whichever buffer comes up.  The exhibits
page keeps lines 120-127 black, because ms_loop blinks PRESS SPACE
there on both sides; every exhibit row carries pixels (a picture AND
its caption); and turning attract_pg back to 0 repaints the title with
its backdrop."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

# what start: does first: carry the pixel shelf from the load image's
# buffer-B gap (#4000) home to #A600
HD, HE = S("HIDATA_START"), S("HIDATA_END")
MEM[HD:HE] = MEM[0x4000:0x4000 + HE - HD]

LO = S("LINE_OFFSETS")
LINE = [MEM[LO + 2 * y] | MEM[LO + 2 * y + 1] << 8 for y in range(200)]

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(("OK   " if c else "FAIL ") + m)


def cpu():
    z = Z80()
    z.sp = 0x1000
    return z


def row_pixels(base, y0, y1, x0=0, x1=80):
    n = 0
    for y in range(y0, y1):
        a = base + LINE[y]
        n += sum(1 for v in MEM[a + x0:a + x1] if v)
    return n


MEM[S("CURRENT_MAP")] = S("MENU_MAP") & 0xFF
MEM[S("CURRENT_MAP") + 1] = S("MENU_MAP") >> 8

# --- the exhibits side, into both buffers
MEM[S("ATTRACT_PG")] = 1
run(cpu(), S("MS_PAGES"), max_steps=60_000_000)
same = all(MEM[0x4000 + i] == MEM[0xC000 + i] for i in range(0x4000))
chk(same, "ms_pages paints the SAME page into both buffers")
rows = [16, 32, 48, 64, 80, 96, 112, 136, 152, 168, 184]
counts = [row_pixels(0xC000, y, y + 8) for y in rows]
chk(all(c > 20 for c in counts),
    f"all 11 exhibit rows carry a picture and a caption {counts}")
chk(row_pixels(0xC000, 0, 8) > 10, "the title line is up")
chk(row_pixels(0xC000, 120, 128) == 0,
    "lines 120-127 stay black: the blinking prompt's berth")

# --- back to the title side
MEM[S("ATTRACT_PG")] = 0
run(cpu(), S("MS_PAGES"), max_steps=60_000_000)
same = all(MEM[0x4000 + i] == MEM[0xC000 + i] for i in range(0x4000))
chk(same, "flipping back: both buffers again agree")
chk(row_pixels(0xC000, 32, 48) > 50,
    "...and the title page's big letters are back")
chk(row_pixels(0xC000, 128, 144, 30, 50) > 10,
    "...with the face-off diorama on its crates")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
