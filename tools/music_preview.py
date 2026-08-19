#!/usr/bin/env python3
"""music_preview.py -- hear the game's AY music WITHOUT a CPC.

Executes the real assembled binary's music player frame by frame
(tools/z80mini.py), tracks the AY registers it writes, and renders
channels B and C as the square waves the chip would make.  Two loops
of the title theme, so the seam is audible.

    ./build.sh
    python3 tools/music_preview.py            # -> build/menu_theme.wav
    python3 tools/music_preview.py dirge      # -> build/dirge.wav
"""
import struct
import sys
import wave

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run

sym = load("build/shaft.bin", "build/shaft.sym")
S = lambda n: sym[n.upper()]

PSG = S("PSG_WRITE")
regs = [0] * 16
_step = Z80.step


def step(self):
    if self.pc == PSG:
        regs[self.a & 15] = self.e
    _step(self)


Z80.step = step

RATE = 44100
SPF = RATE // 50                       # samples per 50 Hz frame
# The AY's volume DAC is logarithmic: each step down halves the power.
DAC = [0.0] + [2 ** ((v - 15) / 2) for v in range(1, 16)]


def cpu():
    z = Z80()
    z.sp = 0x1000
    return z


def render(entry, frames, path):
    MEM[S("MUSIC_ON")] = 1
    run(cpu(), S(entry))
    phase = {2: 0.0, 4: 0.0}
    out = []
    for _fr in range(frames):
        run(cpu(), S("UPDATE_MUSIC"))
        for _s in range(SPF):
            mixed = 0.0
            for base, volreg in ((2, 9), (4, 10)):
                period = regs[base] | (regs[base + 1] & 15) << 8
                amp = DAC[regs[volreg] & 15]
                if period and amp:
                    phase[base] += 1_000_000 / 16 / period / RATE
                    phase[base] %= 1.0
                    mixed += amp if phase[base] < 0.5 else -amp
            out.append(mixed)
    peak = max(1e-9, max(abs(x) for x in out))
    out = [int(x / peak * 0.7 * 32767) for x in out]   # a volume knob,
    # not the chip's DAC: levels 6-7 are near-silent in 16-bit terms
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(out)}h", *out))
    print(f"{path}: {frames} frames, {frames / 50:.1f}s")


if __name__ == "__main__":
    if "dirge" in sys.argv[1:]:
        render("MUSIC_RESTART", 2 * 6400, "build/dirge.wav")
    else:
        render("MUSIC_MENU", 2 * 768, "build/menu_theme.wav")
