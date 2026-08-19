#!/usr/bin/env python3
"""Execute the lift's LED floor readout, the widened doors and the
HUD's LVL label (the real assembled binary) and check the pixels.

The panel: two seven-segment digits over the doors, decoded back OFF
THE SCREEN segment by segment for levels 01, 10, 38 and 50 -- every
lit segment solid #F0 (both pixels pen 5), every dark one empty, and
no ink at all on a level without a lift.  The doors: check_elevator
accepts a body overlapping the 8-byte double doors and nothing wider.
The HUD: LVL sits between the key counts and the number, the number
changes with the level, the label does not."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

LO = S("LINE_OFFSETS")
LINE = [MEM[LO + 2 * y] | MEM[LO + 2 * y + 1] << 8 for y in range(200)]
FONT = [0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F]
GEOM = [(0, 160, 3, 1), (2, 160, 1, 4), (2, 164, 1, 4), (0, 167, 3, 1),
        (0, 164, 1, 4), (0, 160, 1, 4), (0, 163, 3, 1)]

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(("OK   " if c else "FAIL ") + m)


def cpu():
    z = Z80()
    z.sp = 0x1000
    return z


def clear():
    MEM[0xC000:0x10000] = bytes(0x4000)
    MEM[S("DRAW_PAGE")] = 0xC0


def seg_state(x0, seg):
    dx, y, w, h = GEOM[seg]
    vals = {MEM[0xC000 + LINE[y + i] + x0 + dx + j]
            for i in range(h) for j in range(w)}
    return vals == {0xF0} if vals != {0} else False


# --- the readout, decoded off the screen for four stops
for level, col in ((1, 20), (10, 36), (38, 44), (50, 60)):
    clear()
    MEM[S("CURRENT_LEVEL")] = level
    MEM[S("ELEVATOR_COL")] = col
    run(cpu(), S("DRAW_LIFT_PANEL"))
    want = (level // 10, level % 10)
    good = True
    for pos, digit in enumerate(want):
        for seg in range(7):
            lit = bool(FONT[digit] >> seg & 1)
            good &= seg_state(col + 4 * pos, seg) == lit
    chk(good, f'level {level}: the panel reads "{want[0]}{want[1]}", '
              f"segment for segment")
ink = sum(1 for y in range(160, 200) for x in range(80)
          if MEM[0xC000 + LINE[y] + x])
clear()
MEM[S("ELEVATOR_COL")] = 0xFF
run(cpu(), S("DRAW_LIFT_PANEL"))
ink = sum(1 for y in range(200) for x in range(80)
          if MEM[0xC000 + LINE[y] + x])
chk(ink == 0, "no lift on the level: not a pixel drawn")

# --- the double doors' catchment: any body overlap, nothing wider
hits = []
COL = 40
for px in range(30, 52):
    MEM[S("ELEVATOR_COL")] = COL
    MEM[S("PLAYER_STATE")] = 0
    MEM[S("PLAYER_Y")] = 176
    MEM[S("PLAYER_X")] = px
    MEM[S("INPUT_NEW")] = 1 << 2          # INP_UP
    MEM[S("VISITED_STOPS")] = 0           # ...but nowhere to go: the
    reached = []                          # scan returns harmlessly
    _step = Z80.step
    def step(self, reached=reached):
        if self.pc == S("ELEVATOR_UP"):
            reached.append(1)
        _step(self)
    Z80.step = step
    run(cpu(), S("CHECK_ELEVATOR"))
    Z80.step = _step
    if reached:
        hits.append(px)
chk(hits == list(range(COL - 2, COL + 7)),
    f"the doors answer to a body overlapping bytes {COL}..{COL+7} "
    f"(gripped at {hits})")

# --- the HUD: LVL in front of the number
def hud(level):
    clear()
    MEM[S("CURRENT_LEVEL")] = level
    MEM[S("PLAYER_ENERGY")] = 5
    MEM[S("GAME_LIVES")] = 3
    for i in range(5):
        MEM[S("KEYS_HELD") + i] = 0
    for i in range(4):
        MEM[S("SCORE") + i] = 0
    run(cpu(), S("DRAW_HUD"))
    grab = lambda x0, x1: bytes(MEM[0xC000 + LINE[y] + x]
                                for y in range(8) for x in range(x0, x1))
    return grab(24, 33), grab(33, 40)


label12, num12 = hud(12)
label57, num57 = hud(57)
chk(any(label12), "the LVL label is inked before the number")
chk(label12 == label57, "...and reads the same on every level")
chk(num12 != num57 and any(num12),
    "...while the number itself changes with the level")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
