#!/usr/bin/env python3
"""Execute the game's movement sounds (the real assembled binary) and
check what a player would hear.

Walking a deck: a DC click -- level 8 for one frame, both generators
off -- every 5 frames.  Climbing: a 20 ms metallic tick (tone period
100, level 8) at the same cadence, but ONLY while player_y actually
moves; ladder motion never sets player_moved, so the climb is read
off the y coordinate, and resting hands are silent.  Standing or
airborne (air control sets player_moved, falling changes player_y --
both traps are tested) there is no sound.  A ringing effect is never
clipped by a boot: the beat skips its turn.  Frames are driven in
game_loop's real order (sfx_update and update_music BEFORE step_tick),
so the one-frame arm-to-burst latency is in the test."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

ST_GROUND, ST_CLIMB, ST_AIR = 0, 1, 2
SFX_JUMP, SFX_WHIP, SFX_STEP, SFX_RUNG = 1, 4, 6, 7
STEP_FRAMES = 5

PSG = S("PSG_WRITE")
writes = []
_step = Z80.step


def step(self):
    if self.pc == PSG:
        writes.append((self.a, self.e))
    _step(self)


Z80.step = step

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(("OK   " if c else "FAIL ") + m)


def cpu():
    z = Z80()
    z.sp = 0x1000
    return z


def reset():
    MEM[S("SFX_TYPE")] = 0
    MEM[S("SFX_TIMER")] = 0
    MEM[S("STEP_TIMER")] = STEP_FRAMES
    MEM[S("MIX_A")] = 9
    MEM[S("MUSIC_ON")] = 0
    MEM[S("PLAYER_Y")] = 100
    MEM[S("CLIMB_PY")] = 100


def frames(n, state=ST_GROUND, moved=1, dy=0):
    """game_loop's sound slice, n frames; per frame: [(reg, val), ...]."""
    out = []
    for _ in range(n):
        del writes[:]
        run(cpu(), S("SFX_UPDATE"))
        run(cpu(), S("UPDATE_MUSIC"))
        MEM[S("PLAYER_STATE")] = state
        MEM[S("PLAYER_MOVED")] = moved
        MEM[S("PLAYER_Y")] = (MEM[S("PLAYER_Y")] + dy) & 0xFF
        run(cpu(), S("STEP_TICK"))
        out.append(list(writes))
    return out


def strikes(log):
    """(frame, kind) for every burst that jumps R8 to 8: 'click' is the
    toneless walking thud (only R7/R8 written, channel A fully shut),
    'rung' the ladder tick (tone period 100 latched, tone A open)."""
    out = []
    for fr, w in enumerate(log):
        d = dict(w)
        if d.get(8) != 8:
            continue
        regs = {r for r, _v in w}
        mix = [v for r, v in w if r == 7]
        chk(all(v & 0x40 == 0 for v in mix),
            f"frame {fr}: mixer bit 6 stays low (the keyboard survives)")
        if 0 in regs:
            chk(d.get(0) == 100 and d.get(1) == 0
                and regs <= {0, 1, 7, 8} and all(v & 9 == 8 for v in mix),
                f"frame {fr}: the rung is tone 100, tone A open, "
                f"noise shut")
            out.append((fr, "rung"))
        else:
            chk(regs <= {7, 8} and all(v & 9 == 9 for v in mix),
                f"frame {fr}: the click touches only R7/R8, channel A "
                f"fully shut")
            out.append((fr, "click"))
    return out


def cadence(hit, kind, what):
    frs = [fr for fr, k in hit]
    gaps = [b - a for a, b in zip(frs, frs[1:])]
    chk(bool(gaps) and set(gaps) == {STEP_FRAMES}
        and {k for _fr, k in hit} == {kind},
        f"{what}: '{kind}' every {STEP_FRAMES} frames exactly {frs}")
    return frs


# --- walking: a click every 5 frames, one frame after each arm
reset()
log = frames(40)
hit = cadence(strikes(log), "click", "walking")
chk(all(dict(log[fr + 1]).get(8) == 0 for fr in hit),
    "each click closes to volume 0 on the very next frame: a 20 ms tap")
chk(all(dict(w).get(8) in (None, 0) for fr, w in enumerate(log)
        if fr not in hit),
    "between footfalls the level never rises")

# --- climbing with real motion: the rung tick, same cadence
reset()
log = frames(40, ST_CLIMB, moved=0, dy=-2)
hit = cadence(strikes(log), "rung", "climbing up")
chk(all(dict(log[fr + 1]).get(8) == 0 for fr in hit),
    "each rung closes on the very next frame too")
reset()
log = frames(40, ST_CLIMB, moved=0, dy=2)
cadence(strikes(log), "rung", "climbing down")

# --- no motion, no sound -- each row springs a different trap
for name, state, moved, dy in (
        ("standing", ST_GROUND, 0, 0),
        ("hands resting on the rails", ST_CLIMB, 1, 0),
        ("airborne, drifting sideways", ST_AIR, 1, 0),
        ("falling (y changes, but no ladder)", ST_AIR, 1, 3)):
    reset()
    log = frames(40, state, moved, dy)
    chk(not strikes(log), f"{name}: silence over 40 frames")
    chk(MEM[S("STEP_TIMER")] == STEP_FRAMES,
        f"...and the cadence re-arms to {STEP_FRAMES}")

# --- a rest between walks restarts the count from 5
reset()
frames(3)
frames(2, moved=0)
hit = strikes(frames(10))
chk(bool(hit) and hit[0][0] == STEP_FRAMES,
    f"after a rest the first click waits {STEP_FRAMES} walking frames "
    f"again (frame {hit[:1]})")

# --- priority: a ringing one-shot is never clipped by a boot
reset()
z = cpu()
z.a = SFX_JUMP
run(z, S("SFX_START"))
jump_len = MEM[S("SFX_TIMER")]
log = frames(jump_len + 12)
chirp = [fr for fr, w in enumerate(log) for r, v in w if r == 0 and v > 60]
hit = strikes(log)
chk(bool(chirp) and min(chirp) < jump_len,
    "the jump chirp's tone sweep plays out in full")
chk(bool(hit) and all(fr >= jump_len for fr, _k in hit),
    f"footfalls hold back until the chirp dies, then resume {hit}")

# --- and a boot never blocks a real effect: the whip replaces it
reset()
frames(STEP_FRAMES)                       # a click is ringing right now
z = cpu()
z.a = SFX_WHIP
run(z, S("SFX_START"))
chk(MEM[S("SFX_TYPE")] == SFX_WHIP and MEM[S("SFX_TIMER")] == 6,
    "a fresh whip crack replaces a ringing footfall at once")

# --- both lengths come from the table, not luck
chk(MEM[S("SFX_LEN_TAB") + SFX_STEP - 1] == 2
    and MEM[S("SFX_LEN_TAB") + SFX_RUNG - 1] == 2,
    "sfx_len_tab holds 2 frames for STEP and RUNG")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
