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

# ----- every guard has deck under his boots (B8)
cm = S("LEVEL_BUFFER") + 6
world(5)
z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=6, brief=True)
under = [MEM[cm + row * 20 + 1] for row in (12, 18, 24)]
chk(all(t in (2, 3) for t in under),
    f"each counter reaches the door the queue comes from (tiles {under})")

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

# ----- honest odds, a word that stays off the HUD, a refusal that is true
def prize_path(x):
    world(13)
    z = cpu(S("HK_PRIZE"))
    z.ix = ENT
    MEM[ENT + 1] = x
    MEM[ENT + 2] = 176
    stops = {S("HKP_CLEAN"): "clean"}
    n = 0
    while z.pc not in stops and z.pc != 0xFFFF and n < 200000:
        if n == 0:
            z.push(0xFFFF)
        z.step()
        n += 1
    return "clean" if z.pc in stops else "sloppy"


chk([prize_path(x) for x in (32, 33, 34, 35, 36)] ==
    ["clean", "sloppy", "sloppy", "sloppy", "clean"],
    "the good odds fall where the claw hangs over a crate (x mod 4 = 0)")

world(9)
MEM[S("SCORE") + 1] = 7
MEM[S("CURRENT_LEVEL")] = 13
z = cpu(S("MG_HOOK"))
drive(z, {}, frames=10, brief=True)
MEM[S("MG_FL")] = S("TXT_HK_LIFE") & 0xFF      # the longest prize word
MEM[S("MG_FL") + 1] = S("TXT_HK_LIFE") >> 8
MEM[S("MG_FLT")] = 52
drive(z, {}, frames=20)
said = [band(b, [112 + r for r in range(7)]) for b in (0x4000, 0xC000)]
chk(all(sum(1 for v in d.values() if v) > 40 for d in said),
    "the prize word is spoken on row 14, in both pages")
drive(z, {}, frames=40)
top = band(0xC000, range(0, 8))
HUD = lambda x: 2 <= x < 9 or 12 <= x < 16 or 44 <= x < 60 or 66 <= x < 73
stray = [k for k, v in top.items() if v and not HUD(k[0])]
gone = [band(b, [112 + r for r in range(8)]) for b in (0x4000, 0xC000)]
chk(not stray and all(not any(d.values()) for d in gone),
    f"...then wiped from both, and never on the HUD line ({len(stray)} stray)")

# ----- a cabinet the claw hangs in: the cable, and nothing left behind
world(9)
MEM[S("SCORE") + 1] = 7
MEM[S("CURRENT_LEVEL")] = 13
z = cpu(S("MG_HOOK"))
drive(z, {}, frames=8, brief=True)
EP = S("ENTITY_PREV")                 # slot 0's {x,y} per buffer


def hook_rects():
    return [(MEM[EP + 2 * k], MEM[EP + 2 * k + 1]) for k in (0, 1)]


def off_cast(k, rects, px):
    x, y = k
    if px <= x <= px + 3 and 176 <= y < 192:
        return False
    return not any(hx <= x <= hx + 3 and hy <= y < hy + 16
                   for hx, hy in rects)


SET = list(range(120, 192))           # rows 15-23: lid down to the bin
built = {b: band(b, SET) for b in (0x4000, 0xC000)}
r0 = hook_rects()
px = MEM[S("PLAYER_X")]
chk(px == 74 and MEM[S("PLAYER_FACING")] == 1,
    "the mechanic stands outside the cabinet, facing the machine")


def fire_once(fr):
    if fr == 1:
        MEM[S("INPUT_NEW")] = INP_FIRE


drive(z, {}, frames=12, feed=fire_once)
hx = MEM[ENT + 1]
low = min(y for _, y in hook_rects())
cable = [all(MEM[b + LINE[y] + hx + 1] & 0x55 == 0x04
             for y in range(143, low)) for b in (0x4000, 0xC000)]
chk(MEM[S("MG_D")] == 1 and low > 150 and all(cable),
    f"the claw goes down on a cable: pen 2 on lines 143-{low - 1} "
    f"of its stem, in both pages")
drive(z, {}, frames=70)
rects = r0 + hook_rects()
debris = [k for b in built for k, v in band(b, SET).items()
          if off_cast(k, rects, px) and v != built[b][k]]
chk(MEM[S("MG_D")] == 0 and MEM[S("MG_A")] == 2 and not debris,
    f"back on its rail, the cable is wound in: the cabinet is as built "
    f"({len(debris)} stale bytes)")
berth = band(0xC000, range(0, 8))
chk(not any(v for (x, _), v in berth.items() if 66 <= x < 73)
    and any(v for (x, _), v in berth.items() if 12 <= x < 16),
    "drops left sit on the left berth with the hook beside them; "
    "the clock's berth stays dark")

world(13)
z = cpu(S("MG_HOOK"))                 # broke: the door is not spent
MEM[S("MG_MARQ")] = 2
MEM[S("SHOWS_DONE")] = 0x04           # (mg_enter had set marquee 2's bit)
where, _ = drive(z, STOP_EXIT, brief=True)
chk(where == "exit" and not (MEM[S("SHOWS_DONE")] & 0x04),
    "turned away for want of 50: the door stays open, as the line promises")

# ======================================================================
print("--- THE BLACK MARKET: the telegraph, the catch, the eye")
world(10)
z = cpu(S("MG_MARKET"))
drive(z, {}, frames=100, brief=True)
chk(bool(slots(ET["DRIP"])), "the pipe telegraphs (drips flow)")
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

# ----- the eye, the tileset, the hold (audit B4)
TS, TSN = S("TILESET"), 59 * 32
world(10)
tiles0 = bytes(MEM[TS:TS + TSN])
z = cpu(S("MG_MARKET"))
drive(z, {}, frames=8, brief=True)
flips, bad = [], []
a_prev = MEM[S("MG_A")]
for fr in range(600):
    busy(fr)
    w_, _ = drive(z, {S("MG_EXIT"): "exit", S("MV_HOLD"): "hold"}, frames=1)
    if w_ in ("exit", "hold"):        # the show ended: stop HERE, never
        break                         # reload a fake level behind it
    a_now = MEM[S("MG_A")]
    if a_now != a_prev:
        want = 90 if a_now else 140
        flips.append((a_now, MEM[S("MG_B")]))
        if MEM[S("MG_B")] != want:
            bad.append((fr, a_now, MEM[S("MG_B")]))
        if a_now:                       # just turned red: the pipe says so
            cm = MEM[S("CURRENT_MAP")] | MEM[S("CURRENT_MAP") + 1] << 8
            cell = MEM[cm + 1 * 20 + 2]
            t21 = MEM[TS + 21 * 32:TS + 22 * 32]
            seen = [bytes(MEM[base + LINE[8 + l] + 8:base + LINE[8 + l] + 12])
                    for base in (0x4000, 0xC000) for l in range(8)]
            want_px = [bytes(t21[l * 4:l * 4 + 4]) for l in range(8)] * 2
            red_ok = cell == 21 and seen == want_px
    a_prev = a_now
chk(bytes(MEM[TS:TS + TSN]) == tiles0,
    f"the market leaves the tileset alone ({len(flips)} flips in 600 frames)")
chk(flips and not bad,
    f"every flip sets its own phase length: 140 white, 90 red {bad[:3]}")
chk(any(a for a, _ in flips) and red_ok,
    "when the eye turns red the pipe on screen turns red, in both pages")

world(10)
z = cpu(S("MG_MARKET"))
drive(z, {}, frames=8, brief=True)
pk = 6                                   # a package pinned in mid-air
a0 = MEM[S("MG_A")]
MEM[S("MG_B")] = 1
held = True
for _ in range(12):
    MEM[ENT + pk * ENT_SIZE + 0] = ET["PROJ"]
    MEM[ENT + pk * ENT_SIZE + 1] = 10
    MEM[ENT + pk * ENT_SIZE + 2] = 40
    drive(z, {}, frames=1)
    held &= MEM[S("MG_A")] == a0
MEM[ENT + pk * ENT_SIZE + 0] = 0
drive(z, {}, frames=2)
chk(held and MEM[S("MG_A")] != a0,
    "the eye holds its flip while stock is in the air, then flips")


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

# ----- he is seen in front of a shut hatch; one crack, one rat (B7)
world(8)
z = cpu(S("MG_MOLES"))
drive(z, {}, frames=10, brief=True)
tab12 = S("MG_TAB12")
for i in range(12):
    MEM[tab12 + i] = 0                # every hatch shut and settled
MEM[S("MG_B")] = 250                  # no new nose for a while
MEM[S("PLAYER_X")] = 36               # right in front of hatch 1
drive(z, {}, frames=4)
him = [band(b, range(176, 192)) for b in (0x4000, 0xC000)]
seen = [sum(1 for (x, y), v in d.items() if 36 <= x < 40 and v) for d in him]
MEM[S("PLAYER_X")] = 26
drive(z, {}, frames=4)
empty = [sum(1 for (x, y), v in band(b, range(176, 192)).items()
             if 36 <= x < 40 and v) for b in (0x4000, 0xC000)]
chk(all(a != b for a, b in zip(seen, empty)),
    f"in front of a shut hatch the mechanic is drawn, in both pages "
    f"(his ink {seen} vs the bare grate {empty})")

world(8)
z = cpu(S("MG_MOLES"))
drive(z, {}, frames=10, brief=True)
for i in range(12):
    MEM[tab12 + i] = 0
MEM[S("MG_B")] = 250
MEM[tab12 + 8] = 60                   # red nose, hatch 4 (36,160) high
MEM[tab12 + 9] = 0
MEM[tab12 + 2] = 60                   # the white one, hatch 1 (36,176)
MEM[tab12 + 3] = 1
MEM[S("MG_E")] = 0
MEM[S("MG_F")] = 0
MEM[S("PLAYER_X")] = 29
MEM[S("PLAYER_FACING")] = 1


def diag(fr):
    MEM[S("INPUT_HELD")] = INP_UP | 2      # UP + RIGHT
    if fr == 1:
        MEM[S("INPUT_NEW")] = INP_ACT


drive(z, {}, frames=12, feed=diag)
chk(MEM[S("MG_E")] == 1 and MEM[S("MG_F")] == 0,
    f"a diagonal crack takes the high rat and spares the white one below "
    f"(tally {MEM[S('MG_E')]}, voided {MEM[S('MG_F')]})")

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

# ----- falling slag chips too, and the chip is black in both pages (B9)
world(12)
z = cpu(S("MG_BOILER"))
drive(z, {}, frames=5, brief=True)
crud0 = MEM[S("MG_E")]
MEM[S("MG_C")] = 1                    # FALLING into intact crust
MEM[ENT + 1] = 46
MEM[ENT + 2] = 22
drive(z, {}, frames=3)
chk(MEM[S("MG_E")] == crud0 - 1,
    "slag falling into intact scale chips it (no passing through bricks)")
cm = S("LEVEL_BUFFER") + 6
holes = [(c, r) for r in (2, 3, 4) for c in range(20)
         if MEM[cm + r * 20 + c] == 0]
drive(z, {}, frames=3)
def cell_ink(base, c, r):
    return sum(1 for l in range(8) for x in range(4)
               if MEM[base + LINE[r * 8 + l] + c * 4 + x])
dirty = [(c, r) for (c, r) in holes
         for b in (0x4000, 0xC000) if cell_ink(b, c, r)
         and not (abs(c * 4 - MEM[ENT + 1]) < 5 and abs(r * 8 - MEM[ENT + 2]) < 17)]
chk(holes and not dirty,
    f"every chipped cell is black in both pages ({len(holes)} chipped, "
    f"{len(dirty)} still showing crate)")

# ----- a swing that counts, a drum to play in, a pellet that glows (P1)
def boiler():
    world(12)
    z = cpu(S("MG_BOILER"))
    drive(z, {}, frames=5, brief=True)
    return z


z = boiler()
px, py = MEM[S("PLAYER_X")], MEM[S("PLAYER_Y")]
MEM[S("PLAYER_FACING")] = 0
MEM[ENT + 1] = px + 5                 # up and ahead: the diagonal's box
MEM[ENT + 2] = py - 10
MEM[S("MG_C")] = 1
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 2
drive(z, {}, frames=1)
chk(MEM[S("MG_C")] == 0xFF, "a diagonal crack (UP and a direction) bats "
    "a falling pellet too")
z = boiler()
MEM[ENT + 1] = MEM[S("PLAYER_X")]
MEM[ENT + 2] = 166
MEM[S("MG_C")] = 1
MEM[S("WHIP_TIMER")] = WHIP_T = 9     # the rope's first frame out
MEM[S("WHIP_DIR")] = 1
drive(z, {}, frames=1)
chk(MEM[S("MG_C")] == 0xFF, "the rope bats it on its first frame out, "
    "not only on one frame of the swing")
pings = []


def keep_playing(fr):
    busy(fr)
    MEM[S("MG_A")] = 3                # endless pellets, endless scale
    MEM[S("MG_E")] = 48
    pings.append(MEM[ENT + 1])


z = boiler()
pristine = {b: band(b, ALL) for b in (0x4000, 0xC000)}
w, _ = drive(z, STOP_EXIT, frames=3000, feed=keep_playing)
chk(w == "time" and 8 <= min(pings) and max(pings) <= 69,
    f"3000 frames: the slag stays between the drum's walls "
    f"(x {min(pings)}..{max(pings)}, ink within bytes 8..71)")
MEM[ENT] = ET["DYING"]                # park it
MEM[S("PLAYER_X")] = 38
drive(z, {}, frames=10)
flick = sum(1 for k, v in band(0x4000, ALL).items()
            if v != MEM[0xC000 + LINE[k[1]] + k[0]])
dirt = [k for b in pristine for k, v in band(b, ALL).items()
        if k[1] >= 8 and not 16 <= k[1] < 40 and v != pristine[b][k]
        and not (38 <= k[0] <= 41 and 176 <= k[1] < 192)   # the man
        and not (30 <= k[0] <= 33 and 100 <= k[1] < 116)]  # the serve
chk(not flick and not dirt,
    f"the drum after the game: nothing flickers ({flick}), the set "
    f"outside the crust is as built ({len(dirt)} stale)")


def berth_ink(b):
    return sum(1 for yy in range(8) for x in range(2, 9)
               if MEM[b + LINE[yy] + x])


z = boiler()
drive(z, {}, frames=4)
chk(MEM[ENT] == ET["MGSPR"] and all(
        MEM[b + LINE[yy] + x] for b in (0x4000, 0xC000)
        for yy, x in ((2, 13), (3, 13))),
    "the pellet is the slag sprite, and the slag icon sits on the berth")
two = [berth_ink(b) for b in (0x4000, 0xC000)]
MEM[ENT + 2] = 179
MEM[S("MG_C")] = 1
drive(z, {}, frames=4)
one = [berth_ink(b) for b in (0x4000, 0xC000)]
chk(MEM[S("MG_A")] == 2 and all(two) and all(one) and two != one,
    f"the spare pellets are counted on the left berth: 02, then 01 after "
    f"one hits the floor (ink {two} -> {one})")

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

# ----- the height is its own die, not the last side again (B6)
world(29)
MEM[S("RND_STATE")] = 0x5A
log = []
for _ in range(200):
    for i in range(MAXE * ENT_SIZE):
        MEM[ENT + i] = 0
    run(cpu(), S("FL_SALVO"))
    log.append((MEM[ENT + 3] == 1, MEM[ENT + 2] == 188))   # (left, low)
pairs = [(log[i][0], log[i + 1][1]) for i in range(len(log) - 1)]
after_left = [low for left, low in pairs if left]
p_low = sum(after_left) / max(1, len(after_left))
chk(0.3 <= p_low <= 0.7,
    f"a salvo's height does not repeat the last one's side "
    f"(P(low | left before) = {p_low:.2f})")

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

# ----- the sets: the record format, read independently (S1)
ROOMS = ["ROOM_TAPPER", "ROOM_MOLES", "ROOM_HOOK", "ROOM_MARKET",
         "ROOM_GALLERY", "ROOM_BOILER", "ROOM_FIRING"]
# Inside a show only update_player reads the map (find_ladder): ladders
# climb.  check_keycards and check_doors run only in the shaft's own
# loop, so doors are fair decoration here; cards, kits, switches and
# vaults would be inert too, but a card you cannot take is a lie.
LIVE = {4: "ladder", 8: "card", 9: "card", 10: "card", 11: "card",
        12: "card", 20: "medkit", 23: "switch", 24: "switch",
        25: "vault", 33: "arch foot"}
LADDER_OK = {"ROOM_TAPPER"}          # the canteen's service ladders


def parse_room(a):
    """Predict mg_room's map and rects from the bytes of a record."""
    m = [0] * 500
    px_, py_, floor = MEM[a], MEM[a + 1], MEM[a + 2]
    a += 3
    for c in range(20):
        m[480 + c] = floor
    while MEM[a] != 0xFF:
        t, col, row, n = MEM[a:a + 4]
        a += 4
        step = 20 if n & 0x80 else 1
        for i in range(n & 0x7F):
            m[row * 20 + col + i * step] = t
    a += 1
    rects = [[0, 80, 192, 8, 1]]
    while MEM[a] != 0xFF:
        rects.append(list(MEM[a:a + 5]))
        a += 5
    return m, rects, (px_, py_)


for room in ROOMS:
    world(11)
    want_m, want_r, want_p = parse_room(S(room))
    z = cpu()
    z.sethl(S(room))
    run(z, S("MG_ROOM"), max_steps=60_000_000)
    cm = S("LEVEL_BUFFER") + 6
    cr = MEM[S("CURRENT_RECTS")] | MEM[S("CURRENT_RECTS") + 1] << 8
    got_m = list(MEM[cm:cm + 500])
    got_r, a = [], cr
    while MEM[a] != 0xFF:
        got_r.append(list(MEM[a:a + 5]))
        a += 5
    live = sorted({LIVE[t] for t in got_m if t in LIVE
                   and not (t == 4 and room in LADDER_OK)})
    chk(got_m == want_m and got_r == want_r and not live,
        f"{room[5:].lower()}: built exactly as its record reads, "
        f"{len(got_r)} rect(s), no live tiles {live or ''}")
    if room == "ROOM_BOILER":
        scale = sum(1 for r in (2, 3, 4) for c in range(20)
                    if got_m[r * 20 + c] == 5)
        chk(scale == 48, f"...the boiler's crust is 48 tiles of scale ({scale})")

# ----- readouts paint on change; the quota counts down; vitals (S2, S3)
def row0(base, x0, x1):
    return bytes(MEM[base + LINE[yy] + x] for yy in range(8)
                 for x in range(x0, x1))


world(8)
z = cpu(S("MG_MOLES"))
drive(z, {}, frames=10, brief=True)
MEM[S("MG_E")] = 15
drive(z, {}, frames=3)
met = row0(0xC000, 2, 9), row0(0x4000, 2, 9)
MEM[S("MG_E")] = 20
drive(z, {}, frames=3)
chk(row0(0xC000, 2, 9) == met[0] and met[0] == met[1] and any(met[0]),
    "the quota met reads 00 -- and beating it still reads 00, in both pages")
MEM[S("MG_FR")] = 1                  # the next frame ticks a second
drive(z, {}, frames=3)
chk(row0(0xC000, 66, 73) == row0(0x4000, 66, 73),
    "two frames after a second ticks, both pages show the same clock")

world(5)
z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=12, brief=True)
before = row0(0xC000, 44, 60)
MEM[S("PLAYER_ENERGY")] = 4          # as a report would leave it
drive(z, {}, frames=3)
after = row0(0xC000, 44, 60), row0(0x4000, 44, 60)
chk(any(before) and after[0] == after[1] and after[0] != before,
    "energy and lives sit on the top line, and a change reaches both pages")

for room in ROOMS:
    world(11)
    m, _r, _p = parse_room(S(room))
    if any(m[:20]):
        break
chk(not any(m[:20]), "row 0 is empty in every set: it belongs to the readouts")

# ----- the card: centred, a prompt you can read, the way out (S4)
def pen_of(b, right):
    v = (b << 1) & 0xFF if right else b
    return (((v >> 7) & 1) | ((v >> 3) & 1) << 1
            | ((v >> 5) & 1) << 2 | ((v >> 1) & 1) << 3)


def pens(base, y0, y1, x0=0, x1=80):
    out = set()
    for yy in range(y0, y1):
        for x in range(x0, x1):
            b = MEM[base + LINE[yy] + x]
            for r in (0, 1):
                p_ = pen_of(b, r)
                if p_:
                    out.add(p_)
    return out


def span(base, y0, y1):
    xs = [x for yy in range(y0, y1) for x in range(80)
          if MEM[base + LINE[yy] + x]]
    return (min(xs), max(xs)) if xs else None


FONT = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ- ")
for name in BRIEFS:
    p_, strs = S(name), []
    while True:
        ptr = MEM[p_] | MEM[p_ + 1] << 8
        p_ += 2
        if not ptr:
            break
        strs.append(cstr(ptr))
    chk(all(set(x) <= FONT and 3 * len(x) <= 80 for x in strs[1:]),
        f"{name[4:].lower()}: {len(strs) - 1} rules, every glyph in the font, "
        f"every line fits")

world(49)                              # an admin floor: pen 6 was dark here
z = cpu(S("MG_GALLERY"))
drive(z, {S("MG_ROOM"): "room"}, frames=30)
both = [span(b, 184, 192) for b in (0x4000, 0xC000)]
chk(both[0] and both[0] == both[1], "the card's last line says how to leave, in both pages")
chk(pens(0xC000, 168, 176) == {9}, "the SPACE prompt is pen 9 -- bright green on every floor")
lines = [(24, 40), (72, 80), (88, 96), (104, 112), (120, 128), (168, 176), (184, 192)]
off = [(y0, sp) for y0, y1 in lines for sp in [span(0xC000, y0, y1)]
       if sp and abs((sp[0] + sp[1] + 1) / 2 - 40) > 2]
chk(not off, f"every line of the card is centred {off}")

# ----- the verdict: coloured by outcome, on row 5, quick to leave (S5)
def to_verdict(entry, fix):
    world(11)
    for i in range(4):
        MEM[S("SCORE") + i] = (0, 1, 0, 0)[i]
    z = cpu(S(entry))
    drive(z, {}, frames=30, brief=True)
    fix()
    w, _ = drive(z, {S("MV_HOLD"): "hold"}, frames=4000)
    return z, w


def gal_quota():
    MEM[S("MG_E")] = 8
    MEM[S("MG_SEC")] = 1
    MEM[S("MG_FR")] = 1


def gal_short():
    MEM[S("MG_E")] = 3
    MEM[S("MG_SEC")] = 1
    MEM[S("MG_FR")] = 1


z, w = to_verdict("MG_GALLERY", gal_quota)
chk(w == "hold" and pens(0xC000, 40, 47, 4, 76) == {9} == pens(0x4000, 40, 47, 4, 76),
    "a quota met is said in green, on row 5, in both pages")
z, w = to_verdict("MG_GALLERY", gal_short)
chk(w == "hold" and pens(0xC000, 40, 47, 4, 76) == {5},   # between the walls
    "a quota missed is said in red")

world(5)                               # the canteen's set survives its verdict
z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=30, brief=True)
pic = band(0xC000, range(88, 104))
MEM[S("MG_E")] = 12
w, _ = drive(z, {S("MV_HOLD"): "hold"})
drive(z, {}, frames=4)
chk(w == "hold" and band(0xC000, range(88, 104)) == pic,
    "the verdict leaves the set alone: the counter is still standing")


def early(fr):
    MEM[S("INPUT_NEW")] = INP_ACT if fr == 20 else 0


def late(fr):
    MEM[S("INPUT_NEW")] = INP_ACT if fr == 60 else 0


for feed, should, what in ((early, False, "a key in the first second is ignored"),
                           (late, True, "...after it, Z leaves at once")):
    z, w = to_verdict("MG_GALLERY", gal_quota)
    w2, fr = drive(z, STOP_EXIT, frames=100, feed=feed)
    chk((w2 == "exit") == should, f"{what} ({w2} at {fr})")

# ----- the range: a light that remembers a mark; shots and misses heard
world(29)
z = cpu(S("MG_FIRING"))
drive(z, {}, frames=10, brief=True)
cm = S("LEVEL_BUFFER") + 6
def cell_is(base, c, r, t):
    return all(MEM[base + LINE[r * 8 + l] + c * 4 + x] ==
               MEM[S("TILESET") + t * 32 + l * 4 + x]
               for l in range(8) for x in range(4))
clean = MEM[cm + 3 * 20 + 9] == 54 == MEM[cm + 3 * 20 + 10]
MEM[S("MG_F")] = 1                    # a paint round found him
drive(z, {}, frames=3)
red = (MEM[cm + 3 * 20 + 9] == 58 == MEM[cm + 3 * 20 + 10]
       and all(cell_is(b, c, 3, 58) for b in (0x4000, 0xC000) for c in (9, 10)))
chk(clean and red, "the sheet's light is green until the first mark, then red "
    "in the map and in both pages")
MEM[S("MG_B")] = 1                    # a salvo this frame
MEM[S("SFX_TIMER")] = 0
drive(z, {}, frames=1)
chk(MEM[S("SFX_TYPE")] == 4, "a salvo is heard")

world(29)                              # the dressed range stays steady
z = cpu(S("MG_FIRING"))
drive(z, {}, frames=8, brief=True)
pristine = band(0xC000, ALL)
px0 = MEM[S("PLAYER_X")]
drive(z, {}, frames=400, feed=busy)
MEM[S("MG_B")] = 250                  # cease fire; the rounds in the air
MEM[S("PLAYER_X")] = px0              # fly out on their own
drive(z, {}, frames=200)
chk(not [i for i in range(MAXE) if ent(i, 0)], "the range: the field empties")
a_, b_ = band(0xC000, ALL), band(0x4000, ALL)
flick = [k for k in a_ if a_[k] != b_[k]]
dirt = [k for k in pristine if pristine[k] != a_[k] and k[1] >= 8
        and not 24 <= k[1] < 32
        and not (px0 <= k[0] <= px0 + 3 and 176 <= k[1] < 192)]
chk(not flick and not dirt, f"the dressed range: nothing flickers ({len(flick)}), "
    f"400 frames leave it as built ({len(dirt)} stale)")

world(21)                              # a dud on the gallery deck is heard
z = cpu(S("MG_GALLERY"))
drive(z, {}, frames=60, brief=True)
d = slots(ET["DRONE"])
heard = False
if d:
    MEM[ENT + d[0] * ENT_SIZE + 1] = 20
    MEM[ENT + d[0] * ENT_SIZE + 2] = 180
    MEM[S("PLAYER_X")] = 60
    MEM[S("SFX_TIMER")] = 0
    drive(z, {}, frames=3)
    heard = MEM[S("SFX_TYPE")] == 6
chk(heard, "a drone that reaches the deck unwhipped is heard landing")

# ----- the Eye is the whole ceiling; a moment's grace; the count (P3)
def no_stock():
    for i in range(1, MAXE):
        if ent(i, 0) == ET["PROJ"]:
            MEM[ENT + i * ENT_SIZE] = 0


world(17)
z = cpu(S("MG_MARKET"))
drive(z, {}, frames=8, brief=True)
MEM[S("MG_A")] = 0
MEM[S("MG_F")] = 0
MEM[S("MG_C")] = 200                  # no new stock to hold the flip
no_stock()
MEM[S("MG_B")] = 1
drive(z, {}, frames=2)
cm = S("LEVEL_BUFFER") + 6
row1 = [MEM[cm + 20 + c] for c in range(20)]
shown = all(cell_is(b, c, 1, 21) for b in (0x4000, 0xC000) for c in range(20))
chk(MEM[S("MG_A")] == 1 and row1 == [21] * 20 and shown,
    "when the Eye opens the whole ceiling main turns red, in both pages")
walked = []
for fr in range(40):
    MEM[S("INPUT_HELD")] = 2 if fr % 2 else 1     # fidgeting on the spot
    drive(z, {S("MV_HOLD"): "hold"}, frames=1, feed=lambda f, fr=fr:
          MEM.__setitem__(S("INPUT_HELD"), 2 if fr % 2 else 1))
    walked.append(MEM[S("MG_F")])
first = next((i for i, f in enumerate(walked) if f == 1), None)
chk(first is not None and 29 <= first <= 34,
    f"moving in the first half-second of red is forgiven; after it the "
    f"Eye sees (caught on red frame {first})")

world(17)
z = cpu(S("MG_MARKET"))
drive(z, {}, frames=8, brief=True)
before = row0(0xC000, 2, 9)
MEM[S("MG_E")] = 7
drive(z, {}, frames=3)
chk(row0(0xC000, 2, 9) != before and row0(0x4000, 2, 9) == row0(0xC000, 2, 9),
    "the market counts its catches on the left berth, beside a bundle")

# ----- red rats you can see; the quota rings; a void shows at once (P2)
world(9)
z = cpu(S("MG_MOLES"))
drive(z, {}, frames=10, brief=True)
tab12 = S("MG_TAB12")
for i in range(12):
    MEM[tab12 + i] = 0
MEM[S("MG_B")] = 250
MEM[tab12] = 60                       # a red nose at hatch 0 (16,176)
drive(z, {}, frames=3)
chk(5 in pens(0xC000, 176, 184, 16, 20) and 5 in pens(0x4000, 176, 184, 16, 20),
    "a rat out of its hatch is red: it stands off the black")
icon_red = pens(0xC000, 0, 16, 12, 16)
MEM[S("MG_E")] = 14
MEM[S("PLAYER_X")] = 24
MEM[S("PLAYER_FACING")] = 1
MEM[S("WHIP_TIMER")] = 7
MEM[S("WHIP_DIR")] = 0
MEM[S("SFX_TIMER")] = 0
drive(z, {}, frames=3)
chk(MEM[S("MG_E")] == 15 and MEM[S("SFX_TYPE")] == 3,
    "the fifteenth rat rings the quota bell, not the kill")
MEM[tab12 + 2] = 60                   # now the white one, hatch 1 (36,176)
MEM[tab12 + 3] = 1
MEM[S("PLAYER_X")] = 44
MEM[S("WHIP_TIMER")] = 7
drive(z, {}, frames=4)
icon_now = pens(0xC000, 0, 16, 12, 16)
chk(MEM[S("MG_F")] == 1 and 5 in icon_red and 5 not in icon_now
    and icon_now == pens(0x4000, 0, 16, 12, 16),
    f"whip the white one and the rat beside the count turns white at once "
    f"(pens {sorted(icon_red)} -> {sorted(icon_now)})")

# ----- the canteen: SPACE serves, the shelf empties, the set holds (P7)
def guard_lane():
    for i in range(3):
        if ent(i, 0) == ET["RIOT"] and not ent(i, 3) & 0x80:
            return ent(i, 9)
    return None


def run_serving(z, frames, key):
    n0 = MEM[S("MG_E")]
    for fr in range(frames):
        lane = guard_lane()
        if lane is not None:
            MEM[S("MG_A")] = lane
        w_, _ = drive(z, {S("MV_HOLD"): "hold", S("MG_EXIT"): "exit"},
                      frames=1, feed=lambda f, fr=fr:
                      MEM.__setitem__(S("INPUT_NEW"), key if fr % 8 == 0 else 0))
        if w_ in ("hold", "exit"):          # ("time" just means the frame ran)
            return w_, MEM[S("MG_E")] - n0
    return None, MEM[S("MG_E")] - n0


world(5)
z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=8, brief=True)
pristine = band(0xC000, ALL)
w_, served = run_serving(z, 260, INP_FIRE)
chk(w_ is None and served >= 2, f"SPACE serves a tin just as Z does ({served} served)")
cm = S("LEVEL_BUFFER") + 6
gone = [c for c in range(4, 16) if MEM[cm + 3 * 20 + c] == 0]
blank = all(not any(MEM[b + LINE[24 + l] + c * 4 + x] for l in range(8) for x in range(4))
            for b in (0x4000, 0xC000) for c in gone)
chk(gone and len(gone) == MEM[S("MG_E")] and gone == list(range(4, 4 + len(gone))) and blank,
    f"one tin leaves the shelf per serve, in both pages ({len(gone)} gone)")
for x0 in (0, 1, 2, 3):               # served in the doorway itself
    world(5)
    z = cpu(S("MG_TAPPER"))
    drive(z, {}, frames=8, brief=True)
    MEM[S("MG_B")] = 250
    for i in range(4):
        MEM[ENT + i * ENT_SIZE] = 0
    MEM[ENT + 0] = ET["RIOT"]
    MEM[ENT + 1] = x0
    MEM[ENT + 2] = 176
    MEM[ENT + 3] = 0x81
    xs = []
    for _ in range(6):
        drive(z, {}, frames=1)
        xs.append(ent(0, 1))
    chk(max(xs) < 80 and ent(0, 0) in (0, ET["DYING"]),
        f"a guard served in the doorway at x={x0} leaves there, never "
        f"wrapping past the screen's end (x {xs})")
world(5)
z = cpu(S("MG_TAPPER"))
drive(z, {}, frames=8, brief=True)
pristine = band(0xC000, ALL)
w_, served = run_serving(z, 260, INP_FIRE)
MEM[S("MG_B")] = 250                  # the queue stops; the served walk out
drive(z, {}, frames=200)
a_, b_ = band(0xC000, ALL), band(0x4000, ALL)
flick = [k for k in a_ if a_[k] != b_[k]]
dirt = [k for k in pristine if pristine[k] != a_[k] and k[1] >= 8
        and not 24 <= k[1] < 32 and not 64 <= k[0] < 68]
chk(not [i for i in range(MAXE) if ent(i, 0)] and not flick and not dirt,
    f"the dressed canteen: field empty, nothing flickers ({len(flick)}), "
    f"the set is as built ({len(dirt)} stale)")

# ======================================================================
print("--- the frame around every show: aim, stillness, a clean slate")
ENTRIES = ["MG_TAPPER", "MG_MOLES", "MG_HOOK", "MG_MARKET", "MG_GALLERY",
           "MG_BOILER", "MG_FIRING"]


def to_sync(z):
    """Step to the next frame_sync: the frame just built is complete."""
    while z.pc != FS:
        z.step()


for entry in ("MG_GALLERY", "MG_TAPPER"):
    for k in (4, 5):                    # SPACE on an even and an odd frame
        world(11)
        z = cpu(S(entry))
        def card(fr, k=k):
            MEM[S("INPUT_NEW")] = INP_FIRE if fr == k else 0
        w, _ = drive(z, {S("MG_ROOM"): "room"}, feed=card)
        to_sync(z)
        shown_a = MEM[S("SHOWN_R12")] == 0x30
        chk(w == "room" and (MEM[S("DRAW_PAGE")] == 0x40) == shown_a,
            f"{entry.lower()[3:]}, card left on frame {k}: the show draws "
            f"into the HIDDEN page (draw #{MEM[S('DRAW_PAGE')]:02X}, "
            f"showing {'A' if shown_a else 'B'})")


def pages_differ():
    return sum(1 for yy in range(200) for x in range(80)
               if MEM[0x4000 + LINE[yy] + x] != MEM[0xC000 + LINE[yy] + x])


for entry in ENTRIES:
    world(11)
    for i in range(4):
        MEM[S("SCORE") + i] = (0, 1, 0, 0)[i]   # the hook takes its stake
    z = cpu(S(entry))
    drive(z, {}, frames=60, feed=busy, brief=True)
    z.sp = 0x1000                       # the show ends here, mid-stride
    z.sethl(S("TXT_MG_PAY"))
    z.pc = S("MG_VERDICT")
    w, _ = drive(z, {S("MV_HOLD"): "hold"})
    drive(z, {}, frames=20)
    diff = pages_differ()
    ink = [band(base, [40 + r for r in range(7)]) for base in (0x4000, 0xC000)]
    said = all(sum(1 for v in b.values() if v) > 30 for b in ink)   # row 5
    chk(w == "hold" and diff == 0 and said,
        f"{entry.lower()[3:]}: the verdict is ONE still picture with the line "
        f"in it ({diff} bytes differ between the pages)")

world(11)
MEM[S("MG_RANGE")] = 1
MEM[S("MG_DRILL")] = 1
run(cpu(), S("ARCH_SCAN"))
chk(MEM[S("MG_RANGE")] == 0 and MEM[S("MG_DRILL")] == 0,
    "a reloaded shaft forgets a show's house rules (range, drill)")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
