#!/usr/bin/env python3
"""demo_preview.py -- render the demo disc to video WITHOUT a CPC.

Every frame is produced by executing the real assembled binary in
tools/z80mini.py and reading the resulting Mode 0 screen, so the preview
is the engine's own output rather than a second implementation that
could drift from it.

    ./build_demo.sh
    python3 tools/demo_preview.py docs/demo.mp4
"""
import os
import subprocess
import sys

sys.path.insert(0, "tools")
from z80mini import Z80, MEM, load, run
import demo_scene as D
import gfx_ref as gr
from PIL import Image

sym = load("build/demo.bin", "build/demo.sym")
S = lambda n: sym[n.upper()]
LO = S("LINE_OFFSETS")
LINE = [MEM[LO + 2 * y] | MEM[LO + 2 * y + 1] << 8 for y in range(200)]


def dec_left(b):
    return (((b >> 7) & 1) | ((b >> 3) & 1) << 1
            | ((b >> 5) & 1) << 2 | ((b >> 1) & 1) << 3)


def boot():
    run(Z80(), S("SCENE_RESET"))
    MEM[S("SEG_PTR")] = S("DEMO_SCRIPT") & 0xFF
    MEM[S("SEG_PTR") + 1] = S("DEMO_SCRIPT") >> 8
    run(Z80(), S("SEG_LOAD"))
    MEM[S("CAM_XF")] = 0
    MEM[S("CAM_XF") + 1] = D.CAM_X0 & 0xFF
    MEM[S("CAM_XH")] = D.CAM_X0 >> 8
    MEM[S("CAM_YF")] = 0
    MEM[S("CAM_YF") + 1] = D.CAM_Y0 & 0xFF
    MEM[S("CAM_YH")] = D.CAM_Y0 >> 8
    run(Z80(), S("CAM_WHOLE"))
    MEM[S("LAST_FRAME")] = 0
    MEM[S("FRAME50")] = 0
    MEM[S("ANIM_CTR")] = 0


def draw(ctr):
    """main_loop's draw half, into the buffer at #C000."""
    MEM[0xC000:0x10000] = bytes(0x4000)
    MEM[S("DRAW_PAGE")] = 0xC0
    for i in range(2):
        MEM[S("PASS_X") + i] = MEM[S("CAM_IX") + i]
        MEM[S("PASS_Y") + i] = MEM[S("CAM_IY") + i]
    MEM[S("BLIT_BLACK")] = 0
    MEM[S("ANIM_CTR")] = ctr
    run(Z80(), S("OBJECT_PASS"))
    run(Z80(), S("DRAW_STEAM"))
    run(Z80(), S("DRAW_HERO"))


def frame_image():
    """The Mode 0 band as a 320x152 RGB image (pixels doubled across)."""
    img = Image.new("RGB", (320, D.BAND))
    put = img.load()
    for y in range(D.BAND):
        base = 0xC000 + LINE[y]
        for xb in range(80):
            b = MEM[base + xb]
            for half, pen in ((0, dec_left(b)), (1, dec_left(b << 1))):
                col = gr.PAL[pen]
                put[xb * 2 + half, y] = col
    return img


def main(dst):
    total = sum(s[2] for s in D.SCRIPT)
    boot()
    os.makedirs("build/preview", exist_ok=True)
    for fr in range(1, total + 1):
        MEM[S("FRAME50")] = fr & 0xFF
        z = Z80()
        run(z, S("ADVANCE_CAMERA"))
        if z.fc:
            break
        run(Z80(), S("BEAT_TICK"))      # legs and hands swap on the beat
        draw(fr & 0xFF)
        # A CPC line is taller than a doubled Mode 0 pixel is wide, so
        # the 160x152 band shows as 1.754:1; 1280x720 lands on 16:9 with
        # about a percent of stretch, which nothing notices.
        frame_image().resize((1280, 720), Image.NEAREST).save(
            f"build/preview/f{fr:04d}.png")
        if fr % 100 == 0:
            print(f"  {fr}/{total}", file=sys.stderr, flush=True)

    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "50", "-i", "build/preview/f%04d.png",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"{dst}: {total} frames, {total / 50:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/demo.mp4")
