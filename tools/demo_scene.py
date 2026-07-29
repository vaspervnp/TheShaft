#!/usr/bin/env python3
"""demo_scene.py -- build the scripted set for the DEMO disc.

The demo is a camera move over a hand-placed set made from the game's
own tiles.  A full 16:9 band redraw costs ~5 frames, so the scene is
kept SPARSE and emitted as an OBJECT LIST (tile, column, row): each
update erases every visible object at the camera position from two
updates ago and redraws it at the current one.  That is only affordable
while the visible-object count stays inside the budget below, so this
tool simulates the whole camera script and refuses to emit a set that
would drop frames.

The run: up a long ladder, right along the deck, jump a gap, wait out a
steam vent, throw a switch, up a second ladder, pocket a yellow keycard
and walk through the yellow door it opens.

Usage:  python3 tools/demo_scene.py src/demo_scene.asm
"""
import sys

sys.path.insert(0, "tools")
import level_gen as lg

COLS, ROWS = 84, 64                 # world: 336 bytes x 512 lines
BAND = 152                          # 19 char rows -- the 16:9 window
HERO_X, HERO_Y = 38, 120            # hero's FIXED screen slot (bytes, lines)
VISIBLE_BUDGET = 80

FLOOR_ROW, DECK_A, DECK_B = 58, 30, 18
LAD1_COL, LAD2_COL = 19, 50
GAP_COLS = (26, 27)
VENT_COL, SWITCH_COL = 34, 42
KEY_COL, DOOR_COL = 56, 66
KEY_CH, DOOR_LINTEL = 'Y', 'I'      # yellow card, yellow indicator light

# He stands on deck row R when the camera sits at R*8 - (HERO_Y + 16);
# CAM_Y0 is one line low so the 3 s climb lands on exactly 104.  He
# opens the run nine columns short of the ladder, so the first thing
# you see is a mechanic walking a floor, not one already on a rung.
CAM_X0, CAM_Y0 = 2, 329


def deck_cam_y(row):
    return row * 8 - (HERO_Y + 16)


def col_cam_x(col):
    return col * 4 - HERO_X


# anim: how the mechanic is drawn.  event: what the beat does to the set.
CLIMB, WALK_R, WALK_L, JUMP, STAND = 0, 1, 2, 3, 4
EV_NONE, EV_SWITCH, EV_KEY, EV_DOOR, EV_STEAM = 0, 1, 2, 3, 4

# (dx, dy per frame in 8.8 fixed point, frames, anim, event) at 50 Hz
# Each edit gets a beat of stillness BEFORE it fires, so the viewer
# reads the lever/card/door first and then sees it change.
SCRIPT = [
    (1.0,  0.0,  36, WALK_R, EV_NONE),   # 0.7s  along the bottom floor
    (0.0, -1.5, 150, CLIMB,  EV_NONE),   # 3.0s  up the long ladder
    (1.0,  0.0,  28, WALK_R, EV_NONE),   # 0.6s  out along the deck
    (0.6,  0.0,  30, JUMP,   EV_NONE),   # 0.6s  over the gap
    (1.0,  0.0,   6, WALK_R, EV_NONE),   # 0.1s  up to the vent
    (0.0,  0.0,  60, STAND,  EV_STEAM),  # 1.2s  wait out the steam
    (1.0,  0.0,  40, WALK_R, EV_NONE),   # 0.8s  past it, to the lever
    (0.0,  0.0,  12, STAND,  EV_NONE),   # 0.2s  a look at the lever
    (0.0,  0.0,  18, STAND,  EV_SWITCH), # 0.4s  ...and it throws
    (1.0,  0.0,  32, WALK_R, EV_NONE),   # 0.6s  on to the second ladder
    (0.0, -1.5,  64, CLIMB,  EV_NONE),   # 1.3s  up to the top deck
    (1.0,  0.0,  24, WALK_R, EV_NONE),   # 0.5s  to the keycard
    (0.0,  0.0,  12, STAND,  EV_NONE),   # 0.2s  a look at the card
    (0.0,  0.0,  18, STAND,  EV_KEY),    # 0.4s  ...and it is pocketed
    (1.0,  0.0,  40, WALK_R, EV_NONE),   # 0.8s  to the matching door
    (0.0,  0.0,  14, STAND,  EV_NONE),   # 0.3s  a look at the door
    (0.0,  0.0,  22, STAND,  EV_DOOR),   # 0.4s  ...and it opens
    (1.0,  0.0,  24, WALK_R, EV_NONE),   # 0.5s  and through
]

# where the steam plume stands (sprite, so it is a world position in
# tile units; it rises out of the vent nozzle on the deck below it)
STEAM_COL, STEAM_ROW = VENT_COL, DECK_A - 3


def build():
    """The set, as {(col, row): map char}.  Everything else stays black."""
    g = {}

    def put(col, row, ch):
        if 0 <= col < COLS and 0 <= row < ROWS:
            g[(col, row)] = ch

    def run(c0, c1, row, ch, step=1):
        for c in range(c0, c1 + 1, step):
            put(c, row, ch)

    def arch(col, deck):                    # 2 wide x 4 tall, on a deck
        for i, (l, r) in enumerate((('a', 'e'), ('c', 'q'),
                                    ('o', 's'), ('A', 'B'))):
            put(col, deck - 4 + i, l)
            put(col + 1, deck - 4 + i, r)

    # ---- the three floors
    run(0, COLS - 1, FLOOR_ROW, 'F')
    for c in range(0, COLS):                # deck A, with the gap he jumps
        if c not in GAP_COLS:
            put(c, DECK_A, '#')
    run(40, COLS - 1, DECK_B, '#')          # deck B only covers the top run

    # ---- the two ladders, each piercing the deck it arrives at
    for r in range(DECK_A + 1, FLOOR_ROW + 1):
        put(LAD1_COL, r, 'L')
    for r in range(DECK_B + 1, DECK_A + 1):
        put(LAD2_COL, r, 'L')

    # ---- the turbine, off to the RIGHT of the climb.  A whole one is
    # ~90 tiles at once, so it is placed where only its left-hand edge
    # ever reaches the frame -- and where the opening walk, which sees
    # much further left, never meets it at all.
    cap, stage, spacer = "1223224", "5779886", "5..0..6"
    tc, top = 28, 44
    for i, ch in enumerate(cap):
        put(tc + i, top, ch)
    for r in range(top + 1, FLOOR_ROW):
        band = stage if (r - top - 1) % 2 == 0 else spacer
        for i, ch in enumerate(band):
            if ch != '.':
                put(tc + i, r, ch)

    # ---- dressing down the shaft: a pipe out of the wall, elbowing
    # away down the wall, drips, a grate, crates on the floor
    put(26, 34, 't')
    run(24, 25, 34, 'i')
    put(23, 34, 'k')
    for r in range(35, 44):
        put(23, r, 'p')
    put(21, 37, 'l')                        # a red drip...
    put(25, 43, 'w')                        # ...and a harmless one
    put(22, 48, 'g')
    put(25, 51, 'n')
    put(24, 56, 'C')
    put(25, 56, 'C')
    put(12, 56, 'C')                        # the opening walk, dressed
    put(13, 56, 'C')
    put(16, 55, 'n')
    put(10, 54, 'g')
    put(15, FLOOR_ROW - 1, 'm')
    for r in (42, 54):
        put(27, r, 'm')

    # ---- deck A: the obstacle run.  Vent on the deck, lever past it,
    # arches and a painting to give the walk some depth.
    put(VENT_COL, DECK_A - 1, 'u')          # steam nozzle, grounded
    put(SWITCH_COL, DECK_A - 2, '!')        # the lever he throws
    arch(22, DECK_A)
    put(31, DECK_A - 3, 'x')
    arch(38, DECK_A)
    put(46, DECK_A - 3, 'z')
    put(24, DECK_A - 1, 'C')
    for col in (21, 36, 48):                # work lights under the deck
        put(col, DECK_A + 1, 'm')
    put(29, DECK_A + 1, 'v')

    # a warning stripe either side of the gap he has to jump
    for c in (GAP_COLS[0] - 1, GAP_COLS[1] + 1):
        put(c, DECK_A + 1, 'h')

    # ---- deck B: the payoff.  Card, then the door whose lintel light
    # is the same colour, with a little scenery between them.
    put(KEY_COL, DECK_B - 2, KEY_CH)
    put(DOOR_COL, DECK_B - 5, DOOR_LINTEL)
    for i in range(1, 5):
        put(DOOR_COL, DECK_B - i, 'X')
    arch(52, DECK_B)
    put(61, DECK_B - 3, 'y')
    put(70, DECK_B - 3, 'x')
    put(63, DECK_B - 1, 'C')
    put(73, DECK_B - 2, '$')
    for col in (54, 68, 76):
        put(col, DECK_B + 1, 'm')
    put(59, DECK_B + 1, 'v')

    # ---- ceilings: a pipe run high over each deck
    put(44, DECK_B - 11, 'f')
    run(45, 52, DECK_B - 11, 'i')
    put(53, DECK_B - 11, 'j')
    run(40, COLS - 1, DECK_B - 13, 'W', step=6)
    return g


def camera_track():
    """Every integer (cam_x, cam_y) the script visits, frame by frame.

    This mirrors the Z80's arithmetic exactly -- 24-bit accumulators
    stepped by a signed 8.8 fixed-point delta.  Summing the decimal
    speeds in floating point instead drifts (0.6 thirty times is not
    18.0) and would make every check here a check of the wrong thing."""
    def step(v):                       # signed 8.8, as the engine sees it
        f = fixed(v)
        return f - 65536 if f & 0x8000 else f

    x, y = CAM_X0 << 8, CAM_Y0 << 8
    out = [(CAM_X0, CAM_Y0)]
    for dx, dy, n, _a, _e in SCRIPT:
        sx, sy = step(dx), step(dy)
        for _ in range(n):
            x, y = x + sx, y + sy
            out.append((x >> 8, y >> 8))
    return out


def anim_track():
    out = []
    for _dx, _dy, n, a, _e in SCRIPT:
        out += [a] * n
    return out + [out[-1]]


def fixed(v):
    """8.8 two's complement."""
    return round(v * 256) & 0xFFFF


def visible(objs, cam):
    cx, cy = cam
    return sum(1 for _ch, col, row in objs
               if -3 <= col * 4 - cx <= 79 and -7 <= row * 8 - cy <= BAND - 1)


def hero_beats(track):
    """Where the mechanic actually is at the end of each segment."""
    out, f = [], 0
    for dx, dy, n, a, e in SCRIPT:
        f += n
        cx, cy = track[f]
        out.append((a, e, (cx + HERO_X) / 4, (cy + HERO_Y + 16) / 8))
    return out


def main(dst):
    g = build()
    objs = sorted(((ch, c, r) for (c, r), ch in g.items()),
                  key=lambda o: (o[2], o[1]))
    track = camera_track()
    index = {(c, r): i for i, (_ch, c, r) in enumerate(objs)}

    # ---- the checks that keep the demo honest and inside the budget
    peak = max(visible(objs, cam) for cam in track)
    xs = [c[0] for c in track]
    ys = [c[1] for c in track]
    assert peak <= VISIBLE_BUDGET, \
        f"{peak} objects visible at once, budget is {VISIBLE_BUDGET}"
    # the camera is 24-bit fixed point, so it only has to stay positive
    # and inside the set; object columns and rows still have to fit the
    # byte fields of a record
    assert min(xs) >= 0 and min(ys) >= 0, "the camera goes negative"
    assert max(xs) + 80 <= COLS * 4, "camera x runs off the world"
    assert max(ys) + BAND <= ROWS * 8, "camera y runs off the world"
    assert COLS <= 256 and ROWS <= 256, "a record cannot hold that column"
    assert max(xs) < 65536 and max(ys) < 65536, "camera exceeds 16 bits"
    assert (CAM_X0 + HERO_X) % 4 == 0, "he starts mid-tile"
    assert (CAM_X0 + HERO_X) // 4 < LAD1_COL, \
        "he starts on the ladder instead of walking to it"
    assert SCRIPT[0][3] == WALK_R and SCRIPT[1][3] == CLIMB, \
        "the run must open with a walk, then the climb"

    # every beat must leave him standing on the thing it is about
    beats = hero_beats(track)
    want = [
        (WALK_R, FLOOR_ROW, LAD1_COL),       # reaches the ladder's foot
        (CLIMB,  DECK_A, LAD1_COL),          # top of ladder 1
        (WALK_R, DECK_A, None),
        (JUMP,   DECK_A, None),              # cleared the gap
        (WALK_R, DECK_A, None),
        (STAND,  DECK_A, None),              # waiting out the steam
        (WALK_R, DECK_A, SWITCH_COL),        # at the lever
        (STAND,  DECK_A, SWITCH_COL),
        (STAND,  DECK_A, SWITCH_COL),        # ...throwing it
        (WALK_R, DECK_A, LAD2_COL),          # foot of ladder 2
        (CLIMB,  DECK_B, LAD2_COL),          # top of ladder 2
        (WALK_R, DECK_B, KEY_COL),           # at the card
        (STAND,  DECK_B, KEY_COL),
        (STAND,  DECK_B, KEY_COL),           # ...pocketing it
        (WALK_R, DECK_B, DOOR_COL),          # at the door
        (STAND,  DECK_B, DOOR_COL),
        (STAND,  DECK_B, DOOR_COL),          # ...opening it
        (WALK_R, DECK_B, None),              # through it
    ]
    for i, ((a, e, hc, hr), (wa, wrow, wcol)) in enumerate(zip(beats, want)):
        assert a == wa, f"beat {i}: anim {a} != {wa}"
        # CAM_Y0 is a line low on purpose so the climb lands square,
        # so his feet may sit up to a line into the deck he is on
        assert wrow <= hr <= wrow + 0.25, \
            f"beat {i}: he ends on row {hr}, wanted {wrow}"
        if wcol is not None:
            assert hc == wcol, f"beat {i}: he ends at col {hc}, wanted {wcol}"
    # the plume must rise from the nozzle, and he must be clear of it
    assert STEAM_COL == VENT_COL, "the plume is not over the vent"
    steam_seg = [i for i, sg in enumerate(SCRIPT) if sg[4] == EV_STEAM]
    assert len(steam_seg) == 1, "exactly one beat should blow steam"
    f = sum(s[2] for s in SCRIPT[:steam_seg[0] + 1])
    hero_col = (track[f][0] + HERO_X) / 4
    assert hero_col < VENT_COL - 1, \
        f"he waits at col {hero_col}, on top of the vent at {VENT_COL}"
    # the jump has to leave before the hole and land past it -- find the
    # beat by what it IS, so reordering the run cannot quietly skip this
    jumps = [i for i, sg in enumerate(SCRIPT) if sg[3] == JUMP]
    assert len(jumps) == 1, "exactly one beat should be a jump"
    ji = jumps[0]
    f0 = sum(sg[2] for sg in SCRIPT[:ji])
    a = (track[f0][0] + HERO_X) / 4
    b = (track[f0 + SCRIPT[ji][2]][0] + HERO_X) / 4
    assert a <= GAP_COLS[0], f"the jump starts at col {a}, already past the gap"
    assert b > GAP_COLS[1], f"the jump lands on col {b}, still inside the gap"
    # ...and the door must answer to the card he picked up
    assert lg.CHARMAP[DOOR_LINTEL].endswith(KEY_CH.lower()) or \
        lg.CHARMAP[DOOR_LINTEL] == "door_top_y" and KEY_CH == 'Y', \
        "the door's indicator light is not the keycard's colour"

    frames = sum(s[2] for s in SCRIPT)
    sw_i = index[(SWITCH_COL, DECK_A - 2)]
    key_i = index[(KEY_COL, DECK_B - 2)]
    # The list is sorted by row, and other scenery shares the door's
    # rows, so its five cells are NOT contiguous -- emit each offset.
    # Row order puts the lintel first, which is what the reset wants.
    door_is = [index[(DOOR_COL, DECK_B - i)] for i in range(5, 0, -1)]
    assert objs[door_is[0]][2] == DECK_B - 5, "the lintel must sort first"
    assert len({objs[i][1] for i in door_is}) == 1, "door cells drifted"

    with open(dst, "w") as f:
        w = f.write
        w("; ======================================================\n")
        w("; GENERATED by tools/demo_scene.py -- DO NOT EDIT\n")
        w("; ======================================================\n\n")
        for name, val in (("COLS", COLS), ("ROWS", ROWS), ("BAND", BAND),
                          ("HERO_X", HERO_X), ("HERO_Y", HERO_Y),
                          ("CAM_X0", CAM_X0), ("CAM_Y0", CAM_Y0),
                          ("OBJS", len(objs)),
                          ("STEAM_COL", STEAM_COL), ("STEAM_ROW", STEAM_ROW)):
            w(f"DEMO_{name:<10} equ {val}\n")
        w("; byte offsets into demo_objects of the cells the script edits\n")
        w(f"DEMO_SW_OFF    equ {sw_i * 3}\n")
        w(f"DEMO_SW_TILE   equ {lg.tile_index('switch_on')}\n")
        w(f"DEMO_KEY_OFF   equ {key_i * 3}\n")
        w("DEMO_DOOR_N    equ 5\n")
        # rasm divides in FLOATING POINT, so split the wide camera start
        # into bytes here rather than writing CAM_Y0/256 in the source
        w(f"DEMO_CAMX_LO   equ {CAM_X0 & 255}\n")
        w(f"DEMO_CAMX_HI   equ {CAM_X0 >> 8}\n")
        w(f"DEMO_CAMY_LO   equ {CAM_Y0 & 255}\n")
        w(f"DEMO_CAMY_HI   equ {CAM_Y0 >> 8}\n")
        w(f"DEMO_SW_OFF_TILE equ {lg.tile_index('switch_off')}\n")
        w(f"DEMO_KEY_TILE  equ {lg.tile_index(lg.CHARMAP[KEY_CH])}\n")
        w(f"DEMO_DOOR_TILE equ {lg.tile_index('door_body')}\n")
        w(f"DEMO_LINTEL_TILE equ {lg.tile_index(lg.CHARMAP[DOOR_LINTEL])}\n\n")
        w("; the door's cells, lintel first -- they are not adjacent\n")
        w("demo_door_offs:\n")
        w(f"        defw {','.join(str(i * 3) for i in door_is)}\n\n")
        w("; (tile, column, row), sorted by row\ndemo_objects:\n")
        for ch, c, r in objs:
            w(f"        defb {lg.tile_index(lg.CHARMAP[ch]):3},{c:3},{r:3}"
              f"   ; '{ch}'\n")

        w("\n; first object of each world row, as an offset into the\n")
        w("; list above -- the engine never touches rows off the band\n")
        w("demo_row_ptr:\n")
        starts, k = [], 0
        for r in range(ROWS + 1):
            while k < len(objs) and objs[k][2] < r:
                k += 1
            starts.append(k * 3)
        for r in range(0, ROWS + 1, 8):
            w(f"        defw {','.join(str(v) for v in starts[r:r + 8])}\n")

        w("\n; per segment: dx, dy (8.8 per frame), frames, anim, event\n")
        w("; anim 0 climb 1 right 2 left 3 jump 4 stand\n")
        w("; event 0 none 1 switch 2 key 3 door\ndemo_script:\n")
        for dx, dy, n, a, e in SCRIPT:
            w(f"        defw #{fixed(dx):04X},#{fixed(dy):04X}\n")
            w(f"        defb {n},{a},{e}\n")
        w("        defw 0,0\n        defb 0,#FF,0    ; end of script\n")

    print(f"{dst}: {len(objs)} objects, peak {peak} visible "
          f"(budget {VISIBLE_BUDGET}), {frames} frames = {frames / 50:.1f}s",
          file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "src/demo_scene.asm")
