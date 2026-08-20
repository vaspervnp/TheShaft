#!/usr/bin/env python3
"""Execute the game's new enemies (the real assembled binary) and check
the rules the player will live by.

Bullets: from level 10 the first riot guard carries a rifle, one more
every 8 levels until all do.  A guard fires only at walkway height, on
the side he faces, never point-blank; the round flies at gun height,
where a DUCK (or a jump) clears it, and it dies on walls and slabs.

Drones: from level 20 a dispatcher sometimes launches a kamikaze that
homes on the player and detonates on contact for one energy point --
even through the immunity flicker it still detonates, just harmlessly.
The odds and the ceiling (1..3 aloft) climb with the level.  Any whip
crack that geometrically reaches it kills it: the overhead and the
diagonal, and the side crack once it has dropped to arm height (a
flyer still overhead escapes the side rope).  check_enemy_hit ignores
both new types -- they judge their own contact."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

ENT = S("ENTITIES")
ENT_SIZE, MAXE = 10, 8
(ET_NONE, ET_RIOT, ET_COAT, ET_THROW, ET_PROJ,
 ET_DYING, ET_DRIP, ET_STEAM, ET_BULLET, ET_DRONE) = range(10)
SFX_HIT, SFX_KILL = 2, 5
RECTS = 0x0900                        # a scratch rect list for probes

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(("OK   " if c else "FAIL ") + m)


def cpu(ix=None):
    z = Z80()
    z.sp = 0x1000
    if ix is not None:
        z.ix = ix
    return z


def world(px=40, py=160, duck=0, rects=()):
    for i in range(MAXE * ENT_SIZE):
        MEM[ENT + i] = 0
    a = RECTS
    for x, w, y, h, t in rects:            # the game's order: x,w,y,h
        MEM[a:a + 5] = bytes((x, w, y, h, t))
        a += 5
    MEM[a] = 0xFF
    MEM[S("CURRENT_RECTS")] = RECTS & 0xFF
    MEM[S("CURRENT_RECTS") + 1] = RECTS >> 8
    MEM[S("PLAYER_X")] = px
    MEM[S("PLAYER_Y")] = py
    MEM[S("PLAYER_DUCK")] = duck
    MEM[S("IMMUNE_TIMER")] = 0
    MEM[S("PLAYER_ENERGY")] = 5
    MEM[S("GAME_LIVES")] = 3
    MEM[S("SFX_TYPE")] = 0
    MEM[S("SFX_TIMER")] = 0
    MEM[S("WHIP_TIMER")] = 0
    MEM[S("WHIP_DIR")] = 0
    MEM[S("PLAYER_FACING")] = 0
    MEM[S("RND_STATE")] = 0x5A


def ent(i, type=0, x=0, y=0, dir=0, spd=0, xmin=0, xmax=0, t8=0, t9=0):
    r = ENT + i * ENT_SIZE
    MEM[r:r + 10] = bytes(v & 0xFF for v in
                          (type, x, y, dir, spd, xmin, xmax, 0, t8, t9))


def rec(i):
    r = ENT + i * ENT_SIZE
    return list(MEM[r:r + 10])


# ======================================================================
print("--- the rifle ramp (arm_riots, executed per level)")
for level, expect in ((1, 0), (9, 0), (10, 1), (17, 1), (18, 2),
                      (26, 3), (42, 4), (59, 4)):
    world()
    for i in range(4):
        ent(i, ET_RIOT, x=10 + i * 15, y=160, dir=1, spd=1)
    ent(4, ET_COAT, x=70, y=160, dir=1, spd=1)
    MEM[S("CURRENT_LEVEL")] = level
    MEM[S("FRAME_TICKS")] = 76
    run(cpu(), S("ARM_RIOTS"))
    armed = [i for i in range(4) if rec(i)[9]]
    chk(len(armed) == expect and armed == list(range(expect)),
        f"level {level}: {len(armed)} of 4 riot guards armed")
    chk(rec(4)[9] == 0, "...and the coat man never is")
    if expect >= 2:
        chk(rec(0)[8] == 30 and rec(1)[8] == 70,
            "...opening volleys staggered 40 frames apart")
chk(MEM[S("RND_STATE")] == 77, "the dice are seeded from the 300 Hz clock")

# ======================================================================
print("--- when the guard pulls the trigger (riot_fire)")


def fire(gx=20, gy=160, gdir=1, px=40, py=160):
    world(px, py)
    ent(0, ET_RIOT, x=gx, y=gy, dir=gdir, spd=1, t9=1)
    run(cpu(ix=ENT), S("RIOT_FIRE"))
    return rec(1) if rec(1)[0] == ET_BULLET else None


b = fire()
chk(b is not None and b[1] == 22 and b[2] == 165 and b[3] == 1,
    f"facing an intruder to his right: a round from the muzzle at gun "
    f"height {b and (b[1], b[2])}")
b = fire(gx=60, gdir=0xFF, px=30)
chk(b is not None and b[1] == 58 and b[3] == 0xFF,
    "facing left: the round flies left")
chk(fire(gdir=0xFF) is None, "an intruder BEHIND the shield is safe")
chk(fire(px=24) is None, "point-blank (under 6 bytes) is the shield's job")
chk(fire(py=112) is None, "a man on another walkway is not a target")
chk(fire(py=164) is not None, "...but a small step of height (4) is")

# ======================================================================
print("--- the bullet in flight (update_entities, frame by frame)")


def fly(duck=0, py=160, rects=(), bx=30, frames=60):
    world(50, py, duck, rects)
    ent(0, ET_BULLET, x=bx, y=165, dir=1)
    path = []
    for _ in range(frames):
        run(cpu(), S("UPDATE_ENTITIES"))
        path.append(rec(0)[1])
        if rec(0)[0] != ET_BULLET:
            break
    return path


p = fly()                                # standing man at x=50
chk(rec(0)[0] == ET_DYING and p[-1] == 49,
    f"standing: it finds the chest at x={p[-1]}")
chk(MEM[S("PLAYER_ENERGY")] == 4 and MEM[S("IMMUNE_TIMER")] > 0
    and MEM[S("SFX_TYPE")] == SFX_HIT,
    "...one energy, the flicker starts, the HIT noise plays")
p = fly(duck=1)
chk(MEM[S("PLAYER_ENERGY")] == 5 and max(p) >= 77,
    f"DUCKED: it passes over his head and dies on the far wall "
    f"(reached x={max(p)})")
p = fly(py=140)
chk(MEM[S("PLAYER_ENERGY")] == 5, "mid-jump: it passes under his boots")
p = fly(py=100, rects=((60, 4, 150, 40, 1),))
chk(MEM[S("PLAYER_ENERGY")] == 5 and rec(0)[0] == ET_DYING
    and max(p) <= 61,
    f"a crate stops it cold at x={max(p)}")

# ======================================================================
print("--- the drone (homing, detonation, the flicker)")
world(40, 160)
ent(0, ET_DRONE, x=10, y=8)
frames = 0
while rec(0)[0] == ET_DRONE and frames < 500:
    run(cpu(), S("UPDATE_ENTITIES"))
    frames += 1
chk(rec(0)[0] == ET_DYING and frames < 400,
    f"it homes in and detonates after {frames} frames")
chk(MEM[S("PLAYER_ENERGY")] == 4 and MEM[S("IMMUNE_TIMER")] > 0
    and MEM[S("SFX_TYPE")] == SFX_HIT,
    "...one energy, flicker, the blast noise")
world(40, 160)
MEM[S("IMMUNE_TIMER")] = 100
ent(0, ET_DRONE, x=40, y=150)
for _ in range(20):
    if rec(0)[0] != ET_DRONE:
        break
    run(cpu(), S("UPDATE_ENTITIES"))
chk(rec(0)[0] == ET_DYING and MEM[S("PLAYER_ENERGY")] == 5,
    "through the immunity flicker it still detonates -- harmlessly")

# ======================================================================
print("--- the dispatcher (update_drones: gates, odds, ceiling)")


def tries(level, n=200, keep=0):
    """n timer expiries at this level; count launches; keep the sky
    pre-filled with `keep` drones."""
    world()
    MEM[S("CURRENT_LEVEL")] = level
    spawned = 0
    for _ in range(n):
        for i in range(keep):
            ent(i, ET_DRONE, x=20 + i, y=20)
        for i in range(keep, MAXE):
            ent(i)
        MEM[S("DRONE_TIMER")] = 1
        run(cpu(), S("UPDATE_DRONES"))
        spawned += sum(1 for i in range(MAXE)
                       if rec(i)[0] == ET_DRONE) > keep
    return spawned


world()
MEM[S("CURRENT_LEVEL")] = 5
MEM[S("DRONE_TIMER")] = 1
run(cpu(), S("UPDATE_DRONES"))
chk(all(rec(i)[0] == 0 for i in range(MAXE)) and MEM[S("DRONE_TIMER")] == 1,
    "below level 20 the dispatcher does not even wind its clock")
p20, p40, p59 = tries(20), tries(40), tries(59)
chk(p20 <= 30, f"level 20: rare -- {p20}/200 expiries launch")
chk(p59 >= 100, f"level 59: busy -- {p59}/200")
chk(p20 < p40 < p59, f"the odds climb with the level ({p20}<{p40}<{p59})")
chk(tries(20, 100, keep=1) == 0, "level 20 ceiling: one aloft is the lot")
chk(tries(40, 100, keep=2) == 0, "level 40: two")
chk(tries(59, 100, keep=3) == 0
    and tries(59, 100, keep=2) > 0,
    "level 59: three aloft, never four")
world()
MEM[S("CURRENT_LEVEL")] = 59
MEM[S("DRONE_TIMER")] = 1
run(cpu(), S("UPDATE_DRONES"))
sp = [rec(i) for i in range(MAXE) if rec(i)[0] == ET_DRONE]
chk(bool(sp) and all(8 <= r[1] <= 71 and r[2] == 8 for r in sp),
    "a launch appears along the open top strip, under the ceiling")

# ======================================================================
print("--- the whip against the flyer: any aim the rope can reach")


def crack(wdir, dx=0, dy=-14, facing=0):
    world(40, 160)
    MEM[S("WHIP_DIR")] = wdir
    MEM[S("PLAYER_FACING")] = facing
    MEM[S("SCORE") + 3] = 0
    ent(0, ET_DRONE, x=40 + dx, y=160 + dy)
    run(cpu(), S("WHIP_HITS"))
    return rec(0)[0]


chk(crack(1) == ET_DYING and MEM[S("SFX_TYPE")] == SFX_KILL,
    "the OVERHEAD crack brings it down (and the kill sound plays)")
chk(crack(2, dx=3, dy=-10) == ET_DYING,
    "so does the rising DIAGONAL")
chk(crack(0, dx=4, dy=-6) == ET_DYING,
    "...and the SIDE crack too, once it has dropped to arm height")
chk(crack(0, dx=4, dy=-14) == ET_DRONE,
    "but the side rope stays at arm height: one still overhead escapes")
world(40, 160)
MEM[S("PLAYER_FACING")] = 0
ent(0, ET_RIOT, x=44, y=160, dir=1, spd=1)
run(cpu(), S("WHIP_HITS"))
chk(rec(0)[0] == ET_DYING, "...while a guard on the walkway still snares")

# ======================================================================
print("--- the whole crack, end to end: keys -> update_player arms ->")
print("    timer runs -> game_loop's resolve -> the drone falls")
INP_RIGHT, INP_UP, INP_ACT = 1 << 1, 1 << 2, 1 << 5
WHIP_TIME = 10
FLOOR = ((0, 80, 192, 8, 1),)             # a real deck to stand on


def crack_chain(press=0, hold=0, drone=(40, 150), frames_max=60):
    """game_loop's exact per-frame order -- update_player (which counts
    the whip down and arms it on the press edge), update_entities, then
    the resolve at WHIP_TIME-4 -- with the keys held for one frame and
    real physics on a real floor.  Returns what became of the drone."""
    world(40, 176, rects=FLOOR)
    MEM[S("SCORE") + 3] = 0
    ent(0, ET_DRONE, x=drone[0], y=drone[1])
    for fr in range(frames_max):
        MEM[S("INPUT_NEW")] = press if fr == 0 else 0
        MEM[S("INPUT_HELD")] = hold if fr == 0 else 0
        run(cpu(), S("UPDATE_PLAYER"))
        run(cpu(), S("UPDATE_ENTITIES"))
        if MEM[S("WHIP_TIMER")] == WHIP_TIME - 4:
            run(cpu(), S("WHIP_HITS"))
        if rec(0)[0] != ET_DRONE:
            return (fr, MEM[S("PLAYER_ENERGY")], MEM[S("SFX_TYPE")],
                    MEM[S("SCORE") + 3])
    return (None, MEM[S("PLAYER_ENERGY")], MEM[S("SFX_TYPE")],
            MEM[S("SCORE") + 3])


fr, en, sfx, sc = crack_chain(press=INP_ACT, hold=INP_UP)
chk(fr is not None and fr <= 6 and en == 5 and sfx == SFX_KILL and sc == 1,
    f"Z + UP with a drone overhead: it falls at frame {fr}, no energy "
    f"lost, the kill rings, +1 score")
fr, en, sfx, sc = crack_chain(press=INP_ACT, hold=INP_UP | INP_RIGHT,
                              drone=(45, 158))
chk(fr is not None and fr <= 6 and en == 5 and sc == 1,
    f"Z + UP + RIGHT, drone high to the right: the diagonal takes it "
    f"(frame {fr})")
fr, en, sfx, sc = crack_chain()
chk(fr is not None and en == 4 and sfx == SFX_HIT and sc == 0,
    f"no whip: the same drone detonates on him at frame {fr} for one "
    f"energy -- the danger is real")
fr, en, sfx, sc = crack_chain(press=INP_ACT, drone=(47, 170))
chk(fr is not None and fr <= 6 and en == 5 and sc == 1,
    f"the SIDE crack takes one diving in at body height (frame {fr})")
fr, en, sfx, sc = crack_chain(press=INP_ACT)
chk(fr is not None and en == 4 and sc == 0,
    f"...but fired at one still high overhead it whiffs -- detonation "
    f"at frame {fr}: the overhead aim remains the safe answer")

# ======================================================================
print("--- contact bookkeeping stays with the specialists")
world(40, 160)
ent(0, ET_BULLET, x=40, y=165, dir=1)
ent(1, ET_DRONE, x=40, y=155)
z = cpu()
run(z, S("CHECK_ENEMY_HIT"))
chk(not z.fc, "check_enemy_hit ignores bullets and drones underfoot")
ent(2, ET_COAT, x=41, y=160, dir=1, spd=1)
z = cpu()
run(z, S("CHECK_ENEMY_HIT"))
chk(z.fc, "...but a coat man in your face still counts")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
