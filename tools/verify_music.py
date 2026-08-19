#!/usr/bin/env python3
"""Execute the game's music player (the real assembled binary) and
check what the title screen will sound like.

The title theme's two voices must be the SAME length -- 768 frames,
15.4 s -- so the piece repeats exactly; every period written must come
from note_table; the melody's volume must land on R9 and the bass's on
R10 (the register the first player never wrote: it added 7 to the tone
register instead of mapping it, sending the bass to R11 -- the AY's
envelope period -- so the bass was silent for its whole life); and the
mixer must keep tones B+C open, noise B+C shut and bit 6 low, or the
keyboard dies.  The whole register stream is also compared against an
independent Python model of the player, write for write."""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

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


def tune(addr):
    """(note, duration) pairs straight from the assembled binary."""
    out = []
    while MEM[addr] != 0xFF:
        out.append((MEM[addr], MEM[addr + 1]))
        addr += 2
    return out


def play(frames):
    """update_music once per 50 Hz frame; [(frame, reg, val), ...]."""
    log = []
    for fr in range(frames):
        del writes[:]
        run(cpu(), S("UPDATE_MUSIC"))
        log += [(fr, r, v) for r, v in writes]
    return log


NOTES = [MEM[S("NOTE_TABLE") + 2 * i] | MEM[S("NOTE_TABLE") + 2 * i + 1] << 8
         for i in range(37)]
theme_b, theme_c = tune(S("MENU_TB")), tune(S("MENU_TC"))

# --- the data itself: a locked 15-second loop of legal notes
LOOP = sum(d for _n, d in theme_b)
chk(LOOP == sum(d for _n, d in theme_c) == 768,
    f"both voices hold {LOOP} frames = {LOOP / 50:.2f}s: the 15-second "
    f"loop repeats in lockstep")
chk(all(0 <= n <= len(NOTES) for n, _d in theme_b + theme_c)
    and all(d >= 1 for _n, d in theme_b + theme_c),
    "every note indexes note_table (0 = rest), every duration is real")
same = [(a, b) for (a, _), (b, _) in zip(theme_b, theme_b[1:]) if a == b]
same += [(a, b) for (a, _), (b, _) in zip(theme_c, theme_c[1:]) if a == b]
chk(not same,
    "no note repeats back-to-back (same period + held volume would "
    "merge into one long note)")

# --- the player, executed: three loops of the theme
MEM[S("MUSIC_ON")] = 1
run(cpu(), S("MUSIC_MENU"))
log = play(LOOP * 3)


def model():
    """The write stream a correct player produces, frame by frame."""
    exp = []
    st = {2: [0, 1], 4: [0, 1]}          # per voice: tune index, frames left
    for fr in range(LOOP * 3):
        for base, notes in ((2, theme_b), (4, theme_c)):
            s = st[base]
            s[1] -= 1
            if s[1]:
                continue
            n, d = notes[s[0] % len(notes)]
            s[0] += 1
            s[1] = d
            vol = 9 if base == 2 else 10
            if n == 0:
                exp.append((fr, vol, 0))
            else:
                p = NOTES[n - 1]
                exp.append((fr, base, p & 0xFF))
                exp.append((fr, base + 1, p >> 8))
                exp.append((fr, vol, 6 if base == 2 else 7))
        exp.append((fr, 7, MEM[S("MIX_A")] | 0x30))
    return exp


exp = model()
chk(log == exp,
    f"the executed binary's {len(log)} register writes match the model, "
    f"write for write")
first = [(f, r, v) for f, r, v in log if f < LOOP]
second = [(f - LOOP, r, v) for f, r, v in log if LOOP <= f < 2 * LOOP]
chk(first == second, "...and the second 15 seconds repeat the first")

chk(all(r != 11 for _f, r, v in log),
    "R11 (envelope period) is never touched: the bass volume reaches R10")
vols_b = {v for _f, r, v in log if r == 9}
vols_c = {v for _f, r, v in log if r == 10}
chk(vols_b <= {0, 6} and vols_c <= {0, 7} and 6 in vols_b and 7 in vols_c,
    f"melody plays at 6 on R9 {sorted(vols_b)}, bass at 7 on R10 "
    f"{sorted(vols_c)}")
mixes = {v for _f, r, v in log if r == 7}
chk(all(v & 0x40 == 0 for v in mixes),
    "mixer bit 6 stays 0 (the keyboard survives)")
chk(all(v & 0x36 == 0x30 for v in mixes),
    f"tones B+C open, noise B+C shut, every frame {sorted(mixes)}")
periods = {v << 8 | lo for (_f, r, lo), (_g, s, v) in zip(log, log[1:])
           if r in (2, 4) and s == r + 1}
chk(periods <= set(NOTES),
    "every period written is a note_table pitch")

# --- music off (the shaft itself): B+C gagged at the mixer
MEM[S("MUSIC_ON")] = 0
del writes[:]
run(cpu(), S("UPDATE_MUSIC"))
off = dict(writes)
chk(set(off) == {7} and off[7] & 0x36 == 0x36 and off[7] & 0x40 == 0,
    "music_on=0: one mixer write, tones B+C disabled, bit 6 low")

# --- the ending's dirge still drifts, and its bass now sounds
dirge_b = sum(d for _n, d in tune(S("TUNE_B")))
dirge_c = sum(d for _n, d in tune(S("TUNE_C")))
chk(dirge_b != dirge_c,
    f"the dirge keeps drifting: voice loops of {dirge_b} and {dirge_c} "
    f"frames")
MEM[S("MUSIC_ON")] = 1
run(cpu(), S("MUSIC_RESTART"))
dlog = play(max(dirge_b, dirge_c))
chk(any(r == 10 and v == 7 for _f, r, v in dlog),
    "...and its bass, at last, reaches R10")

print("\nALL PASS" if ok else "\nFAILED")
sys.exit(0 if ok else 1)
