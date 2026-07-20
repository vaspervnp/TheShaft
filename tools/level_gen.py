#!/usr/bin/env python3
"""level_gen.py -- ASCII tile art + level maps -> rasm source for The Shaft.

Usage:
    python3 tools/level_gen.py > src/levels.asm

Emits, all from the authored data in this file:
  * tileset      -- 8x8 tiles as raw Mode 0 bytes (4 bytes x 8 lines)
  * font_8x8     -- 38 glyphs (0-9 A-Z '-' space) as 1-bit bitmaps,
                    bit 7 = leftmost pixel.  Glyphs 0..15 double as the
                    hex digits, so the HUD prints a value directly.
  * pen_left     -- pen -> left-pixel Mode 0 bits (right = value >> 1)
  * menu_map     -- 20x25 backdrop for the title menu (main RAM)
  * levels_blob  -- one self-contained blob per level, copied into
                    extra-RAM bank 4 at boot and fetched per level:
                        +0  word: offset of collision rects
                        +2  word: offset of drone spawn list
                        +4  the 20x25 tilemap (500 bytes)
                        ... rects (x,w,y,h,type)*, #FF terminator
                        ... drones: count, then (x,y,speed,xmin,xmax)*
                    All header offsets are label arithmetic -- the
                    assembler does the math, this tool never counts.
  * level_table  -- per level: word offset-in-bank, word length

Map characters:
  .  empty        W wall (solid)    # platform slab (solid)
  F  floor        C crate (solid)   L ladder
  p  pipe (deco)  h hazard (deco)   v vine (deco)
  K  keycard tile (pick up: tile turns empty, KeycardsHeld++)
  X  security door (its own SOLID|DOOR rect; opens with a keycard)
  D  drone spawn (empty tile; patrol = the clear run on its two rows)

Authoring rules (as before): platform surfaces on tile rows; a ladder
column includes 2 rows of rail head-room above the surface it serves
and ends just above the lower floor.  New rules: the exit ladder of
level N and the entry ladder of level N+1 must share a column (the
player keeps X across the transition), doors are 1 column x 2 rows
sitting on a walkway, keycards sit at body height on a walkway.
"""
import sys

EMPTY, SOLID, LADDER, DOOR = 0, 1, 2, 4
TYPE_NAME = {SOLID: "TYPE_SOLID", LADDER: "TYPE_LADDER",
             SOLID | DOOR: "TYPE_SOLID+TYPE_DOOR"}

# ----------------------------------------------------------------------
# Tile art.  Pens: 0 black, 1 bright white, 2 grey, 3 blue, 4 sky blue,
# 5 bright red, 6 orange, 7 bright yellow, 9 bright green, a bright
# cyan, d green.
# ----------------------------------------------------------------------
TILES = [
    ("empty", EMPTY, [
        "........", "........", "........", "........",
        "........", "........", "........", "........"]),
    ("wall", SOLID, [
        "12222233", "12222233", "12212233", "12222233",
        "12222233", "12212233", "12222233", "12222233"]),
    ("slab", SOLID, [
        "11111111", "22222222", "22222222", "32323232",
        "22222222", "22222222", "33333333", "........"]),
    ("floor", SOLID, [
        "11111111", "22662266", "26622662", "66226622",
        "22222222", "32323232", "22222222", "33333333"]),
    ("ladder", LADDER, [
        ".6....6.", ".6....6.", ".666666.", ".6....6.",
        ".6....6.", ".666666.", ".6....6.", ".6....6."]),
    ("crate", SOLID, [
        "76666667", "63666636", "66366366", "66633666",
        "66633666", "66366366", "63666636", "76666667"]),
    ("pipe", EMPTY, [
        ".34413..", ".34413..", ".34413..", ".33333..",
        ".34413..", ".34413..", ".34413..", ".34413.."]),
    ("hazard", EMPTY, [
        "66..66..", "6..66..6", "..66..66", ".66..66.",
        "66..66..", "6..66..6", "..66..66", ".66..66."]),
    ("keycard", EMPTY, [                  # index 8 = TILE_KEYCARD
        "........", "........", ".999999.", ".911119.",
        ".999999.", ".99..99.", "........", "........"]),
    ("door", DOOR, [                      # index 9 = TILE_DOOR
        "25555552", "25577552", "25577552", "25555552",
        "25555552", "25577552", "25577552", "25555552"]),
    ("vine", EMPTY, [
        ".d......", ".dd.....", "..d..d..", "..dd.dd.",
        "...d.d..", "...dd...", "....d...", "....d..."]),
]

CHARMAP = {'.': "empty", 'W': "wall", '#': "slab", 'F': "floor",
           'L': "ladder", 'C': "crate", 'p': "pipe", 'h': "hazard",
           'K': "keycard", 'X': "door", 'v': "vine", 'D': "empty"}

# ----------------------------------------------------------------------
# 5x7 font (classic LCD shapes), rendered into bits 6..2 of each row.
# Order: 0-9, A-Z, '-', space  ->  glyph index 0..37.
# ----------------------------------------------------------------------
FONT_ORDER = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ- "
GLYPHS = {
    '0': [0x0E,0x11,0x13,0x15,0x19,0x11,0x0E], '1': [0x04,0x0C,0x04,0x04,0x04,0x04,0x0E],
    '2': [0x0E,0x11,0x01,0x02,0x04,0x08,0x1F], '3': [0x1F,0x02,0x04,0x02,0x01,0x11,0x0E],
    '4': [0x02,0x06,0x0A,0x12,0x1F,0x02,0x02], '5': [0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E],
    '6': [0x06,0x08,0x10,0x1E,0x11,0x11,0x0E], '7': [0x1F,0x01,0x02,0x04,0x08,0x08,0x08],
    '8': [0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E], '9': [0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C],
    'A': [0x0E,0x11,0x11,0x1F,0x11,0x11,0x11], 'B': [0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E],
    'C': [0x0E,0x11,0x10,0x10,0x10,0x11,0x0E], 'D': [0x1C,0x12,0x11,0x11,0x11,0x12,0x1C],
    'E': [0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F], 'F': [0x1F,0x10,0x10,0x1E,0x10,0x10,0x10],
    'G': [0x0E,0x11,0x10,0x17,0x11,0x11,0x0F], 'H': [0x11,0x11,0x11,0x1F,0x11,0x11,0x11],
    'I': [0x0E,0x04,0x04,0x04,0x04,0x04,0x0E], 'J': [0x07,0x02,0x02,0x02,0x02,0x12,0x0C],
    'K': [0x11,0x12,0x14,0x18,0x14,0x12,0x11], 'L': [0x10,0x10,0x10,0x10,0x10,0x10,0x1F],
    'M': [0x11,0x1B,0x15,0x15,0x11,0x11,0x11], 'N': [0x11,0x11,0x19,0x15,0x13,0x11,0x11],
    'O': [0x0E,0x11,0x11,0x11,0x11,0x11,0x0E], 'P': [0x1E,0x11,0x11,0x1E,0x10,0x10,0x10],
    'Q': [0x0E,0x11,0x11,0x11,0x15,0x12,0x0D], 'R': [0x1E,0x11,0x11,0x1E,0x14,0x12,0x11],
    'S': [0x0F,0x10,0x10,0x0E,0x01,0x01,0x1E], 'T': [0x1F,0x04,0x04,0x04,0x04,0x04,0x04],
    'U': [0x11,0x11,0x11,0x11,0x11,0x11,0x0E], 'V': [0x11,0x11,0x11,0x11,0x11,0x0A,0x04],
    'W': [0x11,0x11,0x11,0x15,0x15,0x15,0x0A], 'X': [0x11,0x11,0x0A,0x04,0x0A,0x11,0x11],
    'Y': [0x11,0x11,0x11,0x0A,0x04,0x04,0x04], 'Z': [0x1F,0x01,0x02,0x04,0x08,0x10,0x1F],
    '-': [0x00,0x00,0x00,0x1F,0x00,0x00,0x00], ' ': [0x00,0x00,0x00,0x00,0x00,0x00,0x00],
}

# ----------------------------------------------------------------------
# The levels.  Bottom of the shaft first; climb order 1 -> 2 -> 3.
# ----------------------------------------------------------------------
LEVEL1 = [                       # Machine Deck (exit ladder col 8)
    "W.p.....L..........W",
    "W.p.....L..........W",
    "W.p.....L..........W",
    "W.p.....L..........W",
    "W.p.....L......L...W",
    "W.......L......L...W",
    "W##############L###W",
    "W..............L...W",
    "W..............L...W",
    "W..............L...W",
    "W..L...........L...W",
    "W..L...........L...W",
    "W##L###############W",
    "W..L.............p.W",
    "W..L.............p.W",
    "W..L.............p.W",
    "W..L......L......p.W",
    "W..L......L......p.W",
    "W#########L########W",
    "W.........L........W",
    "W.........L........W",
    "W.........L........W",
    "W....CC...L........W",
    "Wh...CC...L.......hW",
    "FFFFFFFFFFFFFFFFFFFF",
]
LEVEL2 = [                       # Hydroponics (entry col 8, exit col 12)
    "W...........L......W",
    "W...........L......W",
    "W.v.........L....v.W",
    "W.v.........L....v.W",
    "W.v.D.......L..L.v.W",
    "W...........L..L...W",
    "W##############L###W",
    "W..............L...W",
    "W.....v........L.v.W",
    "W.....v........L.v.W",
    "W..L........X..L...W",
    "W..L........X..L...W",
    "W##L###############W",
    "W..L.........v.....W",
    "W..L.........v.....W",
    "W..L...............W",
    "W..L.D..L.......K..W",
    "W..L....L..........W",
    "W#######L##########W",
    "W.......L..........W",
    "W.......L..........W",
    "W.......L..........W",
    "W.......L..........W",
    "W.......L....v.....W",
    "FFFFFFFFFFFFFFFFFFFF",
]
LEVEL3 = [                       # Upper Works (entry col 12, exit col 7)
    "W......L...........W",
    "W......L...........W",
    "W......L.......p...W",
    "W......L.......p...W",
    "W......L.........L.W",
    "W......L.........L.W",
    "W################L#W",
    "W................L.W",
    "W................L.W",
    "W................L.W",
    "W.L..D...........L.W",
    "W.L..............L.W",
    "W#L################W",
    "W.L................W",
    "W.L................W",
    "W.L................W",
    "W.L...X..D..L....K.W",
    "W.L...X.....L......W",
    "W###########L######W",
    "W...........L......W",
    "W...........L......W",
    "W...........L......W",
    "W...........L......W",
    "W...........L......W",
    "FFFFFFFFFFFFFFFFFFFF",
]
LEVELS = [LEVEL1, LEVEL2, LEVEL3]

MENU = [                         # title-screen backdrop; text rows stay black
    "hhhhhhhhhhhhhhhhhhhh",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "....................",
    "..p..............p..",
    "..p..............p..",
    "..p.....CCCC.....p..",
    "..p.....CCCC.....p..",
    "..p.....CCCC.....p..",
    "..p.....CCCC.....p..",
    "..p..............p..",
    "....................",
    "FFFFFFFFFFFFFFFFFFFF",
]

MAP_W, MAP_H = 20, 25


def left_pixel(pen):
    return ((pen & 1) << 7) | ((pen & 2) << 2) | ((pen & 4) << 3) | ((pen & 8) >> 2)


def right_pixel(pen):
    return left_pixel(pen) >> 1


def encode_art_row(row):
    out = []
    for i in range(0, 8, 2):
        pl = 0 if row[i] == '.' else int(row[i], 16)
        pr = 0 if row[i + 1] == '.' else int(row[i + 1], 16)
        out.append(left_pixel(pl) | right_pixel(pr))
    return out


def tile_index(name):
    return next(i for i, (n, _t, _a) in enumerate(TILES) if n == name)


def cell_type(level, r, c):
    return TILES[tile_index(CHARMAP[level[r][c]])][1]


def validate(level, name):
    assert len(level) == MAP_H, f"{name}: need {MAP_H} rows"
    for r, row in enumerate(level):
        assert len(row) == MAP_W, f"{name} row {r}: {len(row)} chars"
        for ch in row:
            assert ch in CHARMAP, f"{name} row {r}: unknown char {ch!r}"


def solid_rects(level):
    """Greedy row-merge of SOLID cells (doors excluded: they are their
    own dynamic rects)."""
    def runs(r):
        out, c = [], 0
        while c < MAP_W:
            if cell_type(level, r, c) == SOLID:
                c0 = c
                while c < MAP_W and cell_type(level, r, c) == SOLID:
                    c += 1
                out.append((c0, c - 1))
            else:
                c += 1
        return out
    rects, active = [], {}
    for r in range(MAP_H + 1):
        now = set(runs(r)) if r < MAP_H else set()
        for key in [k for k in active if k not in now]:
            rects.append((key[0], key[1], active.pop(key), r - 1, SOLID))
        for key in now:
            active.setdefault(key, r)
    return sorted(rects, key=lambda t: (t[2], t[0]))


def column_runs(level, want):
    """Vertical runs of a given type, one column wide."""
    out = []
    for c in range(MAP_W):
        r = 0
        while r < MAP_H:
            if cell_type(level, r, c) == want:
                r0 = r
                while r < MAP_H and cell_type(level, r, c) == want:
                    r += 1
                out.append((c, c, r0, r - 1, want if want != DOOR else SOLID | DOOR))
            else:
                r += 1
    return out


def drones(level):
    """'D' cells: y from the row, patrol = clear run on rows r..r+1
    (solids and doors bound it; ladders/deco/keycards do not)."""
    out = []
    for r, row in enumerate(level):
        for c, ch in enumerate(row):
            if ch != 'D':
                continue
            def clear(cc):
                return all(cell_type(level, rr, cc) in (EMPTY, LADDER)
                           for rr in (r, r + 1))
            c0 = c
            while c0 > 0 and clear(c0 - 1):
                c0 -= 1
            c1 = c
            while c1 < MAP_W - 1 and clear(c1 + 1):
                c1 += 1
            out.append((c * 4, r * 8, 1, c0 * 4, c1 * 4))  # x,y,speed,xmin,xmax
    return out


def ladder_cols(level, row):
    return [c for c in range(MAP_W) if level[row][c] == 'L']


def check_links():
    """Exit ladder column of level N must be an entry ladder of N+1."""
    for i in range(len(LEVELS) - 1):
        exits = ladder_cols(LEVELS[i], 0)
        entries = ladder_cols(LEVELS[i + 1], MAP_H - 2)
        assert exits, f"level {i+1} has no exit ladder at row 0"
        for e in exits:
            assert e in entries, \
                f"level {i+1} exit col {e} has no matching entry ladder in level {i+2}"
    assert ladder_cols(LEVELS[-1], 0), "last level needs a top exit (the WIN)"


def emit_map(level, label, comment):
    print(f"{label}:   ; {comment}")
    for row in level:
        idx = ",".join(str(tile_index(CHARMAP[ch])) for ch in row)
        print(f"        defb {idx}   ; {row}")


def emit_rects(rects):
    for c0, c1, r0, r1, t in rects:
        print(f"        defb {c0*4:3},{(c1-c0+1)*4:3},{r0*8:4},{(r1-r0+1)*8:4}, {TYPE_NAME[t]}")
    print("        defb #FF")


def emit():
    print("; ======================================================================")
    print("; GENERATED by tools/level_gen.py -- DO NOT EDIT, edit the tool instead")
    print("; ======================================================================")
    print()
    print(f"; --- tileset: {len(TILES)} tiles x 32 bytes ---")
    print("tileset:")
    for i, (name, ttype, art) in enumerate(TILES):
        print(f"; tile {i}: {name}")
        for row in art:
            bs = ", ".join(f"#{b:02X}" for b in encode_art_row(row))
            print(f"        defb {bs}   ; {row}")
    print()
    print("; --- 8x8 font: 1 bit/pixel, bit 7 = leftmost; 0-9 A-Z '-' ' ' ---")
    print("; glyphs 0..15 are the hex digits, so HUD values print directly")
    print("font_8x8:")
    for ch in FONT_ORDER:
        rows = [v << 2 for v in GLYPHS[ch]] + [0]
        bs = ",".join(f"#{b:02X}" for b in rows)
        print(f"        defb {bs}   ; '{ch}'")
    print()
    print("; --- pen -> Mode 0 left-pixel bits (right pixel = value >> 1) ---")
    print("pen_left:")
    bs = ",".join(f"#{left_pixel(p):02X}" for p in range(16))
    print(f"        defb {bs}")
    print()
    emit_map(MENU, "menu_map", "title-screen backdrop (lives in main RAM)")
    print()
    print("; --- level blobs: copied to extra-RAM bank 4 at boot ---")
    print("levels_blob:")
    for n, level in enumerate(LEVELS, 1):
        rects = solid_rects(level) + column_runs(level, DOOR) + column_runs(level, LADDER)
        ds = drones(level)
        print(f"lvl{n}_start:")
        print(f"        defw lvl{n}_rects-lvl{n}_start   ; header: rects offset")
        print(f"        defw lvl{n}_drones-lvl{n}_start  ; header: drone list offset")
        emit_map(level, f"lvl{n}_map", f"level {n}")
        print(f"lvl{n}_rects:")
        emit_rects(rects)
        print(f"lvl{n}_drones:")
        print(f"        defb {len(ds)}")
        for x, y, spd, xmin, xmax in ds:
            print(f"        defb {x:3},{y:4},{spd},{xmin:3},{xmax:3}   ; x,y,speed,xmin,xmax")
        print(f"lvl{n}_end:")
    print("levels_blob_end:")
    print()
    print("; --- where each level lives inside the bank ---")
    print("level_table:")
    for n in range(1, len(LEVELS) + 1):
        print(f"        defw lvl{n}_start-levels_blob, lvl{n}_end-lvl{n}_start")
    print()
    print(f"LEVEL_COUNT     equ {len(LEVELS)}")
    print("LEVELS_BLOB_LEN equ levels_blob_end-levels_blob")
    print(f"TILE_EMPTY      equ 0")
    print(f"TILE_KEYCARD    equ {tile_index('keycard')}")
    print(f"TILE_DOOR       equ {tile_index('door')}")


if __name__ == "__main__":
    for i, lv in enumerate(LEVELS, 1):
        validate(lv, f"level{i}")
    validate(MENU, "menu")
    check_links()
    emit()
    for i, lv in enumerate(LEVELS, 1):
        ds = drones(lv)
        ks = sum(row.count('K') for row in lv)
        xs = sum(1 for r in range(MAP_H) for c in range(MAP_W)
                 if lv[r][c] == 'X' and (r == 0 or lv[r-1][c] != 'X'))
        print(f"level {i}: {len(solid_rects(lv))} solids, "
              f"{len(column_runs(lv, LADDER))} ladders, {len(ds)} drones, "
              f"{ks} keycards, {xs} doors, patrols {[(d[3],d[4]) for d in ds]}",
              file=sys.stderr)
