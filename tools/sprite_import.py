#!/usr/bin/env python3
"""sprite_import.py -- read art/sprites/*.png back into sprites_art.py.

The PNGs (from tools/sprite_export.py, edited in any pixel editor) are
the working copies; this brings them home.  Each frame's rows are
patched IN PLACE in tools/sprites_art.py -- the file's structure,
comments and, crucially, the FRAMES order (which fixes the compiled
stub table's A/B adjacency) are untouched.

Every opaque pixel must be one of the 16 pens in art/cpc-pens.gpl;
anything else is an error naming the pixel and the nearest pen, so a
stray anti-aliased edge cannot slip into the game silently.

    python3 tools/sprite_import.py          # then ./build.sh
"""
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

RGB2PEN = {rgb: pen for pen, rgb in enumerate(gr.PAL)}
UNSTABLE = sorted(p for p in range(16)
                  if len({pal[p] for pal in lg.PALETTES}) > 1)


def nearest(rgb):
    return min(range(16), key=lambda p: sum(
        (a - b) ** 2 for a, b in zip(gr.PAL[p], rgb)))


def read_frame(name, indir="art/sprites"):
    img = Image.open(f"{indir}/{name}.png").convert("RGBA")
    if img.size != (8, 16):
        raise SystemExit(f"{name}.png is {img.size[0]}x{img.size[1]}, "
                         f"must be 8x16 (native pixels, not scaled)")
    px = img.load()
    rows, pens = [], set()
    for y in range(16):
        row = ""
        for x in range(8):
            r, g, b, a = px[x, y]
            if a < 128:
                row += '.'
                continue
            pen = RGB2PEN.get((r, g, b))
            if pen is None:
                n = nearest((r, g, b))
                raise SystemExit(
                    f"{name}.png pixel ({x},{y}) is rgb({r},{g},{b}), "
                    f"not a CPC pen -- nearest is pen {n} "
                    f"rgb{gr.PAL[n]}.  Paint with art/cpc-pens.gpl.")
            pens.add(pen)
            row += f"{pen:x}"
        rows.append(row)
    return rows, pens


def main(indir="art/sprites"):
    src, frames = frames_from_source()
    changed, warned = [], []
    for name in frames:                      # existing order, exactly
        rows, pens = read_frame(name, indir)
        if rows != frames[name]:
            changed.append(name)
        odd = pens & set(UNSTABLE)
        if odd and name.startswith("spr_mech"):
            warned.append((name, sorted(odd)))
        block = (f"    '{name}': [\n"
                 + "".join(f'        "{r}",\n' for r in rows)
                 + "    ],\n")
        pat = re.compile(r"    '" + name
                         + r"': \[\n(?:        \"[^\"]*\",\n)+    \],\n")
        assert len(pat.findall(src)) == 1, f"{name}: frame block not found"
        src = pat.sub(lambda m: block, src, count=1)
    open("tools/sprites_art.py", "w").write(src)

    for name, odd in warned:
        print(f"WARN {name} uses zone-recoloured pens {odd} -- the "
              f"player will change colour between zones")
    if changed:
        print(f"imported changes to: {', '.join(changed)}")
        print("now run ./build.sh")
    else:
        print("no changes -- PNGs and sprites_art.py already agree")


if __name__ == "__main__":
    main()
