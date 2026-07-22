#!/usr/bin/env python3
"""gfx_ref.py -- render every sprite, tile and HUD glyph to docs/gfx/*.png.

Sprites are decoded from the masked (mask,data) bytes in src/main.asm --
the source of truth -- tiles and glyphs from tools/level_gen.py.  Mode 0
pixels are drawn 2x wide, everything scaled x4, transparent background.

Usage:  python3 tools/gfx_ref.py
"""
import re
import struct
import sys
import zlib

sys.path.insert(0, "tools")
import level_gen as lg

SCALE = 4
# Mechanical-zone palette as RGB (the default look)
HW = {0x54:(0,0,0),0x4B:(255,255,255),0x40:(140,140,140),0x44:(0,0,128),
      0x57:(0,128,255),0x4C:(255,0,0),0x4E:(255,120,0),0x4A:(255,255,0),
      0x47:(255,160,140),0x52:(0,255,0),0x53:(0,255,255),0x45:(160,0,160),
      0x5C:(128,0,0),0x56:(0,128,0),0x5E:(128,128,0),0x5F:(128,128,255)}
PAL = [HW[v] for v in lg.PALETTES[0]]


def png(path, w, h, rgba_rows):
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
    raw = b''.join(b'\x00' + r for r in rgba_rows)
    data = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    open(path, 'wb').write(data)


def save_grid(path, grids, gap_px=8):
    """grids: list of 2D pen arrays (None = transparent); side by side.
    Mode 0 pixels render 2*SCALE wide, SCALE tall."""
    h = max(len(g) for g in grids)
    wpx = sum(len(g[0]) * 2 * SCALE for g in grids) + gap_px * (len(grids) - 1)
    rows = []
    for y in range(h * SCALE):
        row = b''
        for gi, g in enumerate(grids):
            for x in range(len(g[0])):
                p = g[y // SCALE][x] if y // SCALE < len(g) else None
                px = bytes(PAL[p]) + b'\xff' if p is not None else b'\x00\x00\x00\x00'
                row += px * (2 * SCALE)
            if gi < len(grids) - 1:
                row += b'\x00\x00\x00\x00' * gap_px
        assert len(row) == wpx * 4
        rows.append(row)
    png(path, wpx, h * SCALE, rows)


def dec_left(b):
    return ((b >> 7) & 1) | ((b >> 3) & 1) << 1 | ((b >> 5) & 1) << 2 | ((b >> 1) & 1) << 3


def sprite_frames(asm):
    """label -> 16x8 pen grid (None = transparent) from masked defb data."""
    frames, label, rows = {}, None, []
    for line in asm.splitlines():
        m = re.match(r'^(spr_[a-z0-9_]+):', line)
        if m:
            label, rows = m.group(1), []
            frames[label] = rows
            continue
        if label is None:
            continue
        b = re.findall(r'#([0-9A-F]{2})', line)
        if len(b) == 8 and line.strip().startswith('defb'):
            vals = [int(x, 16) for x in b]
            row = []
            for i in range(0, 8, 2):
                mask, data = vals[i], vals[i + 1]
                row.append(None if (mask & 0xAA) == 0xAA else dec_left(data))
                row.append(None if (mask & 0x55) == 0x55 else dec_left(data << 1))
            rows.append(row)
            if len(rows) == 16:
                label = None
    return frames


def tile_grid(name):
    art = lg.TILES[lg.tile_index(name)][2]
    return [[None if ch == '.' else int(ch, 16) for ch in row] for row in art]


def glyph_grid(name, pen):
    rows = [v << 2 for v in lg.GLYPHS[name]] + [0]
    return [[pen if r & (0x80 >> x) else None for x in range(8)] for r in rows]


def main():
    import os
    os.makedirs('docs/gfx', exist_ok=True)
    from sprites_art import FRAMES
    fr = {name: [[None if ch == '.' else int(ch, 16) for ch in row]
                 for row in art] for name, art in FRAMES.items()}
    pairs = {
        'spr_mech_walk': ['spr_mech_r', 'spr_mech_r_b'],
        'spr_mech_climb': ['spr_mech_climb', 'spr_mech_climb_b'],
        'spr_mech_duck': ['spr_mech_duck'],
        'spr_riot': ['spr_riot_r', 'spr_riot_r_b'],
        'spr_coat': ['spr_coat_r', 'spr_coat_r_b'],
        'spr_throw': ['spr_throw_a', 'spr_throw_b'],
        'spr_rock': ['spr_rock'],
        'spr_drips': ['spr_drip_red', 'spr_drip_white'],
        'spr_steam': ['spr_steam_a', 'spr_steam_b'],
    }
    for out, labels in pairs.items():
        save_grid(f'docs/gfx/{out}.png', [fr[l] for l in labels])
    for i, (name, _t, _a) in enumerate(lg.TILES):
        save_grid(f'docs/gfx/tile_{i:02}_{name}.png', [tile_grid(name)])
    for name, pen in (('KEY', 2), ('HEART', 5), ('BOLT', 7)):
        save_grid(f'docs/gfx/glyph_{name.lower()}.png', [glyph_grid(name, pen)])
    print(f'{len(pairs) + len(lg.TILES) + 3} images in docs/gfx/')


if __name__ == '__main__':
    main()
