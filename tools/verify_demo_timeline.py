#!/usr/bin/env python3
"""Run the demo's REAL main loop from start:, faking only time, and
assert what a listener would report: footsteps at a walking rate, each
decaying away, with real silence between them."""
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


def run_loop(frames_per_update, total_frames):
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
            clock += frames_per_update
            MEM[S("FRAME50")] = clock & 0xFF
            z.pc = z.pop()
            continue
        if z.pc == PSG:
            writes.append((clock, z.a, z.e))
        z.step()
        budget -= 1
    assert budget, "runaway"
    return writes


def spans_of(writes):
    spans, vol, mix, since = [], 0, 0x3F, None
    for fr, reg, val in writes:
        if reg == 8:
            vol = val
        elif reg == 7:
            mix = val
        else:
            continue
        heard = vol > 0 and (mix & 0x08) == 0
        if heard and since is None:
            since = fr
        elif not heard and since is not None:
            spans.append((since, fr))
            since = None
    return spans


total = sum(s[2] for s in D.SCRIPT)
walk_secs = sum(s[2] for s in D.SCRIPT
                if s[3] in (D.WALK_R, D.WALK_L)) / 50
for fpu in (2, 3):
    spans = spans_of(run_loop(fpu, total + 100))
    lens = [b - a for a, b in spans]
    gaps = [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]
    rate = len(spans) / walk_secs
    print(f"--- {50 // fpu} Hz updates: {len(spans)} steps, "
          f"ring {min(lens)}-{max(lens)} frames, "
          f"gaps {min(gaps)}-{max(gaps)} frames")
    chk(2.0 <= rate <= 5.0, f"a walking rate of {rate:.1f} steps/s")
    chk(max(lens) <= 10, "each step decays away inside 200 ms")
    chk(min(gaps) >= 4, "with real silence between steps")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
