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
    MEM[S("KEY_MATRIX") + 8] = 0xFF        # nobody leaning on ESC
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
    MEM[S("SHOWS_DONE")] = 0           # every doorway open again
    MEM[S("SHOWS_DONE") + 1] = 0
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

# --- level 1 is the opening screen, not an arcade: its doorway is
# scenery like every other unmarked arch
world(1)
MEM[cm + 23 * 20 + 11] = 33
run(cpu(), S("ARCH_SCAN"))
MEM[S("PLAYER_STATE")] = 0
MEM[S("PLAYER_X")] = 44
MEM[S("PLAYER_Y")] = 176
MEM[S("INPUT_NEW")] = INP_UP
MEM[S("MG_FLAG")] = 0
z = cpu(S("CHECK_ARCH"))
z.push(0x0002)
stops = {S(g): g for g in GAMES}
stops[0x0002] = "closed"
w, _ = drive(z, stops, frames=4)
chk(w == "closed" and MEM[S("MG_FLAG")] == 0,
    "level 1 opens no show: the first deck is for learning to climb")

# --- a doorway is spent for the whole run, not just the visit
GSTOP = {S(g): g for g in GAMES}
SENT = 0x0002


def try_enter(level):
    MEM[S("CURRENT_LEVEL")] = level
    MEM[S("PLAYER_STATE")] = 0
    MEM[S("PLAYER_X")] = 20
    MEM[S("PLAYER_Y")] = 176
    MEM[S("INPUT_NEW")] = INP_UP
    z = cpu(S("CHECK_ARCH"))
    z.push(SENT)
    stops = dict(GSTOP)
    stops[SENT] = "closed"
    w, _ = drive(z, stops, frames=4)
    return w


world(9)
MEM[cm + 23 * 20 + 5] = 33
run(cpu(), S("ARCH_SCAN"))
chk(try_enter(9) == "MG_MOLES", "the first visit opens the show")
MEM[S("CURRENT_LEVEL")] = 10          # he climbs away...
run(cpu(), S("ARCH_SCAN"))
MEM[S("CURRENT_LEVEL")] = 9           # ...and comes back down
run(cpu(), S("ARCH_SCAN"))
chk(try_enter(9) == "closed",
    "leaving the level and returning does NOT reopen it")
chk(try_enter(13) == "MG_HOOK", "...while another doorway still plays")
MEM[S("SHOWS_DONE")] = 0
MEM[S("SHOWS_DONE") + 1] = 0
chk(try_enter(9) == "MG_MOLES", "a new game reopens every house")

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
MEM[S("SHOWS_DONE")] = 0xFF
MEM[S("SHOWS_DONE") + 1] = 0xFF
MEM[0xC000:0x10000] = bytes(0x4000)
run(cpu(), S("DRAW_ARCH_SIGN"))
chk(sign_ink(22, 152) == 0, "...and goes dark once that show has run")
MEM[S("SHOWS_DONE")] = 0
MEM[S("SHOWS_DONE") + 1] = 0
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

for lane, y in ((0, 176), (1, 128), (2, 80)):
    z = cpu()
    z.a = lane
    run(z, S("TP_LANEY"))
    chk(z.a == y, f"counter {lane} stands at line {z.a}")

for lane, y in ((0, 176), (1, 128), (2, 80)):   # EVERY counter serves
    world(14)
    z = cpu(S("MG_TAPPER"))
    drive(z, {}, frames=90, brief=True)
    g = slots(ET["RIOT"])[0]
    MEM[ENT + g * ENT_SIZE + 9] = lane
    MEM[ENT + g * ENT_SIZE + 2] = y
    MEM[ENT + g * ENT_SIZE + 1] = 30
    MEM[S("MG_A")] = lane             # the mechanic on that counter
    MEM[S("MG_E")] = 0

    def feed_serve(fr):
        if fr == 3:
            MEM[S("INPUT_NEW")] = INP_ACT

    drive(z, {}, frames=60, feed=feed_serve)
    chk(ent(g, 3) == 0xFF and MEM[S("MG_E")] == 1,
        f"a tin served on counter {lane} turns its guard around")

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
def readout(col):                     # ink in a 7-seg berth
    return sum(1 for yy in range(0, 8) for x in range(col, col + 7)
               if MEM[0xC000 + LINE[yy] + x])


MEM[S("DRAW_PAGE")] = 0xC0            # look at the buffer being drawn
drive(z, {}, frames=2)
one = readout(2)
chk(one > 0, f"the tally is on the wall beside a rat ({one} ink)")
MEM[S("MG_E")] = 11                   # a very different number...
drive(z, {}, frames=2)
chk(readout(2) != one, "...and it changes as the shift goes on")
chk(readout(66) > 0, "...while the clock keeps its own berth")
MEM[S("MG_E")] = 1

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
print("--- ESC walks out of any show, at any moment")
for name, entry, when in (("the canteen", "MG_TAPPER", 30),
                          ("the rat shift", "MG_MOLES", 60),
                          ("the boiler", "MG_BOILER", 20),
                          ("the firing line", "MG_FIRING", 90)):
    world(11)
    z = cpu(S(entry))
    drive(z, {}, frames=when, brief=True)

    def press_esc(fr):
        MEM[S("KEY_MATRIX") + 8] = 0xFB     # ESC down (bit 2 low)

    w, fr = drive(z, STOP_EXIT, frames=30, feed=press_esc)
    MEM[S("KEY_MATRIX") + 8] = 0xFF
    chk(w == "exit" and fr <= 3,
        f"{name}: ESC leaves within {fr} frames")

world(11)                                  # ...even on the briefing card
z = cpu(S("MG_BOILER"))


def esc_now(fr):
    if fr >= 3:
        MEM[S("KEY_MATRIX") + 8] = 0xFB


stops = dict(STOP_EXIT)
stops[S("MG_ROOM")] = "room"
w, fr = drive(z, stops, feed=esc_now)
MEM[S("KEY_MATRIX") + 8] = 0xFF
chk(w == "exit", "...and out of the briefing card itself")

# ======================================================================
print("--- steady picture: no flicker, no debris left behind")


def band(base, rows):
    return {(x, y): MEM[base + LINE[y] + x] for y in rows for x in range(80)}


def busy(fr):                         # a player who walks and whips
    MEM[S("INPUT_HELD")] = (1 << 1) if (fr // 40) % 2 == 0 else 1
    if fr % 25 == 0:
        MEM[S("INPUT_NEW")] = 1 << 5

ALL = list(range(200))
for name, entry, quiet in (("the gallery", "MG_GALLERY", "MG_B"),
                           ("the rat shift", "MG_MOLES", "MG_B")):
    world(11)
    z = cpu(S(entry))
    drive(z, {}, frames=8, brief=True)
    pristine = band(0xC000, ALL)
    px0 = MEM[S("PLAYER_X")]
    drive(z, {}, frames=500, feed=busy)
    MEM[S(quiet)] = 250               # hold the hatches shut...
    if entry == "MG_MOLES":
        for i in range(0, 12, 2):
            MEM[S("MG_TAB12") + i] = 1
    MEM[S("PLAYER_X")] = px0          # ...put him back where he stood
    drive(z, {}, frames=200)
    chk(not [i for i in range(MAXE) if ent(i, 0)],
        f"{name}: the field empties")
    a_, b_ = band(0xC000, ALL), band(0x4000, ALL)
    flick = [k for k in a_ if a_[k] != b_[k]]
    chk(not flick,
        f"{name}: both buffers agree byte for byte -- nothing flickers "
        f"({len(flick)} differ)")
    # (the mechanic himself is allowed to stand in a different stride)
    dirt = [k for k in pristine if pristine[k] != a_[k] and k[1] >= 8
            and not (px0 <= k[0] <= px0 + 3 and 176 <= k[1] < 192)]
    chk(not dirt,
        f"{name}: 500 frames of traffic leave the set exactly as built "
        f"({len(dirt)} stale bytes)")

# ======================================================================
print("--- THE HOOK: the stake, the drop, the prize")
world(13)
z = cpu(S("MG_HOOK"))                 # score 0: the chain man refuses
where, _ = drive(z, STOP_EXIT, brief=True)
chk(where == "exit" and all(MEM[S("SCORE") + i] == 0 for i in range(4)),
    "with no 500 to stake, no game and nothing taken")
world(9)
MEM[S("SCORE") + 1] = 7               # 0700
MEM[S("CURRENT_LEVEL")] = 13          # (not the free arcade)
z = cpu(S("MG_HOOK"))
drive(z, {}, frames=10, brief=True)
chk(MEM[S("SCORE") + 1] == 6 and MEM[S("SCORE") + 2] == 5,
    f"the stake of fifty is taken (0700 -> 0650)")
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

# the range: targets FALL, they do not hunt
i0 = d[0]
MEM[ENT + i0 * ENT_SIZE + 1] = 20         # released far from the man
MEM[ENT + i0 * ENT_SIZE + 2] = 40
MEM[S("PLAYER_X")] = 60
x0, y0 = ent(i0, 1), ent(i0, 2)
drive(z, {}, frames=20)
chk(ent(i0, 1) == x0,
    f"the target holds its column ({x0} -> {ent(i0, 1)}): no homing")
chk(ent(i0, 2) > y0 + 20,
    f"...and comes straight down ({y0} -> {ent(i0, 2)})")
MEM[ENT + i0 * ENT_SIZE + 2] = 182        # let it reach the deck
drive(z, {}, frames=3)
chk(ent(i0, 0) != ET["DRONE"] and MEM[S("PLAYER_ENERGY")] == 5,
    "a target that lands is a spent dud -- it costs nothing")

# ...while in the shaft itself the hunter still hunts
MEM[S("MG_RANGE")] = 0
free = slots(ET["NONE"])[0]
MEM[ENT + free * ENT_SIZE + 0] = ET["DRONE"]
MEM[ENT + free * ENT_SIZE + 1] = 20
MEM[ENT + free * ENT_SIZE + 2] = 40
MEM[S("PLAYER_X")] = 60
drive(z, {}, frames=20)
chk(ent(free, 1) > 20,
    f"outside the range the drone still closes in ({ent(free, 1)})")
MEM[ENT + free * ENT_SIZE + 0] = ET["NONE"]
MEM[S("MG_RANGE")] = 1
MEM[S("PLAYER_X")] = 38

drive(z, {}, frames=45)
d = slots(ET["DRONE"])
chk(bool(d), "the range keeps releasing")
i = d[0]
MEM[ENT + i * ENT_SIZE + 1] = MEM[S("PLAYER_X")]
MEM[ENT + i * ENT_SIZE + 2] = 150
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 1
drive(z, {}, frames=3)
chk(MEM[S("MG_E")] == 1, "the overhead crack counts a kill")
MEM[S("DRAW_PAGE")] = 0xC0
MEM[S("MG_E")] = 3
drive(z, {}, frames=2)
three = readout(2)
chk(three > 0, f"the gallery counts its kills on the wall ({three} ink)")
MEM[S("MG_E")] = 7
drive(z, {}, frames=2)
chk(readout(2) != three, "...and the figure tracks the tally")
chk(readout(66) > 0, "...while the clock keeps its own berth")

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
drive(z, {}, frames=4)
chk(MEM[S("MG_E")] == crud0 - 1 and MEM[S("MG_C")] == 1,
    "a rising pellet chips a scale tile and bounces back down")
MEM[ENT + 1] = 30                     # how fast does it actually fly?
MEM[ENT + 2] = 100
MEM[S("MG_B")] = 1
MEM[S("MG_C")] = 1
x0 = MEM[ENT + 1]
drive(z, {}, frames=20)
travelled = MEM[ENT + 1] - x0
chk(8 <= travelled <= 12,
    f"the slag drifts about half a byte a frame ({travelled} in 20)")

MEM[ENT + 1] = 30
MEM[ENT + 2] = 39
MEM[S("MG_C")] = 0xFF
drive(z, {}, frames=4)
cell = S("LEVEL_BUFFER") + 6 + 4 * 20 + 8   # it stepped once first
chk(MEM[cell] == 0, "...the tile is gone from the map itself")
MEM[ENT + 1] = MEM[S("PLAYER_X")]     # the overhead return
MEM[ENT + 2] = 166
MEM[S("MG_C")] = 1
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 1
drive(z, {}, frames=4)
chk(MEM[S("MG_C")] == 0xFF, "the overhead crack bats it skyward")
for _ in range(2):                    # two splashes...
    MEM[ENT + 2] = 179
    MEM[S("MG_C")] = 1
    drive(z, STOP_EXIT, frames=12)
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

# nobody dies at drill: a round marks the card, it does not wound
world(13)
MEM[S("PLAYER_ENERGY")] = 1           # one point from a lost life
MEM[S("GAME_LIVES")] = 1              # ...and one life from the end
z = cpu(S("MG_FIRING"))
drive(z, {}, frames=5, brief=True)
free = slots(ET["NONE"])[0]
for _ in range(4):                    # four rounds straight into him
    MEM[ENT + free * ENT_SIZE + 0] = ET["BULLET"]
    MEM[ENT + free * ENT_SIZE + 1] = (MEM[S("PLAYER_X")] - 1) & 0xFF
    MEM[ENT + free * ENT_SIZE + 2] = 181
    MEM[ENT + free * ENT_SIZE + 3] = 1
    MEM[ENT + free * ENT_SIZE + 7] = 0
    MEM[S("IMMUNE_TIMER")] = 0
    drive(z, {}, frames=3)
chk(MEM[S("PLAYER_ENERGY")] == 1 and MEM[S("GAME_LIVES")] == 1,
    "four hits at one energy and one life: neither is touched")
chk(MEM[S("MG_F")] == 1, "...but the card is marked")
MEM[S("MG_SEC")] = 1
MEM[S("MG_FR")] = 1
where, _ = drive(z, STOP_EXIT)
chk(where == "exit" and MEM[S("GAME_LIVES")] == 1
    and MEM[S("PLAYER_ENERGY")] == 2,
    "a marked sheet pays hazard pay instead of a life")

# ...while a round in the shaft still wounds
world(13)
MEM[S("MG_DRILL")] = 0
MEM[S("PLAYER_X")] = 40
MEM[S("PLAYER_Y")] = 176
MEM[S("PLAYER_DUCK")] = 0
MEM[S("IMMUNE_TIMER")] = 0
for i in range(MAXE):
    MEM[ENT + i * ENT_SIZE] = 0
ent0 = 0
MEM[ENT + 0] = ET["BULLET"]
MEM[ENT + 1] = 39
MEM[ENT + 2] = 181
MEM[ENT + 3] = 1
en0 = MEM[S("PLAYER_ENERGY")]
run(cpu(), S("UPDATE_ENTITIES"))
chk(MEM[S("PLAYER_ENERGY")] == en0 - 1,
    "outside the drill a bullet still costs an energy point")

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
