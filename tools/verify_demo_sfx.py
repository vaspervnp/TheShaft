#!/usr/bin/env python3
"""Check the demo's sound.  The walking click, to the user's spec --
while walking or climbing, every 10 frames, alternately

    SOUND 1, 0, 3, 8, 1, 1, 0
    SOUND 1, 0, 3, 8, 1, 2, 0           (ENV 1, 3, -5, 3)

-- tone 0 and noise 0, so the sound is the AY's DC step: level 8 for
one ~30 ms envelope step, then zero, a soft click each way.  With no
tone the ENT number is inaudible, so the alternation is checked on the
beat phase (which swaps the legs).  Standing or in the air, the beat
rests.  Plus the game's own one-shots: the rising JUMP chirp on the
leap, and the PING on the switch, the key and the door -- both of
which own the channel while they ring."""
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


def strike(anim, fpu):
    """One beat and its full decay; (strike_regs, volumes, end_regs)."""
    MEM[S("ENV_POS")] = 0
    MEM[S("FRAMES_NOW")] = fpu
    MEM[S("SEG_ANIM")] = anim
    MEM[S("BEAT_TIMER")] = 1
    writes.clear()
    run(V.cpu(), S("BEAT_TICK"))
    st = dict(writes)
    vols = [st.get(8)] if st else []
    while MEM[S("ENV_POS")]:
        writes.clear()
        run(V.cpu(), S("ENV_UPDATE"))
        vols.append(dict(writes).get(8))
    return st, vols, dict(writes)


for fpu in (1, 2, 3, 4, 8):
    st, vols, end = strike(D.WALK_R, fpu)
    body = [v for v in vols[:-1] if v is not None]
    chk(st.get(8) == 8 and st.get(7) == 0x3F
        and 0 not in st and 1 not in st and 6 not in st,
        f"at {fpu} frame(s)/update: the level jumps to 8 with BOTH "
        f"generators off (mixer #{st.get(7, 0):02X})")
    chk(body[0] == 8 and set(body) == {8},
        f"...holds one envelope step: {body}")
    chk(end.get(8) == 0 and end.get(7) == 0x3F,
        "...and the channel closes at the end")
    frames = (len(vols) - 1) * fpu
    chk(2 <= frames <= 8, f"...over {frames * 20} ms of real time")

# climbing strikes too -- each strike is the grab of a rung
st, _v, _e = strike(D.CLIMB, 2)
chk(st.get(8) == 8, "climbing keeps the beat")

# the two SOUNDs differ only in their (inaudible) ENT number, so the
# alternation is carried by the beat phase, which swaps the legs
MEM[S("BEAT_PHASE")] = 0
ph = []
for _ in range(4):
    st, _v, _e = strike(D.WALK_R, 2)
    ph.append(MEM[S("BEAT_PHASE")] & 4)
chk(ph in ([4, 0, 4, 0], [0, 4, 0, 4]),
    f"strikes alternate the beat phase (legs/hands): {ph}")

# standing or in the air, the beat rests (and re-arms)
for anim, name in ((D.STAND, "standing"), (D.JUMP, "jumping")):
    MEM[S("SEG_ANIM")] = anim
    MEM[S("BEAT_TIMER")] = 1
    writes.clear()
    run(V.cpu(), S("BEAT_TICK"))
    chk(not writes and MEM[S("BEAT_TIMER")] == 10,
        f"{name} strikes nothing and re-arms the beat")

# ---- the game's one-shots
def oneshot(kind, fpu):
    MEM[S("SFX_TYPE")] = kind
    MEM[S("SFX_TIMER")] = MEM[S("SFX_LEN") + kind - 1]
    frames_writes = []
    MEM[S("FRAMES_NOW")] = fpu
    while MEM[S("SFX_TIMER")]:
        writes.clear()
        run(V.cpu(), S("SFX_UPDATE"))
        frames_writes.append(dict(writes))
    return frames_writes

jw = oneshot(1, 2)
body, end = jw[:-1], jw[-1]
periods = [w.get(0) for w in body]
chk(all(w.get(7) == 0x3E and w.get(8) == 12 for w in body),
    f"JUMP: tone on A alone at volume 12 for {len(body)} updates")
chk(all(v <= 15 for w in body for v in [w.get(8, 0)]),
    "...every volume legal (no envelope bit)")
chk(periods == sorted(periods, reverse=True) and periods[-1] < periods[0],
    f"...with the period shrinking (pitch rising): {periods}")
chk(end.get(8) == 0 and end.get(7) == 0x3F, "...and the channel closed")

pw = oneshot(2, 2)
body, end = pw[:-1], pw[-1]
vols = [w.get(8) for w in body]
chk(all(w.get(0) == 40 and w.get(7) == 0x3E for w in body),
    f"PING: the game's period-40 ding for {len(body)} updates")
chk(vols == sorted(vols, reverse=True) and len(set(vols)) == len(vols)
    and all(7 <= v <= 13 for v in vols),
    f"...fading through legal volumes as it rings: {vols}")
chk(end.get(8) == 0 and end.get(7) == 0x3F, "...and the channel closed")

# the events and the leap actually trigger them
for anim, ev, want, what in ((D.JUMP, 0, 1, "the leap"),
                             (D.STAND, D.EV_SWITCH, 2, "the switch"),
                             (D.STAND, D.EV_KEY, 2, "the key"),
                             (D.STAND, D.EV_DOOR, 2, "the door")):
    MEM[S("SFX_TIMER")] = 0
    MEM[S("SEG_ANIM")] = anim
    MEM[S("SEG_EVENT")] = ev
    run(V.cpu(), S("DO_EVENT"))
    got = MEM[S("SFX_TYPE")] if MEM[S("SFX_TIMER")] else 0
    chk(got == want, f"{what} starts one-shot {want} (got {got})")
run(V.cpu(), S("SCENE_RESET"))          # undo the door/key edits

# while a one-shot rings, the click stands aside but the legs still swap
MEM[S("SFX_TIMER")] = 5
MEM[S("SEG_ANIM")] = D.WALK_R
MEM[S("BEAT_TIMER")] = 1
ph0 = MEM[S("BEAT_PHASE")]
writes.clear()
run(V.cpu(), S("BEAT_TICK"))
chk(not writes and MEM[S("BEAT_PHASE")] != ph0,
    "while a one-shot rings the click yields, the legs still swap")
MEM[S("SFX_TIMER")] = 0

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
