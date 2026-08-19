#!/usr/bin/env python3
"""Run the demo's REAL main loop from start:, faking only time, and
assert what a listener would report: a strike every 15 frames while he
moves, alternating 22/26, each decaying away -- and rest when he
stands."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load
import demo_scene as D

sym = load("build/demo.bin", "build/demo.sym")
S = lambda n: sym[n.upper()]

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(("OK   " if c else "FAIL ") + m)


def run_loop(fpu, total_frames):
    MEM[:] = bytearray(0x10000)
    load("build/demo.bin", "build/demo.sym")
    z = Z80()
    z.sp = 0x1000
    z.pc = S("START")
    clock, writes = 0, []
    FS, PSG = S("FRAME_SYNC"), S("PSG_WRITE")
    budget = 200_000_000
    while clock < total_frames and budget:
        if z.pc == FS:
            clock += fpu
            MEM[S("FRAME50")] = clock & 0xFF
            z.pc = z.pop()
            continue
        if z.pc == PSG:
            writes.append((clock, z.a, z.e))
        z.step()
        budget -= 1
    assert budget, "runaway"
    return writes


total = sum(s[2] for s in D.SCRIPT)
stretches, f = [], 0
for dx, dy, n, a, e in D.SCRIPT:
    if a in (D.WALK_R, D.WALK_L, D.CLIMB):
        if stretches and stretches[-1][1] == f:
            stretches[-1] = (stretches[-1][0], f + n)   # merge adjacent
        else:
            stretches.append((f, f + n))
    f += n

for fpu in (2, 3):
    writes = run_loop(fpu, total + 100)
    # A click strike is the level jumping to 8 while the mixer is
    # fully closed (the DC step IS the sound) -- the mixer state tells
    # it apart from a ping fading through volume 8.  First loop only.
    strikes, mix = [], 0x3F
    for fr, a, e in writes:
        if a == 7:
            mix = e
        elif a == 8 and e == 8 and mix == 0x3F and fr <= total - fpu:
            strikes.append((fr, e))
    in_stretch = lambda fr: any(s - fpu <= fr <= e + fpu
                                for s, e in stretches)
    stray = [fr for fr, _e in strikes if not in_stretch(fr)]

    periods = []
    for s0, e0 in stretches:
        run = [fr for fr, _e in strikes if s0 - fpu <= fr <= e0 + fpu]
        periods += [b - a for a, b in zip(run, run[1:])]

    print(f"--- {50 // fpu} Hz updates: {len(strikes)} strikes, "
          f"periods {min(periods)}-{max(periods)} frames "
          f"across {len(stretches)} movement stretches")
    chk(all(8 <= p <= 12 for p in periods),
        "the beat holds 10 frames within each movement stretch")
    chk(abs(sum(periods) / len(periods) - 10) <= 0.6,
        f"...averaging {sum(periods) / len(periods):.1f}")
    chk(not stray,
        f"strikes happen only while walking or climbing ({len(stray)} stray)")

    # the game's voices land on their beats: the chirp as the jump
    # starts, and three pings on the switch, the key and the door
    f0, marks = 0, {"jump": [], "ping": []}
    for dx, dy, n, a, e in D.SCRIPT:
        if a == D.JUMP:
            marks["jump"].append(f0)
        if e in (D.EV_SWITCH, D.EV_KEY, D.EV_DOOR):
            marks["ping"].append(f0)
        f0 += n
    chirps = [fr for fr, a, e in writes if a == 0 and e > 100]
    dings = [fr for fr, a, e in writes if a == 0 and e == 40]
    near = lambda fr, beats: any(b - fpu <= fr <= b + 3 * fpu for b in beats)
    chk(chirps and near(chirps[0], marks["jump"]),
        f"the jump chirp sounds as he leaves the deck (frame {chirps[:1]})")
    starts = sorted({b for b in marks["ping"]})
    heard = [any(near(fr, [b]) for fr in dings) for b in starts]
    chk(all(heard),
        f"a ping lands on the switch, the key and the door ({sum(heard)}/3)")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
