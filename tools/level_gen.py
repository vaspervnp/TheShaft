#!/usr/bin/env python3
"""level_gen.py -- procedural level factory + asset generator for The Shaft.

Usage:
    python3 tools/level_gen.py src/levels.asm build

Produces:
  * src/levels.asm  -- tileset, font, pen table, menu map, zone
                       palettes, level_table (bank/offset/length per
                       level), constants
  * build/levels0.bin, levels1.bin, levels2.bin -- raw level blobs,
    one file per 16K extra-RAM bank.  The BASIC loader pages banks
    4/5/6 in and LOADs these straight into #4000 while the title
    screen is showing; the game then fetches one level at a time.

THE SHAFT has three zones, 59 levels total:
    1-19   Mechanical  (the machine decks; riot guards)
    20-39  Agricultural (hydroponics terraces; throwers + coats)
    40-59  Administrative (the elite levels; everything, faster)

Levels 2..59 are generated procedurally from a seeded RNG around a
fixed climb skeleton (floor + three platforms + top exit), then every
single level is VERIFIED completable by an built-in playthrough
simulator -- generation fails the build if a level can't be finished.
Level 1 stays hand-authored as the crafted opening screen.

Level blob layout (unchanged from the banked design):
    +0 word rects offset, +2 word enemies offset, +4 tilemap[500],
    rects (x,w,y,h,type)* #FF, enemies: count, (type,x,y,a,b,c)*
Enemy records: type 1 riot / 2 coat: a=speed, b=xmin, c=xmax
               type 3 thrower:      a=interval, b=phase, c=0
"""
import random
import sys


def difficulty_cap(i):
    return i // 4

EMPTY, SOLID, LADDER, DOOR = 0, 1, 2, 4
MAP_W, MAP_H = 20, 25
ZONES = ("MECHANICAL", "AGRICULTURAL", "ADMINISTRATIVE")
ZONE_SIZE = (19, 20, 20)                 # levels per zone -> 59 total
BANK_SIZE = 16384
ET_RIOT, ET_COAT, ET_THROW = 1, 2, 3

# ----------------------------------------------------------------------
# Tiles (shared; zones are recoloured via their palettes)
# ----------------------------------------------------------------------
TILES = [
    ("empty", EMPTY, ["........"]*8),
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
    # keycards and doors come in three matching colours; the key tiles
    # and door tiles are CONSECUTIVE so the engine indexes by colour
    ("keycard_g", EMPTY, [
        "........", "........", ".999999.", ".911119.",
        ".999999.", ".99..99.", "........", "........"]),
    ("keycard_c", EMPTY, [
        "........", "........", ".aaaaaa.", ".a1111a.",
        ".aaaaaa.", ".aa..aa.", "........", "........"]),
    ("keycard_y", EMPTY, [
        "........", "........", ".777777.", ".711117.",
        ".777777.", ".77..77.", "........", "........"]),
    ("keycard_w", EMPTY, [
        "........", "........", ".111111.", ".133331.",
        ".111111.", ".11..11.", "........", "........"]),
    ("keycard_r", EMPTY, [
        "........", "........", ".555555.", ".511115.",
        ".555555.", ".55..55.", "........", "........"]),
    ("door_g", DOOR, [
        "29999992", "29911992", "29911992", "29999992",
        "29999992", "29911992", "29911992", "29999992"]),
    ("door_c", DOOR, [
        "2aaaaaa2", "2aa11aa2", "2aa11aa2", "2aaaaaa2",
        "2aaaaaa2", "2aa11aa2", "2aa11aa2", "2aaaaaa2"]),
    ("door_y", DOOR, [
        "27777772", "27711772", "27711772", "27777772",
        "27777772", "27711772", "27711772", "27777772"]),
    ("door_w", DOOR, [
        "21111112", "21133112", "21133112", "21111112",
        "21111112", "21133112", "21133112", "21111112"]),
    ("door_r", DOOR, [
        "25555552", "25511552", "25511552", "25555552",
        "25555552", "25511552", "25511552", "25555552"]),
    ("vine", EMPTY, [
        ".d......", ".dd.....", "..d..d..", "..dd.dd.",
        "...d.d..", "...dd...", "....d...", "....d..."]),
    ("lamp", EMPTY, [                    # hanging work light
        "...22...", "...22...", "..2222..", ".777777.",
        "..7777..", "...77...", "........", "........"]),
    ("grate", EMPTY, [                   # ventilation grate
        "22222222", ".323232.", ".232323.", ".323232.",
        ".232323.", "22222222", "........", "........"]),
    ("bush", EMPTY, [                    # hydroponic planter
        "..d.dd..", ".dddddd.", "dddddddd", ".dddddd.",
        "...66...", "..6666..", "........", "........"]),
    ("panel", EMPTY, [                   # wall terminal
        "11111111", "1aaaaaa1", "1a4a4aa1", "1aaaaaa1",
        "11111111", "...22...", "..2..2..", "........"]),
    ("elevator", EMPTY, [                # lift door (stacked x2 rows)
        "27777772", "22233222", "22233222", "22233222",
        "22233222", "22233222", "22233222", "27777772"]),
    ("medkit", EMPTY, [                  # medical crate: 1-4 energy
        "11111111", "11155111", "11155111", "15555551",
        "15555551", "11155111", "11155111", "11111111"]),
    ("leak_red", EMPTY, [                # ceiling pipe, lubricant drip
        "33333333", "44444443", "33333333", "...55...",
        "....5...", "........", "........", "........"]),
    ("leak_white", EMPTY, [              # same pipe, harmless drip
        "33333333", "44444443", "33333333", "...11...",
        "....1...", "........", "........", "........"]),
    ("switch_off", EMPTY, [              # wall switch, lever down (red)
        "..2222..", ".211112.", ".215512.", ".215512.",
        ".211112.", "..2222..", "...22...", "...22..."]),
    ("switch_on", EMPTY, [               # pressed: lever up (green)
        "..2222..", ".211112.", ".219912.", ".219912.",
        ".211112.", "..2222..", "...22...", "...22..."]),
    ("vault", EMPTY, [                   # sealed key vault, grounded
        "11111111", "12222221", "12266221", "12266221",
        "12222221", "12222221", "12222221", "11111111"]),
]
CHARMAP = {'.': "empty", 'W': "wall", '#': "slab", 'F': "floor",
           'L': "ladder", 'C': "crate", 'p': "pipe", 'h': "hazard",
           'K': "keycard_g", 'J': "keycard_c", 'Y': "keycard_y",
           'U': "keycard_w", 'O': "keycard_r",
           'X': "door_g", 'Z': "door_c", 'Q': "door_y",
           'V': "door_w", 'N': "door_r",
           'v': "vine",
           'm': "lamp", 'g': "grate", 'b': "bush", 'n': "panel",
           'E': "elevator", 'M': "medkit",
           'l': "leak_red", 'w': "leak_white",
           '!': "switch_off", '$': "vault",
           'R': "empty", 'G': "empty", 'T': "empty"}   # enemy markers

# ----------------------------------------------------------------------
# Zone palettes.  Pens 0-3,5,7,8 stay fixed (player sprite, UI, steel);
# the rest recolour ladders, crates and deco per zone.
# ----------------------------------------------------------------------
PALETTES = [
    # 0 MECHANICAL: rust and sodium light (the original look)
    [0x54,0x4B,0x40,0x44,0x57,0x4C,0x4E,0x4A,0x47,0x52,0x53,0x45,0x5C,0x56,0x5E,0x5F],
    # 1 AGRICULTURAL: green growth, warm grow-lamps
    [0x54,0x4B,0x40,0x44,0x43,0x4C,0x52,0x4A,0x47,0x52,0x53,0x5A,0x5C,0x56,0x59,0x42],
    # 2 ADMINISTRATIVE: cold blue glass and elite purple
    [0x54,0x4B,0x40,0x44,0x4F,0x4C,0x55,0x4A,0x47,0x52,0x4D,0x45,0x5C,0x45,0x5F,0x4F],
]

# ----------------------------------------------------------------------
# 5x7 font, bits 6..2 of each row; order 0-9 A-Z '-' space
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
    '-': [0x00,0x00,0x00,0x1F,0x00,0x00,0x00], ' ': [0x00]*7,
    # HUD icons ride along as extra glyphs (38 key, 39 heart, 40 bolt)
    'KEY':   [0x0C,0x12,0x12,0x0C,0x04,0x05,0x06],
    'HEART': [0x0A,0x1F,0x1F,0x0E,0x04,0x00,0x00],
    'BOLT':  [0x03,0x06,0x0C,0x1E,0x06,0x0C,0x08],
}
FONT_EXTRA = ['KEY', 'HEART', 'BOLT']

# ----------------------------------------------------------------------
# Level 1: the hand-authored opening screen (exit ladder col 8)
# ----------------------------------------------------------------------
LEVEL1 = [
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
    "W....CC...L.....E..W",
    "Wh...CC...L.....E.hW",
    "FFFFFFFFFFFFFFFFFFFF",
]

MENU = [
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


# ======================================================================
# Collision compilation (same rules the engine relies on)
# ======================================================================
def solid_rects(level):
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


def enemies(level, zone, rng):
    """Marker cells -> enemy records.  R/G patrol the clear run on
    their two rows; T throws on a timer."""
    out = []
    for r, row in enumerate(level):
        for c, ch in enumerate(row):
            if ch in 'RG':
                def clear(cc):
                    # body rows free AND ground under their feet:
                    # walkers turn back at gaps and ladder holes
                    return (all(cell_type(level, rr, cc) in (EMPTY, LADDER)
                                for rr in (r, r + 1))
                            and cell_type(level, r + 2, cc) == SOLID)
                c0 = c
                while c0 > 0 and clear(c0 - 1):
                    c0 -= 1
                c1 = c
                while c1 < MAP_W - 1 and clear(c1 + 1):
                    c1 += 1
                etype = ET_RIOT if ch == 'R' else ET_COAT
                out.append((etype, c*4, r*8, 1, c0*4, c1*4))
            elif ch == 'T':
                interval = rng.randrange(90, 150)
                out.append((ET_THROW, c*4, r*8, interval,
                            rng.randrange(1, interval), 0))
    return out


# ======================================================================
# The procedural factory
# ======================================================================
PLAT_ROWS = (18, 12, 6)                 # platform slab rows, bottom-up


def gen_level(rng, zone, entry_col, difficulty, elevator=False, medkit=False,
              cross_colors=(), spare_colors=(), local_colors=(),
              switch_ids=(), vault_ids=()):
    """Build one screen around the fixed climb skeleton.  Returns
    (map lines, plan); raises AssertionError when a layout constraint
    cannot be met (the caller simply retries with fresh randomness)."""
    grid = [['.'] * MAP_W for _ in range(MAP_H)]
    for r in range(24):
        grid[r][0] = grid[r][19] = 'W'
    grid[24] = list('F' * MAP_W)
    for pr in PLAT_ROWS:
        for c in range(1, 19):
            grid[pr][c] = '#'

    def pick_col(avoid, lo=2, hi=17, min_gap=3):
        for _ in range(200):
            c = rng.randrange(lo, hi + 1)
            if all(abs(c - a) >= min_gap for a in avoid):
                return c
        raise AssertionError("no column fits")

    l0 = entry_col
    l1 = pick_col([l0])
    l2 = pick_col([l1])
    ltop = pick_col([l2])
    ladders = [l0, l1, l2, ltop]
    for r0, r1, col in ((16, 23, l0), (10, 17, l1), (4, 11, l2), (0, 5, ltop)):
        for r in range(r0, r1 + 1):
            grid[r][col] = 'L'

    # --- coloured security doors and keycards, five colours strong.
    # LOCAL pairs keep their key on this level, strictly below the
    # door.  CROSS doors have NO key here: main() only assigns them a
    # colour whose spare key was scattered on an EARLIER level, so an
    # ascending player already carries (or could carry) it.  SPARE
    # keys are the other half of that bargain, sown for doors above.
    DOOR_CH, KEY_CH = "XZQVN", "KJYUO"
    all_doors = list(cross_colors) + list(local_colors)
    assert len(all_doors) <= 3, "too many doors"
    door_plats = sorted(rng.sample(range(3), len(all_doors)))
    pairs = []
    for i, col in enumerate(all_doors):
        d = door_plats[i]
        pr = PLAT_ROWS[d]
        arr, dep = ladders[d], ladders[d + 1]
        lo, hi = (arr, dep) if arr < dep else (dep, arr)
        assert hi - lo >= 6, "walkway too narrow for a door"
        dcol = rng.randrange(lo + 3, hi - 1)
        assert abs(dcol - arr) >= 3 and abs(dcol - dep) >= 3, "door near ladder"
        assert all(grid[pr - r][dcol] == '.' for r in (1, 2, 3)), "door spot taken"
        # doors are 3 tiles (24 px) tall: a 19-line jump apex leaves
        # the feet ~5 lines inside the door's span -- unjumpable
        for r in (1, 2, 3):
            grid[pr - r][dcol] = DOOR_CH[col]
        pairs.append({"color": col, "dplat": d, "dcol": dcol,
                      "cross": i < len(cross_colors)})

    def place_thing(ch, kplat, drop=0):
        # drop=1 puts the thing at FEET level, resting on the ground
        # (vaults); drop=0 is card/switch height
        for _ in range(40):
            if kplat < 0:
                krow, below_row = 22, 24
                bad = [l0]
            else:
                krow, below_row = PLAT_ROWS[kplat] - 2, PLAT_ROWS[kplat]
                bad = [ladders[kplat], ladders[kplat + 1]]
            c = rng.randrange(2, 18)
            if TILES[tile_index(CHARMAP[grid[below_row][c]])][1] != SOLID:
                continue
            if grid[krow][c] != '.' or grid[krow + 1][c] != '.':
                continue
            if any(abs(c - b) < 2 for b in bad):
                continue
            grid[krow + drop][c] = ch
            return kplat, c
        raise AssertionError("no spot")

    # a quarter of all keys hide in VAULTS: the vault sits where the
    # key would, but only opens once its switch -- sown on an EARLIER
    # level -- has been pressed.  vault_ids arrived pre-matched to
    # switches already on record.
    vq = list(vault_ids)
    keys_at, vaults, switches = [], [], []

    def place_key(col, kplat):
        if vq and rng.random() < 0.65:
            vid = vq.pop(0)
            p, c = place_thing('$', kplat, drop=1)
            vaults.append((vid, p, c, col))
        else:
            p, c = place_thing(KEY_CH[col], kplat)
            keys_at.append((p, c))

    for pp in pairs:
        if pp["cross"]:
            continue
        d = pp["dplat"]
        kplat = -1 if (zone == 2 or d == 0) else rng.randrange(-1, d)
        place_key(pp["color"], kplat)
    for col in spare_colors:            # sown for doors on levels above
        place_key(col, rng.randrange(-1, 3))
    for sid in switch_ids:              # switches for vaults above
        p, c = place_thing('!', rng.randrange(-1, 3))
        switches.append((sid, p, c))

    def plat_avoid(p):
        avoid = [ladders[p], ladders[p + 1]]
        for pp in pairs:
            if pp["dplat"] == p:
                avoid.append(pp["dcol"])
        for kp, kc in keys_at:
            if kp == p:
                avoid.append(kc)
        for _v, kp, kc, _c in vaults:
            if kp == p:
                avoid.append(kc)
        for _s, kp, kc in switches:
            if kp == p:
                avoid.append(kc)
        return avoid

    # jumpable gaps (2 tiles; running jump clears 8 bytes with huge
    # margin; dropping through is exactly one floor = never a hit)
    gaps = []
    for p in range(3):
        if rng.random() < 0.55:
            pr = PLAT_ROWS[p]
            for _ in range(30):
                g = rng.randrange(3, 15)
                if all(abs(g - a) >= 3 and abs(g + 1 - a) >= 3
                       for a in plat_avoid(p)):
                    grid[pr][g] = grid[pr][g + 1] = '.'
                    gaps.append((p, g, 2))
                    break

    # the elevator door on the bottom floor (levels 1, 10, 20, ...)
    elev_col = None
    if elevator:
        for _ in range(40):
            c = rng.randrange(3, 17)
            if abs(c - l0) >= 3 and grid[22][c] == '.' and grid[23][c] == '.':
                grid[22][c] = grid[23][c] = 'E'
                elev_col = c
                break

    # a medical crate at body height, somewhere on the platforms
    if medkit:
        for _ in range(40):
            p = rng.randrange(0, 3)
            pr = PLAT_ROWS[p]
            c = rng.randrange(2, 18)
            if TILES[tile_index(CHARMAP[grid[pr][c]])][1] != SOLID:
                continue
            if grid[pr - 2][c] != '.' or grid[pr - 1][c] != '.':
                continue
            if any(abs(c - b) < 2 for b in plat_avoid(p)):
                continue
            grid[pr - 2][c] = 'M'
            break

    # lubricant leaks: dripping ceiling pipes.  Red bites, white is
    # only oily water -- learn to read the stain before walking under.
    n_leaks = rng.randrange(0, 3) + (1 if zone == 0 else 0)
    n_leaks = min(n_leaks, 4)
    for _ in range(n_leaks):
        for _ in range(25):
            row = rng.choice((0, PLAT_ROWS[2] + 1, PLAT_ROWS[1] + 1,
                              PLAT_ROWS[0] + 1))
            c = rng.randrange(3, 17)
            if grid[row][c] != '.':
                continue
            if row > 0 and grid[row - 1][c] != '#':
                continue            # must hang from a slab (or the roof)
            if any(abs(c - l) < 2 for l in ladders):
                continue
            if elev_col is not None and abs(c - elev_col) < 2:
                continue
            grid[row][c] = 'l' if rng.random() < 0.6 else 'w'
            break

    # enemies on the platforms (never the floor: respawns land there)
    n_enemies = min(3, 1 + difficulty // 6 + (1 if zone == 2 else 0))
    mix = {0: 'RRG', 1: 'TGT', 2: 'RGT'}[zone]
    placed = 0
    for _ in range(80):
        if placed >= n_enemies:
            break
        p = rng.randrange(0, 3)
        pr = PLAT_ROWS[p]
        ch = mix[rng.randrange(len(mix))]
        if ch == 'T' and p == 0:
            continue
        row = pr - 2
        c = rng.randrange(3, 17)
        if any(abs(c - b) < 3 for b in plat_avoid(p)):
            continue
        if grid[row][c] != '.' or grid[row + 1][c] != '.':
            continue
        if TILES[tile_index(CHARMAP[grid[pr][c]])][1] != SOLID:
            continue
        grid[row][c] = ch
        placed += 1

    # zone dressing + work lights (or vines) hanging under platforms
    deco = {0: 'pgh', 1: 'vb', 2: 'pn'}[zone]
    for _ in range(rng.randrange(10, 17)):
        c = rng.randrange(2, 18)
        r = rng.randrange(1, 23)
        d = deco[rng.randrange(len(deco))]
        if grid[r][c] == '.' and grid[r + 1][c] == '.' \
           and all(grid[r][max(0, c-1):c+2].count(x) == 0
                   for x in 'LKJYXZQRGTEMlw'):
            grid[r][c] = d
    hang = {0: 'm', 1: 'v', 2: 'm'}[zone]
    for pr in PLAT_ROWS:
        for _ in range(rng.randrange(1, 3)):
            c = rng.randrange(2, 18)
            if grid[pr + 1][c] == '.' and grid[pr][c] == '#':
                grid[pr + 1][c] = hang
    if zone == 0 and rng.random() < 0.6:
        c = rng.randrange(3, 15)
        if abs(c - l0) > 2 and abs(c + 1 - l0) > 2 \
           and all(grid[22][cc] == '.' and grid[23][cc] == '.'
                   for cc in (c, c + 1)):
            for cc in (c, c + 1):
                grid[22][cc] = grid[23][cc] = 'C'

    lines = [''.join(r) for r in grid]
    plan = {"ladders": ladders, "pairs": pairs, "gaps": gaps,
            "keys_at": keys_at, "vaults": vaults, "switches": switches}
    return lines, plan


def door_color(level, r, c):
    return "XZQVN".index(level[r][c])


def leaks(level, rng):
    out = []
    for r, row in enumerate(level):
        for c, ch in enumerate(row):
            if ch in 'lw':
                interval = rng.randrange(70, 130)
                out.append((c*4, (r+1)*8, 1 if ch == 'l' else 0,
                            interval, rng.randrange(1, interval)))
    return out


# ======================================================================
# Built-in playthrough verification (mirrors the engine rules)
# ======================================================================
def verify(level, plan, entry_col, name, inventory=None, pressed=None):
    rects = []
    for c0, c1, r0, r1, t in (solid_rects(level) + column_runs(level, DOOR)
                              + column_runs(level, LADDER)):
        tt = t | (door_color(level, r0, c0) << 4) if t & DOOR else t
        rects.append([c0*4, (c1-c0+1)*4, r0*8, (r1-r0+1)*8, tt])
    grid = [list(r) for r in level]
    keys = inventory if inventory is not None else [0] * 5
    if pressed is None:
        pressed = set()
    vault_by_cell = {(p, c): (vid, col)
                     for vid, p, c, col in plan.get("vaults", [])}
    switch_by_cell = {(p, c): sid for sid, p, c in plan.get("switches", [])}

    def plat_of(yy):
        for p in range(3):
            if yy == PLAT_ROWS[p] * 8 - 16:
                return p
        return -1

    def probe(bx, by, bw=4, bh=16):
        f = 0
        for x, w, y, h, t in rects:
            if t and not (bx+bw-1 < x or x+w-1 < bx or by+bh-1 < y or y+h-1 < by):
                f |= t
        return f

    def ladder_at(px, dy):
        for x, w, y, h, t in rects:
            if (t & 0x0F) == LADDER and -1 <= px - x <= 1 \
               and dy >= y and dy + 16 <= y + h:
                return x
        return None

    x, y = entry_col * 4, 176
    assert ladder_at(x, y) is not None, f"{name}: entry col has no ladder"

    def climb_stretch():
        nonlocal x, y
        for _ in range(120):
            if y < 8:
                return
            lx = ladder_at(x, y - 2)
            if lx is None:
                return
            x, y = lx, y - 2

    def walk_to(tx):
        nonlocal x, y
        d = 1 if tx > x else -1
        for _ in range(220):
            if x == tx:
                return True
            nx = x + d
            jumped = False
            for gp, g, w in plan.get("gaps", []):
                if y != PLAT_ROWS[gp] * 8 - 16:
                    continue
                if d == 1 and nx == g * 4:
                    x = g * 4 + w * 4
                    jumped = True
                elif d == -1 and nx == g * 4 + w * 4 - 4:
                    x = g * 4 - 4
                    jumped = True
            if jumped:
                continue
            f = probe(nx, y)
            if f & SOLID:
                if f & DOOR:
                    opened = False
                    for rect in rects:
                        t = rect[4]
                        if t & DOOR and not (
                                nx+3 < rect[0] or rect[0]+rect[1]-1 < nx
                                or y+15 < rect[2] or rect[2]+rect[3]-1 < y):
                            col = (t >> 4) & 7
                            if keys[col] > 0:
                                keys[col] -= 1
                                rect[4] = 0
                                opened = True
                    if opened:
                        continue
                return False
            x = nx
            for dc in (0, 1):
                for dr in (0, 1, 2):
                    cc, rr = x // 4 + dc, y // 8 + dr
                    if cc < MAP_W and rr < MAP_H:
                        ch = grid[rr][cc]
                        if ch in 'KJYUO':
                            keys["KJYUO".index(ch)] += 1
                            grid[rr][cc] = '.'
                        elif ch == '!':
                            sid = switch_by_cell.get((plat_of(y), cc))
                            if sid is not None:
                                pressed.add(sid)
                                grid[rr][cc] = '.'
                        elif ch == '$':
                            v = vault_by_cell.get((plat_of(y), cc))
                            if v is not None and v[0] in pressed:
                                keys[v[1]] += 1
                                grid[rr][cc] = '.'
        return False

    touch = list(plan["keys_at"]) \
            + [(p, c) for _s, p, c in plan.get("switches", [])] \
            + [(p, c) for _v, p, c, _col in plan.get("vaults", [])]
    # floor first: press and pocket everything stashed down here
    for kp, kc in touch:
        if kp < 0:
            assert walk_to(kc * 4), f"{name}: floor stop unreachable"
    assert walk_to(plan["ladders"][0] * 4), f"{name}: can't get back to the ladder"
    # then platform by platform: touch everything, cross the doors
    for p in range(3):
        climb_stretch()
        assert y == PLAT_ROWS[p] * 8 - 16, f"{name}: stuck below platform {p}"
        for kp, kc in touch:
            if kp == p:
                assert walk_to(kc * 4), f"{name}: stop unreachable on p{p}"
        assert walk_to(plan["ladders"][p + 1] * 4), \
            f"{name}: can't cross platform {p}"
    climb_stretch()
    assert y < 8, f"{name}: top exit unreachable"
    assert x == plan["ladders"][3] * 4, f"{name}: exit column mismatch"


# ======================================================================
# Blob building and bank packing
# ======================================================================
def build_blob(level, zone, rng, plan_lookup, door_base=0):
    ems = enemies(level, zone, rng)
    lks = leaks(level, rng)
    tilemap = bytes(tile_index(CHARMAP[ch]) for row in level for ch in row)
    rects = b''
    for c0, c1, r0, r1, t in (solid_rects(level) + column_runs(level, DOOR)
                              + column_runs(level, LADDER)):
        if t & DOOR:                     # door colour rides in bits 4-5
            t |= door_color(level, r0, c0) << 4
        rects += bytes([c0*4, (c1-c0+1)*4, r0*8, (r1-r0+1)*8, t])
    rects += b'\xFF'
    edata = bytes([len(ems)]) + b''.join(bytes(e) for e in ems)
    ldata = bytes([len(lks)]) + b''.join(bytes(l) for l in lks)
    # switches: (id, col, row); vaults: (id, col, row, key colour).
    # rows derive from the plat: platform p keys sit at PLAT_ROWS[p]-2,
    # floor things at row 22 -- recover the cell from the map itself.
    sw, va = [], []
    for r, rows_ in enumerate(level):
        for c, ch in enumerate(rows_):
            if ch == '!':
                sw.append((c, r))
            elif ch == '$':
                va.append((c, r))
    swp = plan_lookup["switches"]
    vap = plan_lookup["vaults"]
    sdata = bytes([len(swp)])
    for sid, p, c in swp:
        r = 22 if p < 0 else PLAT_ROWS[p] - 2
        sdata += bytes([sid, c, r])
    vdata = bytes([len(vap)])
    for vid, p, c, col in vap:
        r = 23 if p < 0 else PLAT_ROWS[p] - 1   # grounded, feet level
        vdata += bytes([vid, c, r, col])
    # elevator door column (x in bytes), #FF when the level has none
    elev = 0xFF
    for c, ch in enumerate(level[22]):
        if ch == 'E':
            elev = c * 4
            break
    rects_off = 6 + len(tilemap)
    enemies_off = rects_off + len(rects)
    head = bytes([rects_off & 255, rects_off >> 8,
                  enemies_off & 255, enemies_off >> 8, elev, 0])
    # trailer: this level's first global door id -- doors number
    # consecutively in rect order, for the opened-doors bitmask
    return (head + tilemap + rects + edata + ldata + sdata + vdata
            + bytes([door_base])), len(ems)


def main(asm_path, build_dir):
    rng = random.Random(0x5AF7)          # fixed seed: reproducible builds
    levels = [LEVEL1]
    plans = [{"ladders": [10, 12, 15, 8], "pairs": [], "gaps": [],
              "keys_at": [], "vaults": [], "switches": []}]
    exit_col = 8                         # level 1 exits at column 8
    idx = 1
    inventory = [0] * 5                  # the verifier's global key bag
    pressed = set()                      # switches thrown so far
    spare_pool = [0] * 5                 # spare keys sown but not spent
    switch_pool = []                     # switch ids sown, vaults pending
    next_sid = 0
    n_cross = n_vaults = n_keys = 0
    for zone, count in enumerate(ZONE_SIZE):
        first = 1 if zone == 0 else 0    # zone 0 includes hand-made L1
        for i in range(first, count):
            idx += 1
            has_elev = (idx % 10 == 0)   # lift stops: 10, 20, 30, 40, 50
            has_med = (idx >= 20 and idx % 9 == 2)  # meds: level 20 up
            spare_n = (rng.random() < (0.15, 0.35, 0.55)[zone]) + \
                      (zone == 2 and rng.random() < 0.3)
            spares = [rng.randrange(5) for _ in range(spare_n)]
            want = (rng.randrange(0, 2), rng.randrange(1, 3),
                    rng.randrange(2, 4))[zone]
            want = min(want, 1 + difficulty_cap(i))
            cross = []
            if zone > 0:
                for c in range(5):
                    if len(cross) < want - 1 and spare_pool[c] > 0 \
                       and rng.random() < (0, 0.4, 0.6)[zone]:
                        cross.append(c)
                        spare_pool[c] -= 1
            locals_n = want - len(cross)
            free = [c for c in range(5) if c not in cross]
            local_cols = rng.sample(free, min(locals_n, len(free)))
            # the vault economy: sow a switch most levels (32 ids max);
            # offer pending switch ids as vault candidates for the keys
            # placed here -- their switches are all on EARLIER levels
            sow_switch = []
            if idx >= 2 and next_sid < 32 and rng.random() < 0.5:
                sow_switch = [next_sid]
            vids = switch_pool[:2]       # at most two vaults a level
            for attempt in range(150):
                inv_try = inventory[:]
                prs_try = set(pressed)
                try:
                    lv, plan = gen_level(rng, zone, exit_col, i,
                                         has_elev, has_med,
                                         cross, spares, local_cols,
                                         sow_switch, vids)
                    verify(lv, plan, exit_col, f"L{idx}", inv_try, prs_try)
                    break
                except AssertionError:
                    continue
            else:
                raise SystemExit(f"L{idx}: no completable layout found")
            inventory = inv_try
            pressed = prs_try
            used_vids = [v[0] for v in plan["vaults"]]
            switch_pool = [s for s in switch_pool if s not in used_vids]
            if plan["switches"]:
                switch_pool.append(plan["switches"][0][0])
                next_sid += 1
            for c in spares:
                spare_pool[c] += 1
            n_cross += len(cross)
            n_vaults += len(plan["vaults"])
            n_keys += len(plan["keys_at"]) + len(plan["vaults"])
            levels.append(lv)
            plans.append(plan)
            exit_col = plan["ladders"][3]
    print(f"key economy: {n_cross} cross-level doors, {n_vaults} of "
          f"{n_keys} keys vaulted ({100*n_vaults//max(1,n_keys)}%), "
          f"{next_sid} switches, {sum(spare_pool)} spares left",
          file=sys.stderr)

    # global door ids: consecutive per level, 128 bits of persistence
    door_bases, did = [], 0
    for pl in plans:
        door_bases.append(did)
        did += len(pl["pairs"])
    assert did <= 128, f"{did} doors exceed the opened-doors bitmask"
    print(f"{did} doors carry persistent ids", file=sys.stderr)

    # pack blobs into 16K banks (a level never straddles banks)
    banks, table = [b''], []
    total_enemies = 0
    for n, lv in enumerate(levels):
        zone = 0 if n < 19 else 1 if n < 39 else 2
        blob, ne = build_blob(lv, zone, rng, plans[n], door_bases[n])
        total_enemies += ne
        if len(banks[-1]) + len(blob) > BANK_SIZE:
            banks.append(b'')
        table.append((len(banks) - 1, len(banks[-1]), len(blob)))
        banks[-1] += blob
    assert len(banks) <= 3, f"level data needs {len(banks)} banks (max 3)"

    for i, b in enumerate(banks):
        with open(f"{build_dir}/levels{i}.bin", "wb") as f:
            f.write(b)

    with open(asm_path, "w") as out:
        w = out.write
        w("; ======================================================================\n")
        w("; GENERATED by tools/level_gen.py -- DO NOT EDIT, edit the tool instead\n")
        w("; ======================================================================\n\n")
        w(f"; --- tileset: {len(TILES)} tiles x 32 bytes ---\ntileset:\n")
        for i, (name, _t, art) in enumerate(TILES):
            w(f"; tile {i}: {name}\n")
            for row in art:
                bs = ", ".join(f"#{b:02X}" for b in encode_art_row(row))
                w(f"        defb {bs}   ; {row}\n")
        w("\n; --- 8x8 font, bit 7 = leftmost; 0-9 A-Z '-' ' ' + icons ---\nfont_8x8:\n")
        for ch in list(FONT_ORDER) + FONT_EXTRA:
            rows = [v << 2 for v in GLYPHS[ch]] + [0]
            w(f"        defb {','.join(f'#{b:02X}' for b in rows)}   ; '{ch}'\n")
        w("\n; --- pen -> Mode 0 left-pixel bits ---\npen_left:\n")
        w(f"        defb {','.join(f'#{left_pixel(p):02X}' for p in range(16))}\n")
        w("\n; --- zone palettes: 16 Gate Array colours each ---\nzone_palettes:\n")
        for z, pal in enumerate(PALETTES):
            w(f"        defb {','.join(f'#{v:02X}' for v in pal)}   ; {ZONES[z]}\n")
        w("\nmenu_map:   ; title-screen backdrop\n")
        for row in MENU:
            idx_s = ",".join(str(tile_index(CHARMAP[ch])) for ch in row)
            w(f"        defb {idx_s}   ; {row}\n")
        w("\n; --- level_table: per level: bank byte (#C4+n), word offset\n")
        w("; within the bank (#4000-based), word blob length ---\nlevel_table:\n")
        for n, (bank, off, ln) in enumerate(table, 1):
            w(f"        defb #C{4+bank:X}\n        defw #{0x4000+off:04X},{ln}"
              f"   ; level {n}\n")
        w(f"\nLEVEL_COUNT     equ {len(levels)}\n")
        w("TILE_EMPTY      equ 0\n")
        w(f"TILE_KEY_BASE   equ {tile_index('keycard_g')}\n")
        w(f"TILE_DOOR_BASE  equ {tile_index('door_g')}\n")
        w(f"TILE_MEDKIT     equ {tile_index('medkit')}\n")
        w(f"TILE_SWITCH_OFF equ {tile_index('switch_off')}\n")
        w(f"TILE_SWITCH_ON  equ {tile_index('switch_on')}\n")
        w(f"TILE_VAULT      equ {tile_index('vault')}\n")
        w(f"GLYPH_SPACE     equ {FONT_ORDER.index(' ')}\n")
        w(f"GLYPH_KEY       equ {len(FONT_ORDER)}\n")
        w(f"GLYPH_HEART     equ {len(FONT_ORDER) + 1}\n")
        w(f"GLYPH_BOLT      equ {len(FONT_ORDER) + 2}\n")
        stops = [1] + [n for n in range(1, len(levels) + 1) if n % 10 == 0]
        w("; elevator stops, bottom terminus first\n")
        w(f"ELEV_COUNT      equ {len(stops)}\nelev_stops:\n")
        w(f"        defb {','.join(str(s) for s in stops)}\n")

    sizes = [len(b) for b in banks]
    print(f"{len(levels)} levels verified completable; banks: {sizes} bytes; "
          f"{total_enemies} enemies placed", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "src/levels.asm",
         sys.argv[2] if len(sys.argv) > 2 else "build")
