#!/usr/bin/env python3
"""sprite_export.py -- write every sprite frame to art/sprites/*.png
for editing in a pixel editor, plus a GIMP/Aseprite palette file.

The PNGs are NATIVE resolution: 8x16 pixels, exactly the grid the
engine sees.  A Mode 0 pixel is twice as wide as it is tall, so set
your editor's pixel aspect ratio to 2:1 (art/README.md says how) --
do NOT export scaled-up images, or the importer could not know which
of your pixels is real.

Transparency is the PNG alpha channel; every opaque pixel must be one
of the 16 palette colours in art/cpc-pens.gpl.

    python3 tools/sprite_export.py          # -> art/sprites/*.png
    python3 tools/sprite_import.py          # PNGs -> sprites_art.py
"""
import os
import re
import sys

sys.path.insert(0, "tools")
from PIL import Image

import gfx_ref as gr
import level_gen as lg

FRAME_RE = re.compile(
    r"    '(\w+)': \[\n((?:        \"[^\"]*\",\n)+)    \],")


def frames_from_source(path="tools/sprites_art.py"):
    """The frames read straight off the FILE, in order -- never via
    import: a stale __pycache__ once served pre-edit art here and put a
    test pixel back after a revert."""
    src = open(path).read()
    out = {}
    for name, body in FRAME_RE.findall(src):
        out[name] = re.findall(r'"([^"]*)"', body)
    return src, out

# Pens whose ink changes with the zone palette; everything the player
# wears is deliberately drawn without them.
UNSTABLE = sorted(p for p in range(16)
                  if len({pal[p] for pal in lg.PALETTES}) > 1)


def export(outdir="art/sprites"):
    os.makedirs(outdir, exist_ok=True)
    _src, frames = frames_from_source()
    for name, art in frames.items():
        img = Image.new("RGBA", (8, 16), (0, 0, 0, 0))
        put = img.load()
        for y, row in enumerate(art):
            for x, ch in enumerate(row):
                if ch != '.':
                    put[x, y] = gr.PAL[int(ch, 16)] + (255,)
        img.save(f"{outdir}/{name}.png")
    print(f"{len(frames)} frames -> {outdir}/*.png  (8x16, native pixels)")


def palette(path="art/cpc-pens.gpl"):
    with open(path, "w") as f:
        f.write("GIMP Palette\n")
        f.write("Name: The Shaft - CPC pens (mechanical zone)\n")
        f.write("Columns: 8\n")
        f.write("# Pens marked ZONE-VARIES recolour in the agricultural\n")
        f.write("# and administrative zones; sprites that must look the\n")
        f.write("# same everywhere (the player) avoid them.\n")
        for p, (r, g, b) in enumerate(gr.PAL):
            tag = " ZONE-VARIES" if p in UNSTABLE else ""
            f.write(f"{r:3} {g:3} {b:3}\tpen {p:02}{tag}\n")
    print(f"16 pens -> {path}")


if __name__ == "__main__":
    export()
    palette()
