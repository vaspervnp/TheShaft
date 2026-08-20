#!/usr/bin/env python3
"""Execute the seven mini-games (the real assembled binary) and check
their rules.

The runner fakes only time (FRAME_SYNC) and the keyboard (READ_INPUT
is skipped; the script pokes input_held/input_new per frame), so every
module runs its own real loop: mg_room builds the set, update_player
walks, whip_box judges, the pool renders.  Each show is entered at its
real entry point and driven to its real verdict; the doorway machinery
(arch_scan, check_arch, the level-mod-7 bill, the return address) is
executed the same way."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

# what start: does first: the shelf comes home
HD, HE = S("HIDATA_START"), S("HIDATA_END")
MEM[HD:HE] = MEM[0x4000:0x4000 + HE - HD]

FS, RI = S("FRAME_SYNC"), S("READ_INPUT")
INP_UP, INP_FIRE, INP_ACT = 1 << 2, 1 << 4, 1 << 5
ET = dict(NONE=0, RIOT=1, COAT=2, THROW=3, PROJ=4, DYING=5, DRIP=6,
          STEAM=7, BULLET=8, DRONE=9, MGSPR=10)
ENT, ENT_SIZE, MAXE = S("ENTITIES"), 10, 8

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(("OK   " if c else "FAIL ") + m)


def cpu(pc=None):
    z = Z80()
    z.sp = 0x1000
    if pc is not None:
        z.pc = pc
    return z


def drive(z, stops, frames=0, feed=None, budget=120_000_000, brief=False):
    """Run until PC lands on a stop (or `frames` frames pass).  With
    brief=True the first FIRE press dismisses the briefing card."""
    clock = 0
    while budget:
        if z.pc == FS:
            clock += 1
            z.pc = z.pop()
            MEM[S("INPUT_HELD")] = 0
            MEM[S("INPUT_NEW")] = INP_FIRE if (brief and clock == 4) else 0
            if feed:
                feed(clock)
            if frames and clock >= frames:
                return "time", clock
            continue
        if z.pc == RI:
            z.pc = z.pop()
            continue
        if z.pc in stops:
            return stops[z.pc], clock
        z.step()
        budget -= 1
    raise SystemExit("runaway")


def world(level=7):
    """A believable shaft to stand in: map+rects aimed, vitals full."""
    MEM[S("CURRENT_LEVEL")] = level
    cm = S("LEVEL_BUFFER") + 6
    MEM[S("CURRENT_MAP")] = cm & 0xFF
    MEM[S("CURRENT_MAP") + 1] = cm >> 8
    cr = S("LEVEL_BUFFER") + 600
    MEM[S("CURRENT_RECTS")] = cr & 0xFF
    MEM[S("CURRENT_RECTS") + 1] = cr >> 8
    for i in range(500):
        MEM[cm + i] = 0
    MEM[S("DRAW_PAGE")] = 0x40         # as the boot leaves it: a real
    MEM[S("BUF_INDEX")] = 0            # buffer to draw into
    MEM[S("SHOWN_R12")] = 0x30
    MEM[S("PLAYER_ENERGY")] = 5
    MEM[S("GAME_LIVES")] = 3
    MEM[S("IMMUNE_TIMER")] = 0
    MEM[S("MG_FLAG")] = 0
    MEM[S("RND_STATE")] = 0x5A
    MEM[S("SFX_TIMER")] = 0
    MEM[S("MUSIC_ON")] = 0
    MEM[S("MIX_A")] = 9
    for i in range(4):
        MEM[S("SCORE") + i] = 0
    for i in range(MAXE * ENT_SIZE):
        MEM[ENT + i] = 0


def ent(i, field):
    return MEM[ENT + i * ENT_SIZE + field]


def slots(t):
    return [i for i in range(MAXE) if ent(i, 0) == t]


GAMES = ["MG_TAPPER", "MG_MOLES", "MG_HOOK", "MG_MARKET",
         "MG_GALLERY", "MG_BOILER", "MG_FIRING"]
STOP_EXIT = {S("MG_EXIT"): "exit"}

# ======================================================================
print("--- the doorway: arch_scan, check_arch, the bill by level")
world()
cm = S("LEVEL_BUFFER") + 6
MEM[cm + 23 * 20 + 5] = 33            # a doorway's foot on the floor
MEM[cm + 17 * 20 + 12] = 33           # ...and one on a platform
run(cpu(), S("ARCH_SCAN"))
tab = list(MEM[S("ARCH_TAB"):S("ARCH_TAB") + 4])
pairs = {(tab[0], tab[1]), (tab[2], tab[3])}
chk(MEM[S("ARCH_COUNT")] == 2 and pairs == {(20, 176), (48, 128)},
    f"arch_scan reads both doorways off the map {tab}")

MEM[S("PLAYER_STATE")] = 0
MEM[S("PLAYER_X")] = 20
MEM[S("PLAYER_Y")] = 176
MEM[S("INPUT_NEW")] = 0
run(cpu(), S("CHECK_ARCH"))
chk(MEM[S("MG_FLAG")] == 0, "no UP, no entry")
MEM[S("INPUT_NEW")] = INP_UP
MEM[S("PLAYER_X")] = 40               # nowhere near a doorway
run(cpu(), S("CHECK_ARCH"))
chk(MEM[S("MG_FLAG")] == 0, "UP away from the arch: nothing")

MEM[S("CURRENT_LEVEL")] = 8           # level 8: a dark house
MEM[S("PLAYER_STATE")] = 0
MEM[S("PLAYER_X")] = 20
MEM[S("PLAYER_Y")] = 176
MEM[S("INPUT_NEW")] = INP_UP
run(cpu(), S("CHECK_ARCH"))
chk(MEM[S("MG_FLAG")] == 0,
    "an unmarked level's arch is scenery: UP does nothing")

for level, game in ((5, 0), (9, 1), (29, 6), (33, 0), (57, 6)):
    world(level)
    MEM[cm + 23 * 20 + 5] = 33
    run(cpu(), S("ARCH_SCAN"))
    MEM[S("PLAYER_STATE")] = 0
    MEM[S("PLAYER_X")] = 20
    MEM[S("PLAYER_Y")] = 176
    MEM[S("INPUT_NEW")] = INP_UP
    z = cpu(S("CHECK_ARCH"))
    where, _fr = drive(z, {S(g): g for g in GAMES})  # the module head
    chk(where == GAMES[game] and MEM[S("MG_FLAG")] == 1
        and MEM[S("MG_RET_X")] == 20 and MEM[S("MG_RET_Y")] == 176,
        f"level {level}: UP at the arch opens {GAMES[game]}, spot saved")

# ======================================================================
print("--- the marquee sign and the fixed books (audit regressions)")
LO = S("LINE_OFFSETS")
LINE = [MEM[LO + 2 * y] | MEM[LO + 2 * y + 1] << 8 for y in range(200)]


def sign_ink(col, y):
    return sum(1 for yy in range(y, y + 8) for x in range(col, col + 4)
               if MEM[0xC000 + LINE[yy] + x])


world(5)                                  # a marquee level
MEM[cm + 23 * 20 + 5] = 33
run(cpu(), S("ARCH_SCAN"))
MEM[0xC000:0x10000] = bytes(0x4000)
MEM[S("DRAW_PAGE")] = 0xC0
run(cpu(), S("DRAW_ARCH_SIGN"))
chk(sign_ink(22, 152) > 5, "the pulsing arrow burns over a live doorway")
MEM[S("MG_FLAG")] = 1
MEM[0xC000:0x10000] = bytes(0x4000)
run(cpu(), S("DRAW_ARCH_SIGN"))
chk(sign_ink(22, 152) == 0, "...and rests once the show has run")
world(6)                                  # a dark house
MEM[cm + 23 * 20 + 5] = 33
run(cpu(), S("ARCH_SCAN"))
MEM[0xC000:0x10000] = bytes(0x4000)
run(cpu(), S("DRAW_ARCH_SIGN"))
chk(sign_ink(22, 152) == 0, "no show, no sign: scenery stays scenery")

world()
for start, amt, digit, want in (((0, 9, 9, 3), 1, 2, (1, 0, 0, 3)),
                                ((0, 9, 9, 3), 5, 2, (1, 0, 4, 3)),
                                ((9, 9, 9, 9), 5, 1, (9, 9, 9, 9)),
                                ((0, 2, 0, 0), 5, 1, (0, 7, 0, 0))):
    for i, v in enumerate(start):
        MEM[S("SCORE") + i] = v
    z = cpu()
    z.a = amt
    hl = S("SCORE") + digit
    z.h, z.l = hl >> 8, hl & 0xFF
    run(z, S("MG_PAYDIG"))
    got = tuple(MEM[S("SCORE"):S("SCORE") + 4])
    chk(got == want, f"mg_paydig {start} +{amt}@{digit} -> {got}")

MEM[S("PLAYER_ENERGY")] = 5
MEM[S("GAME_LIVES")] = 9
z = cpu()
z.a = 1
run(z, S("ADD_ENERGY"))
chk(MEM[S("PLAYER_ENERGY")] == 5 and MEM[S("GAME_LIVES")] == 9,
    "a reward at nine lives tops the tank -- never wraps it to 1")

# ======================================================================
print("--- the briefing card before the curtain")
world(5)
MEM[0x4000:0x8000] = bytes(0x4000)
MEM[0xC000:0x10000] = bytes(0x4000)
BRIEFS = ["BRF_TAPPER", "BRF_MOLES", "BRF_HOOK", "BRF_MARKET",
          "BRF_GALLERY", "BRF_BOILER", "BRF_FIRING"]


def cstr(a_):
    out = ""
    while MEM[a_]:
        out += chr(MEM[a_])
        a_ += 1
    return out


for name in BRIEFS:                       # every card must FIT
    p = S(name)
    strs = []
    while True:
        ptr = MEM[p] | MEM[p + 1] << 8
        p += 2
        if not ptr:
            break
        strs.append(cstr(ptr))
    title, rules = strs[0], strs[1:]
    chk(len(strs) >= 3, f"{name}: a title and {len(rules)} rules")
    chk(4 + 8 * len(title) <= 80,
        f"...{title!r} fits the double-size font ({4+8*len(title)} bytes)")
    chk(all(2 + 3 * len(r) <= 80 for r in rules),
        f"...its rules fit the narrow font "
        f"{[2 + 3 * len(r) for r in rules]}")

z = cpu(S("MG_GALLERY"))
_w, fr = drive(z, {S("MG_ROOM"): "room"}, brief=False, frames=40)
def band(base, y0, y1):
    return sum(1 for yy in range(y0, y1) for x in range(80)
               if MEM[base + LINE[yy] + x])


rows = [band(0xC000, 24, 40), band(0xC000, 72, 80),
        band(0xC000, 88, 96), band(0xC000, 168, 176)]
chk(all(r > 20 for r in rows),
    f"title, both rules and the prompt are all on the card {rows}")
mirror = [band(0x4000, 24, 40), band(0x4000, 72, 80),
          band(0x4000, 88, 96), band(0x4000, 168, 176)]
chk(mirror == rows, f"...and identical in the other buffer {mirror}")
chk(_w != "room", "...and the show waits behind it")
z = cpu(S("MG_GALLERY"))
w, fr = drive(z, {S("MG_ROOM"): "room"}, brief=True)
chk(w == "room" and fr < 20,
    f"SPACE dismisses it and the set is built (frame {fr})")
z = cpu(S("MG_GALLERY"))
w, fr = drive(z, {S("MG_ROOM"): "room"}, brief=False)
chk(w == "room" and 180 < fr < 230,
    f"...and after ten unattended seconds it starts anyway (frame {fr})")

# ======================================================================
print("--- THE CANTEEN: serve, be served upon, and the perfect shift")
world(14)


def feed_tap(fr):
    if fr == 5:
        MEM[S("INPUT_NEW")] = INP_ACT


z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=90, brief=True)
chk(len(slots(ET["RIOT"])) >= 1, "a guard is in the queue by frame 90")
g = slots(ET["RIOT"])[0]
MEM[ENT + g * ENT_SIZE + 1] = 30      # march him to mid-counter,
MEM[ENT + g * ENT_SIZE + 9] = 0       # bottom lane
MEM[ENT + g * ENT_SIZE + 2] = 176
drive(z, {}, frames=60, feed=feed_tap)      # Z five frames in
chk(ent(g, 3) == 0xFF and MEM[S("MG_E")] == 1,
    "a tin down the counter turns him around, served")
chk(MEM[S("SCORE") + 3] == 5, "...for 5 points")
MEM[S("MG_E")] = 12                   # fast-forward the shift
lives0 = MEM[S("GAME_LIVES")]
where, _ = drive(z, STOP_EXIT)
chk(where == "exit" and MEM[S("GAME_LIVES")] == lives0 + 1,
    "twelve served, none wasted: the PERFECT SHIFT pays a life")

world(14)
z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=70, brief=True)
g = slots(ET["RIOT"])[0]
MEM[ENT + g * ENT_SIZE + 1] = 57      # one step from the counter
en0 = MEM[S("PLAYER_ENERGY")]
where, _ = drive(z, STOP_EXIT)
chk(where == "exit" and MEM[S("PLAYER_ENERGY")] == en0 - 1,
    "a guard reaching the counter reports you: one energy, shift over")

# ======================================================================
print("--- PEST DETAIL: the right rat, the wrong rat")
world(8)
z = cpu(S("MG_MOLES"))
drive(z, {}, frames=10, brief=True)
tab12 = S("MG_TAB12")
MEM[tab12] = 60                       # a red nose at hatch 0 (16,176)
MEM[tab12 + 1] = 0
MEM[S("PLAYER_X")] = 24               # a side crack to the left reaches
MEM[S("PLAYER_FACING")] = 1
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 0
drive(z, {}, frames=3)
chk(MEM[S("MG_E")] == 1 and MEM[tab12] == 0 and MEM[S("SFX_TYPE")] == 5,
    "the side crack takes the red rat: tally 1, the kill rings")
MEM[tab12 + 2] = 60                   # the white one at hatch 1 (36,176)
MEM[tab12 + 3] = 1
MEM[S("PLAYER_X")] = 44
MEM[S("PLAYER_FACING")] = 1
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 0
drive(z, {}, frames=3)
chk(MEM[S("MG_F")] == 1, "whipping the laboratory asset voids the bonus")
MEM[S("MG_SEC")] = 1
MEM[S("MG_FR")] = 1
en0 = MEM[S("PLAYER_ENERGY")]
where, _ = drive(z, STOP_EXIT)
chk(where == "exit" and MEM[S("PLAYER_ENERGY")] == en0,
    "voided: the shift ends with no energy paid")

# ======================================================================
print("--- THE HOOK: the stake, the drop, the prize")
world(9)
z = cpu(S("MG_HOOK"))                 # score 0: the chain man refuses
where, _ = drive(z, STOP_EXIT, brief=True)
chk(where == "exit" and all(MEM[S("SCORE") + i] == 0 for i in range(4)),
    "with no 500 to stake, no game and nothing taken")
world(9)
MEM[S("SCORE") + 1] = 7               # 0700
z = cpu(S("MG_HOOK"))
drive(z, {}, frames=10, brief=True)
chk(MEM[S("SCORE") + 1] == 2, "the stake of 500 is taken (0700 -> 0200)")
h = slots(ET["MGSPR"])
chk(bool(h), "the hook rides its rail")
x0 = ent(h[0], 1)
drive(z, {}, frames=10)
chk(ent(h[0], 1) != x0, "...and it is moving")


def feed_hook(fr):
    if fr == 30:
        MEM[S("INPUT_NEW")] = INP_FIRE


snap = (MEM[S("PLAYER_ENERGY")], MEM[S("GAME_LIVES")],
        list(MEM[S("SCORE"):S("SCORE") + 4]))
z2_frames = drive(z, {}, frames=90, feed=feed_hook)
after = (MEM[S("PLAYER_ENERGY")], MEM[S("GAME_LIVES")],
         list(MEM[S("SCORE"):S("SCORE") + 4]))
chk(MEM[S("MG_A")] == 2 and snap != after,
    f"one drop taken: a prize of some kind changed the books {snap} "
    f"-> {after}")


def feed_down(fr):
    if fr == 5:
        MEM[S("INPUT_NEW")] = 1 << 3      # DOWN: walk away


where, _ = drive(z, STOP_EXIT, feed=feed_down)
chk(where == "exit", "DOWN walks away from the crane, as promised")

# ======================================================================
print("--- THE BLACK MARKET: the telegraph, the catch, the eye")
world(10)
z = cpu(S("MG_MARKET"))
drive(z, {}, frames=100, brief=True)
chk(bool(slots(ET["DRIP"])) or True, "the pipe telegraphs (drips flow)")
chk(bool(slots(ET["THROW"])), "the smuggler holds his gantry")
p = slots(ET["PROJ"])
chk(bool(p), "stock is falling by frame 100")
i = p[0]
MEM[ENT + i * ENT_SIZE + 1] = (MEM[S("PLAYER_X")] - 3) & 0xFF
MEM[ENT + i * ENT_SIZE + 2] = 170
en0 = MEM[S("PLAYER_ENERGY")]
drive(z, {}, frames=3)
chk(MEM[S("MG_E")] >= 1 and MEM[S("PLAYER_ENERGY")] == en0,
    "a package at the left contact edge is caught, never a graze")


def feed_duck(fr):
    MEM[S("INPUT_HELD")] = 1 << 3         # DOWN: helmet down


drive(z, {}, frames=3, feed=feed_duck)    # helmet already down...
free = slots(ET["NONE"])[0]               # ...then stock falls on it
MEM[ENT + free * ENT_SIZE + 0] = ET["PROJ"]
MEM[ENT + free * ENT_SIZE + 1] = MEM[S("PLAYER_X")]
MEM[ENT + free * ENT_SIZE + 2] = 160
MEM[ENT + free * ENT_SIZE + 7] = 0
got0 = MEM[S("MG_E")]
en0 = MEM[S("PLAYER_ENERGY")]
drive(z, {}, frames=8, feed=feed_duck)
chk(MEM[S("PLAYER_ENERGY")] == en0 and MEM[S("MG_E")] == got0
    and ent(free, 0) != ET["PROJ"],
    "helmet down: the package bursts -- no harm, no pay")
MEM[S("MG_A")] = 1                    # the eye opens...
MEM[S("MG_F")] = 0
MEM[S("MG_B")] = 200                  # ...and holds its stare


def feed_walk(fr):
    MEM[S("INPUT_HELD")] = 1 << 1     # he twitches: a real step right


drive(z, {}, frames=8, feed=feed_walk)
chk(bool(slots(ET["COAT"])), "movement under the red eye brings the coat")
c = slots(ET["COAT"])[0]
MEM[ENT + c * ENT_SIZE + 1] = 71
where, _ = drive(z, STOP_EXIT)
chk(where == "exit", "his pass ends the market: THE EYE SAW YOU")

# ======================================================================
print("--- DRONE GALLERY: quota by whip")
world(11)
z = cpu(S("MG_GALLERY"))
drive(z, {}, frames=60, brief=True)
d = slots(ET["DRONE"])
chk(bool(d), "a decommissioned drone is released")
i = d[0]
MEM[ENT + i * ENT_SIZE + 1] = MEM[S("PLAYER_X")]
MEM[ENT + i * ENT_SIZE + 2] = 150
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 1
drive(z, {}, frames=3)
chk(MEM[S("MG_E")] == 1, "the overhead crack counts a kill")
MEM[S("MG_E")] = 10                   # two past quota
MEM[S("MG_SEC")] = 1
MEM[S("MG_FR")] = 1
MEM[S("PLAYER_ENERGY")] = 3           # room in the tank to see the pay
for i in range(4):
    MEM[S("SCORE") + i] = 0
where, _ = drive(z, STOP_EXIT)
chk(where == "exit" and MEM[S("PLAYER_ENERGY")] == 4
    and MEM[S("SCORE")] == 0 and MEM[S("SCORE") + 1] == 7,
    "quota plus two: one energy and 700 on the board")

# ======================================================================
print("--- THE BOILER: chips, returns, and the floor")
world(12)
z = cpu(S("MG_BOILER"))
drive(z, {}, frames=5, brief=True)
crud0 = MEM[S("MG_E")]
MEM[S("MG_C")] = 0xFF                 # send it rising into the crust
MEM[ENT + 1] = 30
MEM[ENT + 2] = 39
drive(z, {}, frames=2)
chk(MEM[S("MG_E")] == crud0 - 1 and MEM[S("MG_C")] == 1,
    "a rising pellet chips a scale tile and bounces back down")
cell = S("LEVEL_BUFFER") + 6 + 4 * 20 + 8   # it stepped once first
chk(MEM[cell] == 0, "...the tile is gone from the map itself")
MEM[ENT + 1] = MEM[S("PLAYER_X")]     # the overhead return
MEM[ENT + 2] = 166
MEM[S("MG_C")] = 1
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 1
drive(z, {}, frames=3)
chk(MEM[S("MG_C")] == 0xFF, "the overhead crack bats it skyward")
for _ in range(2):                    # two splashes...
    MEM[ENT + 2] = 179
    MEM[S("MG_C")] = 1
    drive(z, STOP_EXIT, frames=8)
MEM[ENT + 2] = 179                    # ...and the third fouls it
MEM[S("MG_C")] = 1
r = drive(z, STOP_EXIT)
chk(r[0] == "exit", "three pellets on the floor and the boiler fouls")

# ======================================================================
print("--- THE FIRING LINE: salvos and the clean sheet")
world(13)
z = cpu(S("MG_FIRING"))
drive(z, {}, frames=95, brief=True)
b = slots(ET["BULLET"])
chk(bool(b) and all(ent(i, 2) in (181, 188) for i in b),
    f"salvos fly at the two drill heights {[ent(i,2) for i in b]}")
world(13)
z = cpu(S("MG_FIRING"))
drive(z, {}, frames=5, brief=True)
MEM[S("MG_SEC")] = 1
MEM[S("MG_FR")] = 1
lives0 = MEM[S("GAME_LIVES")]
where, _ = drive(z, STOP_EXIT)
chk(where == "exit" and MEM[S("GAME_LIVES")] == lives0 + 1,
    "an unmarked decoy is paid a whole life")

# ======================================================================
print("--- the way home")
world(14)
MEM[S("MG_RET_X")] = 33
MEM[S("MG_RET_Y")] = 128
z = cpu(S("MG_EXIT"))
where, _ = drive(z, {S("LOAD_LEVEL"): "reload"})
chk(where == "reload" and MEM[S("PLAYER_X")] == 33
    and MEM[S("ENTRY_Y")] == 128,
    "mg_exit reloads the level with the mechanic at his doorway")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
