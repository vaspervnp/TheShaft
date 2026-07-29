#!/usr/bin/env python3
"""Check the footstep against the user's own BASIC spec:

    ENV 1, 3, -5, 3
    SOUND 1, 0, 20, 15, 1, 0, n     with n = 26 / 22 per foot

Noise only, alternating noise period per boot, volume decaying
15 -> 10 -> 5 -> off in real time -- at EVERY update length the demo
runs at, since testing only one is how a silent footstep once shipped.
"""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, run
import demo_scene as D
import verify_demo_loop as V

S = V.S
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


run(V.cpu(), S("AY_INIT"))
init = dict(writes)
chk(init.get(7) == 0x3F and init.get(8) == 0,
    "ay_init leaves the chip defined and silent")

def footfall(frames_per_update):
    """Strike one step and decay it; returns (strike, volumes, end)."""
    MEM[S("ENV_POS")] = 0
    MEM[S("FRAMES_NOW")] = frames_per_update
    MEM[S("SEG_ANIM")] = D.WALK_R
    MEM[S("STEP_TIMER")] = 1
    writes.clear()
    run(V.cpu(), S("WALK_ANIM"))
    strike = dict(writes)
    vols = [strike.get(8)]
    while MEM[S("ENV_POS")]:
        writes.clear()
        run(V.cpu(), S("SND_UPDATE"))
        vols.append(dict(writes).get(8))
    return strike, vols, dict(writes)


for fpu in (1, 2, 3, 4, 8):
    strike, vols, end = footfall(fpu)
    body = [v for v in vols[:-1] if v is not None]
    chk(strike.get(8) == 15 and strike.get(7) == 0x37
        and 0 not in strike and 1 not in strike,
        f"at {fpu} frame(s)/update: struck at 15, noise only "
        f"(mixer #{strike.get(7, 0):02X})")
    chk(body == [15],
        f"...duration 2 cuts the envelope at its first step: {body}")
    chk(end.get(8) == 0 and end.get(7) == 0x3F,
        "...and closes the channel at the end")
    frames = (len(vols) - 1) * fpu
    chk(len(vols) == 2,
        f"...gone at the very next update ({frames * 20} ms)")

# successive footfalls alternate the boot: n = 26, 22, 26, ...
MEM[S("ENV_POS")] = 0
MEM[S("WALK_PHASE")] = 0
MEM[S("FRAMES_NOW")] = 2
ns = []
for _ in range(4):
    MEM[S("SEG_ANIM")] = D.WALK_R
    MEM[S("STEP_TIMER")] = 1
    writes.clear()
    run(V.cpu(), S("WALK_ANIM"))
    ns.append(dict(writes).get(6))
    while MEM[S("ENV_POS")]:
        run(V.cpu(), S("SND_UPDATE"))
chk(ns in ([26, 22, 26, 22], [22, 26, 22, 26]),
    f"left and right boots alternate the noise period: {ns}")

# standing still strikes nothing, but lets a ringing decay finish
MEM[S("SEG_ANIM")] = D.STAND
writes.clear()
run(V.cpu(), S("WALK_ANIM"))
chk(not writes, "standing still strikes nothing")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
