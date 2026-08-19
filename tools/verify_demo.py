#!/usr/bin/env python3
"""Drive the demo's real binary frame by frame and check it against an
independent Python model: the camera, the set edits each beat makes,
and the rendered picture, pixel for pixel."""
import sys

sys.path.insert(0, 'tools')
from z80mini import Z80, MEM, load, run
import level_gen as lg
import demo_scene as D
import gfx_ref as gr
from sprites_art import FRAMES

sym = load('build/demo.bin', 'build/demo.sym')
S = lambda n: sym[n.upper()]
BAND, W = D.BAND, 80
LO = S('LINE_OFFSETS')
line_off = [MEM[LO + 2 * y] | MEM[LO + 2 * y + 1] << 8 for y in range(200)]

ok = True
def chk(c, m):
    global ok
    ok &= c
    print(('OK   ' if c else 'FAIL ') + m)


def dec_left(b):
    return ((b >> 7) & 1) | ((b >> 3) & 1) << 1 | ((b >> 5) & 1) << 2 | ((b >> 1) & 1) << 3


def grab():
    px = [[0] * 160 for _ in range(BAND)]
    for y in range(BAND):
        base = 0xC000 + line_off[y]
        for x in range(W):
            b = MEM[base + x]
            px[y][x * 2] = dec_left(b)
            px[y][x * 2 + 1] = dec_left(b << 1)
    return px


# ------------------------------------------------------------- the model
scene = D.build()
objs = sorted(((ch, c, r) for (c, r), ch in scene.items()),
              key=lambda o: (o[2], o[1]))
tiles = {}                                  # tile index -> 8x8 pen grid
for i, (_n, _t, art) in enumerate(lg.TILES):
    tiles[i] = [[None if p == '.' else int(p, 16) for p in row] for row in art]
base_ids = [lg.tile_index(lg.CHARMAP[ch]) for ch, _c, _r in objs]
index = {(c, r): i for i, (_ch, c, r) in enumerate(objs)}

SW_I = index[(D.SWITCH_COL, D.DECK_A - 2)]
KEY_I = index[(D.KEY_COL, D.DECK_B - 2)]
DOOR_IS = [index[(D.DOOR_COL, D.DECK_B - i)] for i in range(5, 0, -1)]

ARC = S('JUMP_ARC')
arc = [MEM[ARC + i] - 256 if MEM[ARC + i] > 127 else MEM[ARC + i]
       for i in range(30)]


def model_ids_after(seg_done):
    """Object tile ids once the first `seg_done` beats have played."""
    ids = list(base_ids)
    for dx, dy, n, a, e in D.SCRIPT[:seg_done]:
        if e == D.EV_SWITCH:
            ids[SW_I] = lg.tile_index('switch_on')
        elif e == D.EV_KEY:
            ids[KEY_I] = 0
        elif e == D.EV_DOOR:
            for i in DOOR_IS:
                ids[i] = 0
    return ids


def hero_line(anim, elapsed):
    if anim != D.JUMP:
        return D.HERO_Y
    return D.HERO_Y + arc[min(elapsed, len(arc) - 1)]


def model(cam, anim, elapsed, ctr, ids, steam, phase):
    px = [[0] * 160 for _ in range(BAND)]
    cx, cy = cam
    for (ch, col, row), tid in zip(objs, ids):
        sx, sy = col * 4 - cx, row * 8 - cy
        if not (-3 <= sx <= 79 and -7 <= sy <= BAND - 1):
            continue
        for ty in range(8):
            yy = sy + ty
            if not (0 <= yy < BAND):
                continue
            for tb in range(4):
                xx = sx + tb
                if not (0 <= xx < W):
                    continue
                for h in (0, 1):
                    p = tiles[tid][ty][tb * 2 + h]
                    px[yy][xx * 2 + h] = 0 if p is None else p

    def sprite(name, bx, by):
        for ay, arow in enumerate(FRAMES[name]):
            for ax, p in enumerate(arow):
                if p != '.' and 0 <= by + ay < BAND and 0 <= bx * 2 + ax < 160:
                    px[by + ay][bx * 2 + ax] = int(p, 16)

    if steam:                                   # drawn before the hero
        sx = D.STEAM_COL * 4 - cx
        sy = D.STEAM_ROW * 8 - cy
        if 0 <= sx < W - 4 and 0 <= sy < BAND - 16:
            sprite('spr_steam_a' if not (ctr & 4) else 'spr_steam_b', sx, sy)

    if anim == D.CLIMB:
        # the beat picks the climbing hands AND the walking legs, so
        # the strike is always the footfall or the rung grab
        name = 'spr_mech_climb' + ('_b' if phase & 4 else '')
    elif anim == D.WALK_L:
        name = 'spr_mech_l' + ('_b' if phase & 4 else '')
    elif anim == D.WALK_R:
        name = 'spr_mech_r' + ('_b' if phase & 4 else '')
    else:                                       # jump and stand hold A
        name = 'spr_mech_r'
    sprite(name, D.HERO_X, hero_line(anim, elapsed))
    return px


# ------------------------------------------------------------ the engine
def boot():
    z = Z80()
    run(z, S('SCENE_RESET'))
    MEM[S('SEG_PTR')] = S('DEMO_SCRIPT') & 0xFF
    MEM[S('SEG_PTR') + 1] = S('DEMO_SCRIPT') >> 8
    run(Z80(), S('SEG_LOAD'))
    for lbl, v in (('CAM_XF', D.CAM_X0), ('CAM_YF', D.CAM_Y0)):
        MEM[S(lbl)] = 0
        MEM[S(lbl) + 1] = v & 0xFF
    MEM[S('CAM_XH')] = D.CAM_X0 >> 8
    MEM[S('CAM_YH')] = D.CAM_Y0 >> 8
    run(Z80(), S('CAM_WHOLE'))
    MEM[S('LAST_FRAME')] = 0
    MEM[S('FRAME50')] = 0
    MEM[S('ANIM_CTR')] = 0


def render(ctr):
    """Exactly what main_loop's draw half does, into #C000."""
    for a in range(0xC000, 0x10000):
        MEM[a] = 0
    MEM[S('DRAW_PAGE')] = 0xC0
    for i in range(2):
        MEM[S('PASS_X') + i] = MEM[S('CAM_IX') + i]
        MEM[S('PASS_Y') + i] = MEM[S('CAM_IY') + i]
    MEM[S('BLIT_BLACK')] = 0
    MEM[S('ANIM_CTR')] = ctr
    n = run(Z80(), S('OBJECT_PASS'))
    n += run(Z80(), S('DRAW_STEAM'))
    n += run(Z80(), S('DRAW_HERO'))
    return grab(), n, MEM[S('BEAT_PHASE')]


# --------------------------------------------------------------- the run
boot()
track = D.camera_track()
seg_bounds, f = [], 0
for dx, dy, n, a, e in D.SCRIPT:
    f += n
    seg_bounds.append(f)

# probe two frames inside every beat, plus both sides of each event
probes = set()
f0 = 0
for si, end in enumerate(seg_bounds):
    probes.update({f0 + 1, f0 + max(1, (end - f0) // 2), end})
    f0 = end
probes = sorted(p for p in probes if 1 <= p <= seg_bounds[-1])

bad_px = bad_cam = bad_ids = 0
for fr in range(1, seg_bounds[-1] + 1):
    MEM[S('FRAME50')] = fr & 0xFF
    z = Z80()
    run(z, S('ADVANCE_CAMERA'))
    if z.fc:
        chk(fr == seg_bounds[-1] + 1, f'script ended at frame {fr}')
        break

    cam = (MEM[S('CAM_IX')] | MEM[S('CAM_IX') + 1] << 8,
           MEM[S('CAM_IY')] | MEM[S('CAM_IY') + 1] << 8)
    if cam != track[fr]:
        bad_cam += 1
        if bad_cam < 4:
            print(f'  cam frame {fr}: {cam} want {track[fr]}')

    seg_i = next(i for i, b in enumerate(seg_bounds) if fr <= b)
    seg_start = 0 if seg_i == 0 else seg_bounds[seg_i - 1]
    elapsed = fr - seg_start
    anim = D.SCRIPT[seg_i][3]
    event = D.SCRIPT[seg_i][4]

    ids = model_ids_after(seg_i + 1)
    got_ids = [MEM[S('DEMO_OBJECTS') + 3 * i] for i in range(len(objs))]
    if got_ids != ids:
        bad_ids += 1
        if bad_ids < 4:
            diff = [i for i, (g, w) in enumerate(zip(got_ids, ids)) if g != w]
            print(f'  set frame {fr} seg {seg_i}: objects {diff[:6]} differ')

    if fr in probes:
        ctr = fr & 0xFF
        got, _n, phase = render(ctr)
        want = model(cam, anim, elapsed, ctr, ids,
                     event == D.EV_STEAM, phase)
        d = sum(1 for y in range(BAND) for x in range(160)
                if got[y][x] != want[y][x])
        if d:
            bad_px += 1
            if bad_px < 4:
                print(f'  pixels frame {fr} seg {seg_i} anim {anim}: {d} differ')

chk(bad_cam == 0, f'camera matches the model on all {seg_bounds[-1]} frames ({bad_cam} off)')
chk(bad_ids == 0, f'the set edits land on the right beats ({bad_ids} off)')
chk(bad_px == 0, f'{len(probes)} probe frames render pixel-exact ({bad_px} off)')

# the script must end, and end where it should
MEM[S('FRAME50')] = (seg_bounds[-1] + 1) & 0xFF
z = Z80()
run(z, S('ADVANCE_CAMERA'))
chk(z.fc, f'script ends after {seg_bounds[-1]} frames '
          f'({seg_bounds[-1] / 50:.1f}s) and restarts')

# nothing may be drawn outside the 16:9 band
boot()
for fr in range(1, 200):
    MEM[S('FRAME50')] = fr & 0xFF
    run(Z80(), S('ADVANCE_CAMERA'))
render(7)
spill = sum(1 for y in range(BAND, 200) for x in range(80)
            if MEM[0xC000 + line_off[y] + x])
chk(spill == 0, f'nothing drawn outside the 16:9 band ({spill} stray bytes)')

print('\nALL PASS' if ok else '\nFAILED')
sys.exit(0 if ok else 1)
