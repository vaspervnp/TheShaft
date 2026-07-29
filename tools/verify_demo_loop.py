#!/usr/bin/env python3
"""Run the demo's WHOLE per-frame update -- erase pass included -- and
prove it never writes outside the screen buffers.

verify_demo.py only ever exercised the draw half, which is how a wild
row counter in the erase path reached a real CPC.  This harness runs
exactly what main_loop runs, then diffs every byte of code, sprite data
and unrelated RAM against a snapshot taken before the run.
"""
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run
import demo_scene as D

sym = load("build/demo.bin", "build/demo.sym")
S = lambda n: sym[n.upper()]

# Legitimate write targets: the two screen buffers, the variable block
# at the end of the code, and the object list (the beats patch tiles).
VARS_LO, VARS_HI = S("SHOWN_R12"), S("DEMO_END")
OBJ_LO = S("DEMO_OBJECTS")
OBJ_HI = OBJ_LO + 3 * D_OBJS if (D_OBJS := 0) else OBJ_LO  # filled below
with open("src/demo_scene.asm") as f:
    for ln in f:
        if ln.startswith("DEMO_OBJS"):
            OBJ_HI = OBJ_LO + 3 * int(ln.split()[-1])
            break


STACK_TOP = 0x1000                    # what start: sets SP to


def cpu():
    """A CPU stacked where the real program stacks, so the harness's own
    pushes land in the same place the CPC's would."""
    z = Z80()
    z.sp = STACK_TOP
    return z


def allowed(a):
    return (0x4000 <= a < 0x8000 or 0xC000 <= a <= 0xFFFF
            or VARS_LO <= a < VARS_HI or OBJ_LO <= a < OBJ_HI
            or STACK_TOP - 0x100 <= a < STACK_TOP)   # the stack


def boot():
    run(cpu(), S("SCENE_RESET"))
    MEM[S("SEG_PTR")] = S("DEMO_SCRIPT") & 0xFF
    MEM[S("SEG_PTR") + 1] = S("DEMO_SCRIPT") >> 8
    run(cpu(), S("SEG_LOAD"))
    MEM[S("CAM_XF")] = 0
    MEM[S("CAM_XF") + 1] = D.CAM_X0 & 0xFF
    MEM[S("CAM_XH")] = D.CAM_X0 >> 8
    MEM[S("CAM_YF")] = 0
    MEM[S("CAM_YF") + 1] = D.CAM_Y0 & 0xFF
    MEM[S("CAM_YH")] = D.CAM_Y0 >> 8
    run(cpu(), S("CAM_WHOLE"))
    for i in range(4):
        MEM[S("CAM_PREV") + i] = MEM[S("CAM_IX") + (i & 1)] if i < 2 else 0
    for i, v in enumerate(list(MEM[S("CAM_IX"):S("CAM_IX") + 2])
                          + list(MEM[S("CAM_IY"):S("CAM_IY") + 2])):
        MEM[S("CAM_PREV") + i] = v
        MEM[S("CAM_PREV") + 4 + i] = v
    MEM[S("HERO_PREV")] = D.HERO_Y
    MEM[S("HERO_PREV") + 1] = D.HERO_Y
    MEM[S("STEAM_PREV")] = 0
    MEM[S("STEAM_PREV") + 3] = 0
    MEM[S("LAST_FRAME")] = 0
    MEM[S("FRAME50")] = 0
    MEM[S("BUF_INDEX")] = 0
    MEM[S("DRAW_PAGE")] = 0x40


def update(fr):
    """Exactly main_loop's body, minus the raster wait and the flip."""
    MEM[S("FRAME50")] = fr & 0xFF
    z = cpu()
    run(z, S("ADVANCE_CAMERA"))
    if z.fc:
        return False

    # erase half: at the camera this buffer last drew with
    slot = S("CAM_PREV") + 4 * MEM[S("BUF_INDEX")]
    for i in range(2):
        MEM[S("PASS_X") + i] = MEM[slot + i]
        MEM[S("PASS_Y") + i] = MEM[slot + 2 + i]
    MEM[S("BLIT_BLACK")] = 1
    run(cpu(), S("OBJECT_PASS"))
    run(cpu(), S("ERASE_STEAM"))
    run(cpu(), S("ERASE_HERO"))

    # draw half: at the camera we are on now
    for i in range(2):
        MEM[S("PASS_X") + i] = MEM[S("CAM_IX") + i]
        MEM[S("PASS_Y") + i] = MEM[S("CAM_IY") + i]
    MEM[S("BLIT_BLACK")] = 0
    run(cpu(), S("OBJECT_PASS"))
    run(cpu(), S("DRAW_STEAM"))
    run(cpu(), S("DRAW_HERO"))

    for i in range(2):
        MEM[slot + i] = MEM[S("CAM_IX") + i]
        MEM[slot + 2 + i] = MEM[S("CAM_IY") + i]
    MEM[S("BUF_INDEX")] ^= 1
    MEM[S("DRAW_PAGE")] ^= 0x80
    return True


def main():
    boot()
    before = bytes(MEM)
    total = sum(s[2] for s in D.SCRIPT)
    for fr in range(1, total + 1):
        if not update(fr):
            break

    bad = [a for a in range(0x10000)
           if MEM[a] != before[a] and not allowed(a)]
    ok = not bad
    if bad:
        lo, hi = min(bad), max(bad)
        print(f'FAIL {len(bad)} bytes clobbered outside the screen buffers, '
              f'#{lo:04X}..#{hi:04X}')
        for a in bad[:8]:
            what = ('INTERRUPT VECTOR' if a < 0x100 else
                    'CODE' if a < VARS_LO else
                    'SPRITES' if 0x8000 <= a < 0xC000 else 'other')
            print(f'     #{a:04X} {before[a]:02X} -> {MEM[a]:02X}  ({what})')
    else:
        print(f'OK   {total} full updates clobber nothing outside the '
              f'screen buffers')
    print('\nALL PASS' if ok else '\nFAILED')
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
