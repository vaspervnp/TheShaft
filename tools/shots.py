#!/usr/bin/env python3
"""shots.py -- photograph The Shaft without a CPC.

Every picture here is the real assembled binary's own output.  The
menu is painted by ms_pages, a level by load_level + enter_level and
then as many turns of the actual game_loop as the shot needs, and the
Mode 0 bytes are read straight out of the emulated screen buffer --
so a screenshot cannot drift from the game the way a mock-up would.

Three things the emulator does not do for us, and how each is made
good here:
  * banking -- z80mini ignores OUT, so the level blob is staged at
    #4000 by hand, exactly where the bank would have appeared;
  * the Gate Array palette -- never readable from RAM, so the pen list
    load_level chose is decoded through the CPC's 32 hardware colours;
  * the 300 Hz interrupt -- nothing drives int_stub, so frames() adds
    its six ticks to frame_ticks per 50 Hz frame itself.  Without that
    the vent clock (update_vents reads the delta) never advances and
    no nozzle in any picture would ever blow steam.

    ./build.sh
    python3 tools/shots.py              # -> docs/shot_*.png
    python3 tools/shots.py --levels     # what stands on each floor
"""
import os
import re
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run
import level_gen as lg
from PIL import Image

BIN, SYMF = "build/shaft.bin", "build/shaft.sym"


def read_syms(path):
    """Symbols only -- importing this module must not disturb a machine
    the caller may already have set up (load() would overwrite MEM)."""
    out = {}
    for ln in open(path):
        m = re.match(r"(\S+)\s+#([0-9A-F]+)", ln)
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


SYM = read_syms(SYMF)
S = lambda n: SYM[n.upper()]


# The hand-written sources and the generators behind everything the
# build produces.  (src/levels*.asm and src/sprites_c.asm are NOT in
# the list: they are generated, and build_demo.sh rewrites them after
# build.sh has assembled -- a mtime test against those cries wolf on
# every single run.)
SOURCES = ("src/main.asm", "tools/level_gen.py", "tools/sprites_art.py",
           "tools/sprite_compile.py")


def check_fresh():
    """The pictures are only as current as the binary they came from."""
    b = os.path.getmtime(BIN)
    stale = [f for f in SOURCES if os.path.exists(f) and os.path.getmtime(f) > b]
    if stale:
        print(f"warning: {BIN} is older than {', '.join(stale)} -- "
              "run ./build.sh first", file=sys.stderr)

# The Gate Array's 32 hardware colours, in the order the ink value
# (#40 + n) selects them.  Values come in RGB thirds: 0, 128, 255.
HW = [
    (128, 128, 128), (128, 128, 128), (0, 255, 128), (255, 255, 128),
    (0, 0, 128),     (255, 0, 128),   (0, 128, 128), (255, 128, 128),
    (255, 0, 128),   (255, 255, 128), (255, 255, 0), (255, 255, 255),
    (255, 0, 0),     (255, 0, 255),   (255, 128, 0), (255, 128, 255),
    (0, 0, 128),     (0, 255, 128),   (0, 255, 0),   (0, 255, 255),
    (0, 0, 0),       (0, 0, 255),     (0, 128, 0),   (0, 128, 255),
    (128, 0, 128),   (128, 255, 128), (128, 255, 0), (128, 255, 255),
    (128, 0, 0),     (128, 0, 255),   (128, 128, 0), (128, 128, 255),
]

INP_LEFT, INP_RIGHT, INP_UP, INP_DOWN = 1, 2, 4, 8
INP_FIRE, INP_ACT = 16, 32       # Space jumps, Z cracks the whip
SOLIDS = {i for i, (_n, t, _a) in enumerate(lg.TILES) if t == lg.SOLID}
LADDERS = {i for i, (_n, t, _a) in enumerate(lg.TILES) if t == lg.LADDER}
MAP_W, MAP_H = 20, 25


# ----------------------------------------------------------------------
# the machine
# ----------------------------------------------------------------------
def boot():
    """start: up to the JP to the menu -- shelf home, screens black."""
    load(BIN, SYMF)                             # code and data as loaded
    hd, he = S("HIDATA_START"), S("HIDATA_END")
    MEM[hd:he] = MEM[0x4000:0x4000 + he - hd]   # the #A600 shelf comes home
    MEM[0x4000:0x8000] = bytes(0x4000)          # clear_buffers
    MEM[0xC000:0x10000] = bytes(0x4000)
    MEM[S("SHOWN_R12")] = 0x30                  # showing #C000...
    MEM[S("DRAW_PAGE")] = 0x40                  # ...drawing into #4000
    MEM[S("BUF_INDEX")] = 0
    for i in range(11):                         # nobody leaning on a key
        MEM[S("KEY_MATRIX") + i] = 0xFF


def cpu(pc=None):
    z = Z80()
    z.sp = 0x1000
    if pc is not None:
        z.pc = pc
    return z


def call(name, a=0, b=0, c=0, d=0, e=0, hl=0, budget=60_000_000):
    z = cpu()
    z.a, z.b, z.c, z.d, z.e = a, b, c, d, e
    z.sethl(hl)
    run(z, S(name), max_steps=budget)
    return z


def frames(z, n, keys=None, budget=400_000_000):
    """Turn the real game_loop n times.  frame_sync and read_input are
    the only fakes: the clock is ours and the keys come from `keys`."""
    fs, ri, ft = S("FRAME_SYNC"), S("READ_INPUT"), S("FRAME_TICKS")
    clock = 0
    while budget:
        if z.pc == fs:
            clock += 1
            z.pc = z.pop()
            MEM[ft] = (MEM[ft] + 6) & 0xFF      # the 300 Hz stub's share
                                                # of one 50 Hz frame
            held, new = keys(clock) if keys else (0, 0)
            MEM[S("INPUT_HELD")] = held
            MEM[S("INPUT_NEW")] = new
            if clock > n:                # frame n is drawn and complete
                return
            continue
        if z.pc == ri:
            z.pc = z.pop()
            continue
        z.step()
        budget -= 1
    raise SystemExit("runaway")


# ----------------------------------------------------------------------
# the film
# ----------------------------------------------------------------------
LO = None


def line_table():
    global LO
    if LO is None:
        o = S("LINE_OFFSETS")
        LO = [MEM[o + 2 * y] | MEM[o + 2 * y + 1] << 8 for y in range(200)]
    return LO


def palette(zone):
    p = S("ZONE_PALETTES") + zone * 16
    return [HW[MEM[p + i] & 0x1F] for i in range(16)]


def zone_of(level):
    return 0 if level < 20 else 1 if level < 40 else 2


def pen_left(b):
    return (((b >> 7) & 1) | ((b >> 3) & 1) << 1
            | ((b >> 5) & 1) << 2 | ((b >> 1) & 1) << 3)


def shoot(path, base, pal, scale=2):
    """The Mode 0 screen at `base`: 80 bytes a line, 2 pixels a byte,
    so 160x200 logical pixels.  A Mode 0 pixel is twice as wide as it
    is tall, so the picture goes out at 4x across and 2x down."""
    line = line_table()
    img = Image.new("RGB", (160, 200))
    put = img.load()
    for y in range(200):
        a = base + line[y]
        for xb in range(80):
            b = MEM[a + xb]
            put[xb * 2, y] = pal[pen_left(b)]
            put[xb * 2 + 1, y] = pal[pen_left((b << 1) & 0xFF)]
    img = img.resize((160 * 2 * scale, 200 * scale), Image.NEAREST)
    img.save(path)
    return img


def visible():
    """The buffer the loop has just finished drawing into."""
    return MEM[S("DRAW_PAGE")] << 8


# ----------------------------------------------------------------------
# the sets
# ----------------------------------------------------------------------
def stage_bank(level):
    """z80mini ignores OUT, so put the level's bank where it belongs."""
    e = S("LEVEL_TABLE") + (level - 1) * 5
    bank, src = MEM[e], MEM[e + 1] | MEM[e + 2] << 8
    blob = open(f"build/levels{bank - 0xC4}.bin", "rb").read()
    MEM[0x4000:0x4000 + len(blob)] = blob
    return bank, src, MEM[e + 3] | MEM[e + 4] << 8


def map_base():
    return MEM[S("CURRENT_MAP")] | MEM[S("CURRENT_MAP") + 1] << 8


def tile(col, row):
    return MEM[map_base() + row * MAP_W + col]


def standing_cols(y=176):
    """Columns where a mechanic can stand at height y: the two rows he
    fills are clear and the one under his boots is solid."""
    r = y // 8
    return [c for c in range(MAP_W)
            if r + 2 < MAP_H and tile(c, r + 2) in SOLIDS
            and tile(c, r) not in SOLIDS and tile(c, r + 1) not in SOLIDS]


def ladder_cols():
    """Columns with rails he can already be holding when he walks in."""
    return [c for c in range(MAP_W)
            if tile(c, 22) in LADDERS and tile(c, 21) in LADDERS]


def new_game():
    """what ms_loop pokes in before the first load_level."""
    MEM[S("GAME_LIVES")] = 3
    MEM[S("PLAYER_ENERGY")] = 5
    for i in range(5):
        MEM[S("KEYS_HELD") + i] = 0
    for i in range(4):
        MEM[S("SCORE") + i] = 0
    MEM[S("VISITED_STOPS")] = 0
    for i in range(2):
        MEM[S("SHOWS_DONE") + i] = 0
    for i in range(4):
        MEM[S("SWITCH_STATE") + i] = 0
    for i in range(40):
        MEM[S("OPENED_DOORS") + i] = 0
    MEM[S("IMMUNE_TIMER")] = 0
    MEM[S("MG_TEST")] = 0
    MEM[S("RND_STATE")] = 0x5A


def enter(level, x=None, y=176, near=None):
    """Boot, page in the level's bank, load it and walk in the door.
    y is the deck he arrives on -- 176 is the floor the lift and the
    ladders from below deliver him to; a platform works just as well."""
    boot()
    new_game()
    stage_bank(level)
    MEM[S("CURRENT_LEVEL")] = level
    call("LOAD_LEVEL", a=level)
    if x is None:
        cols = standing_cols(y)
        want = near if near is not None else MAP_W // 2
        col = min(cols, key=lambda c: (abs(c - want), c)) if cols else 9
        x = col * 4
    MEM[S("PLAYER_X")] = x
    MEM[S("ENTRY_Y")] = y
    return cpu(S("ENTER_LEVEL"))


def hold(bits, *, jump_at=None, whip_at=None):
    """A held direction, plus the one-frame press edges the player
    reads: Space jumps, Z (INP_ACT) cracks the whip."""
    def keys(clock):
        new = 0
        if jump_at and clock == jump_at:
            new |= INP_FIRE
        if whip_at and clock == whip_at:
            new |= INP_ACT
        return bits | new, new
    return keys


# ----------------------------------------------------------------------
def level_report():
    print("lvl zone lift arch enemies            keys")
    for n in range(1, 60):
        boot()
        stage_bank(n)
        MEM[S("CURRENT_LEVEL")] = n
        call("LOAD_LEVEL", a=n)
        ent = S("ENTITIES")
        types = [MEM[ent + i * 10] for i in range(8)]
        names = {1: "riot", 2: "coat", 3: "throw"}
        mix = ",".join(sorted({names.get(t, str(t)) for t in types if t}))
        cm = MEM[S("CURRENT_MAP")] | MEM[S("CURRENT_MAP") + 1] << 8
        keys = sum(1 for i in range(500) if 8 <= MEM[cm + i] <= 12)
        lift = MEM[S("ELEVATOR_COL")]
        print(f"{n:3} {zone_of(n):4} {'' if lift == 0xFF else lift:>4} "
              f"{MEM[S('ARCH_COUNT')]:4} {mix:18} {keys:4}")


# ----------------------------------------------------------------------
# the pictures
# ----------------------------------------------------------------------
def menu_shot(path, page):
    """One side of the attract loop, with the prompt caught mid-blink
    (ms_loop paints it over whichever page is up, every 16 frames)."""
    boot()
    MEM[S("CURRENT_MAP")] = S("MENU_MAP") & 0xFF
    MEM[S("CURRENT_MAP") + 1] = S("MENU_MAP") >> 8
    MEM[S("ATTRACT_PG")] = page
    call("MS_PAGES")
    MEM[S("DRAW_PAGE")] = 0xC0
    call("DRAW_TEXT", hl=S("TXT_PRESS"), b=0, c=120, e=6)
    return shoot(path, 0xC000, palette(0))


def level_shot(path, level, turns, keys=None, x=None, y=176, near=None):
    z = enter(level, x=x, y=y, near=near)
    frames(z, turns, keys)
    return shoot(path, visible(), palette(zone_of(level)))


def shot_menu(path):
    return menu_shot(path, 0)


def shot_guide(path):
    return menu_shot(path, 1)


def shot_deck(path):
    """Level 1, the machine deck: walking up to the lift's double
    doors with the floor number lit above them, and the background
    doorway that leads off the shaft two bays along."""
    return level_shot(path, 1, 9, hold(INP_RIGHT), x=48)


def shot_whip(path):
    """Level 14, up on a gantry between two riot guards, caught two
    frames into the crack -- whip_timer counts 10 down to 5 with the
    rope drawn, and only at 6 does whip_hits decide who it reached."""
    def keys(clock):                    # two steps left to turn, then
        held = INP_LEFT if clock <= 2 else 0    # stand and crack it
        new = INP_ACT if clock == 4 else 0
        return held | new, new
    return level_shot(path, 14, 6, keys, x=36, y=128)


def shot_climb(path):
    """Level 30, agricultural: on the rails.  UP stays out of
    input_new, so the doorway and the lift both keep their peace."""
    z = enter(30)
    cols = ladder_cols()
    if cols:
        MEM[S("PLAYER_X")] = cols[len(cols) // 2] * 4
        z = cpu(S("ENTER_LEVEL"))
    frames(z, 30, lambda c: (INP_UP, 0))
    return shoot(path, visible(), palette(zone_of(30)))


def shot_admin(path):
    """Level 45, the administrative tiers: cold blue glass, a marked
    doorway and the guards who shoot."""
    return level_shot(path, 45, 14, hold(INP_RIGHT))


SHOTS = [
    ("shot_menu", shot_menu),
    ("shot_guide", shot_guide),
    ("shot_level01", shot_deck),
    ("shot_level14", shot_whip),
    ("shot_level30", shot_climb),
    ("shot_level45", shot_admin),
]


def main():
    check_fresh()
    if "--levels" in sys.argv:
        return level_report()
    os.makedirs("docs", exist_ok=True)
    for name, fn in SHOTS:
        fn(f"docs/{name}.png")
        print(f"docs/{name}.png")


if __name__ == "__main__":
    main()
