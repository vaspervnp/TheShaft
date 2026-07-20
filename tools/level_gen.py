#!/usr/bin/env python3
"""level_gen.py -- ASCII tile art + level map -> rasm source for The Shaft.

Usage:
    python3 tools/level_gen.py > src/level01.asm

Emits three assets from one authored source (this file):
  * tileset    -- 8x8-pixel tiles as raw Mode 0 bytes (4 bytes x 8 lines)
  * tilemap01  -- the 20x25 map, one tile index per byte, row-major
  * level_rects -- the collision geometry COMPILED from the map:
       solid tiles greedy-merged into rectangles,
       each ladder = the bounding box of its vertical tile column.
    So the map is the single source of truth; the Z80 collision code
    (box_overlap / probe_level / find_ladder) is untouched.

Authoring rules:
  * Tile art: 8 rows x 8 chars, one hex digit per pixel, '.' = pen 0.
  * Map: 25 rows x 20 chars, one char per tile (see CHARMAP).
  * Platform surfaces land on tile rows, so y is always a multiple of
    8 -- the climb code's even-y rule is satisfied for free.
  * A ladder column must include 2 rows of rail tiles ABOVE the upper
    platform surface (the climb-volume head room) and end on the row
    just above the lower floor.
"""
import sys

EMPTY, SOLID, LADDER = 0, 1, 2
TYPE_NAME = {SOLID: "TYPE_SOLID", LADDER: "TYPE_LADDER"}

# ----------------------------------------------------------------------
# Tile art.  Palette pens: 0 black, 1 bright white, 2 grey, 3 blue,
# 4 sky blue, 5 bright red, 6 orange, 7 bright yellow.
# ----------------------------------------------------------------------
TILES = [
    ("empty", EMPTY, [
        "........",
        "........",
        "........",
        "........",
        "........",
        "........",
        "........",
        "........"]),
    ("wall", SOLID, [                    # riveted steel strip
        "12222233",
        "12222233",
        "12212233",
        "12222233",
        "12222233",
        "12212233",
        "12222233",
        "12222233"]),
    ("slab", SOLID, [                    # platform deck plate
        "11111111",
        "22222222",
        "22222222",
        "32323232",
        "22222222",
        "22222222",
        "33333333",
        "........"]),
    ("floor", SOLID, [                   # machine-deck floor, worn tread
        "11111111",
        "22662266",
        "26622662",
        "66226622",
        "22222222",
        "32323232",
        "22222222",
        "33333333"]),
    ("ladder", LADDER, [                 # orange rails + rungs
        ".6....6.",
        ".6....6.",
        ".666666.",
        ".6....6.",
        ".6....6.",
        ".666666.",
        ".6....6.",
        ".6....6."]),
    ("crate", SOLID, [                   # X-braced wooden crate
        "76666667",
        "63666636",
        "66366366",
        "66633666",
        "66633666",
        "66366366",
        "63666636",
        "76666667"]),
    ("pipe", EMPTY, [                    # background coolant pipe
        ".34413..",
        ".34413..",
        ".34413..",
        ".33333..",
        ".34413..",
        ".34413..",
        ".34413..",
        ".34413.."]),
    ("hazard", EMPTY, [                  # warning stripes (decor)
        "66..66..",
        "6..66..6",
        "..66..66",
        ".66..66.",
        "66..66..",
        "6..66..6",
        "..66..66",
        ".66..66."]),
]

CHARMAP = {'.': "empty", 'W': "wall", '#': "slab", 'F': "floor",
           'L': "ladder", 'C': "crate", 'p': "pipe", 'h': "hazard"}

# ----------------------------------------------------------------------
# Level 01 -- the bottom machine deck.  Same geometry the collision
# demo used: shaft walls, floor, a crate, three platforms joined by
# three offset ladders zig-zagging towards the top of the screen.
# ----------------------------------------------------------------------
LEVEL01 = [
    "W.p................W",
    "W.p................W",
    "W.p................W",
    "W.p................W",
    "W.p............L...W",
    "W..............L...W",
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

MAP_W, MAP_H = 20, 25


def left_pixel(pen):          # same Mode 0 encoding as sprite_gen.py
    return ((pen & 1) << 7) | ((pen & 2) << 2) | ((pen & 4) << 3) | ((pen & 8) >> 2)


def right_pixel(pen):
    return left_pixel(pen) >> 1


def encode_art_row(row):
    """8 pixels -> 4 raw Mode 0 bytes (no mask: tiles are opaque)."""
    out = []
    for i in range(0, 8, 2):
        pl = 0 if row[i] == '.' else int(row[i], 16)
        pr = 0 if row[i + 1] == '.' else int(row[i + 1], 16)
        out.append(left_pixel(pl) | right_pixel(pr))
    return out


def validate():
    assert len(LEVEL01) == MAP_H, "map must be 25 rows"
    for r, row in enumerate(LEVEL01):
        assert len(row) == MAP_W, f"row {r} is {len(row)} chars, want {MAP_W}"
        for ch in row:
            assert ch in CHARMAP, f"unknown map char {ch!r} in row {r}"
    for name, _t, art in TILES:
        assert len(art) == 8 and all(len(a) == 8 for a in art), f"bad art: {name}"


def tile_index(name):
    return next(i for i, (n, _t, _a) in enumerate(TILES) if n == name)


def tile_type(ch):
    return TILES[tile_index(CHARMAP[ch])][1]


def solid_rects():
    """Greedy-merge solid tiles into rectangles: identical horizontal
    runs on consecutive rows fuse vertically.  Partitioning may differ
    from hand-authored rects but the covered area is identical."""
    def runs(row):
        out, c = [], 0
        while c < MAP_W:
            if tile_type(LEVEL01[row][c]) == SOLID:
                c0 = c
                while c < MAP_W and tile_type(LEVEL01[row][c]) == SOLID:
                    c += 1
                out.append((c0, c - 1))
            else:
                c += 1
        return out

    rects, active = [], {}          # active: (c0,c1) -> start_row
    for r in range(MAP_H + 1):
        now = set(runs(r)) if r < MAP_H else set()
        for key in [k for k in active if k not in now]:
            rects.append((key[0], key[1], active.pop(key), r - 1))
        for key in now:
            active.setdefault(key, r)
    return sorted(rects, key=lambda t: (t[2], t[0]))


def ladder_rects():
    """Each ladder is a vertical run of ladder tiles, one tile wide."""
    rects = []
    for c in range(MAP_W):
        r = 0
        while r < MAP_H:
            if tile_type(LEVEL01[r][c]) == LADDER:
                r0 = r
                while r < MAP_H and tile_type(LEVEL01[r][c]) == LADDER:
                    r += 1
                rects.append((c, c, r0, r - 1))
            else:
                r += 1
    return rects


def emit():
    print("; ======================================================================")
    print("; GENERATED by tools/level_gen.py -- DO NOT EDIT, edit the tool instead")
    print("; Tileset + tilemap + collision rects compiled from one ASCII source.")
    print("; ======================================================================")
    print()
    print(f"; --- tileset: {len(TILES)} tiles x 32 bytes (4 bytes x 8 lines, raw Mode 0)")
    print("tileset:")
    for i, (name, ttype, art) in enumerate(TILES):
        tname = {EMPTY: "walk-through", SOLID: "SOLID", LADDER: "LADDER"}[ttype]
        print(f"; tile {i}: {name} ({tname})")
        for row in art:
            bs = ", ".join(f"#{b:02X}" for b in encode_art_row(row))
            print(f"        defb {bs}   ; {row}")
    print()
    print("; --- tilemap: 20x25 tile indices, row-major from the top left")
    print("tilemap01:")
    for row in LEVEL01:
        idx = ",".join(str(tile_index(CHARMAP[ch])) for ch in row)
        print(f"        defb {idx}   ; {row}")
    print()
    print("; --- collision rects compiled from the map (x,w in bytes; y,h in lines)")
    print("level_rects:")
    for c0, c1, r0, r1 in solid_rects():
        print(f"        defb {c0*4:3},{(c1-c0+1)*4:3},{r0*8:4},{(r1-r0+1)*8:4}, TYPE_SOLID")
    for c0, c1, r0, r1 in ladder_rects():
        print(f"        defb {c0*4:3},{(c1-c0+1)*4:3},{r0*8:4},{(r1-r0+1)*8:4}, TYPE_LADDER")
    print("        defb #FF                ; end of list")


if __name__ == "__main__":
    validate()
    emit()
    ns, nl = len(solid_rects()), len(ladder_rects())
    print(f"level01: {ns} solid rects, {nl} ladders, "
          f"{len(TILES)*32 + MAP_W*MAP_H + (ns+nl)*5 + 1} bytes total",
          file=sys.stderr)
