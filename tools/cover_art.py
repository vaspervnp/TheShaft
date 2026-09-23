#!/usr/bin/env python3
"""cover_art.py -- the sleeve artwork for the 3" disc, drawn from scratch.

There is no vector renderer and no photo source on this machine, so the
cover is *painted in code*: PIL primitives for everything hard-edged,
and for everything soft the trick that makes this look like 1987 rather
than like a computer drawing -- a gradient computed at postage-stamp
size (a few thousand pixels, cheap in pure Python) and blown up BICUBIC.
That is what an airbrush actually does: low spatial frequency, smooth
falloff.  Hard edges go on top at full resolution.

Everything is drawn at 2x the print size and resampled down at the end,
which is where the anti-aliasing comes from.

    python3 tools/cover_art.py --selftest    # swatch sheet, build/
    python3 tools/cover_art.py               # -> docs/cover_*.png
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ----------------------------------------------------------------------
# geometry.  The inlay is a 3" disc case wrap: back | spine | front.
# ----------------------------------------------------------------------
DPI = 300
MM = DPI / 25.4                     # print pixels per millimetre
INLAY_MM = (210.0, 125.0)
FRONT_MM, SPINE_MM = 100.0, 10.0
LABEL_MM = 70.0                     # the 3" shell takes a 70 x 45 label

UNITS = (1000, 1250)                # the front face's design grid
WORK = 2.4                          # working pixels per design unit
PRINT_PPU = FRONT_MM * MM / UNITS[0]   # print pixels per design unit


def px(u):
    return int(round(u * WORK))


FONTS = {
    "bold": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "reg": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "narrow": "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf",
    "cond": "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
}
_fcache = {}


def font(name, size_u):
    key = (name, int(round(size_u * WORK)))
    if key not in _fcache:
        _fcache[key] = ImageFont.truetype(FONTS[name], key[1])
    return _fcache[key]


# ----------------------------------------------------------------------
# colour
# ----------------------------------------------------------------------
def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def ramp(stops):
    """stops = [(position 0..1, colour), ...] -> f(t) -> rgb."""
    stops = sorted(stops)

    def f(t):
        t = max(0.0, min(1.0, t))
        prev = stops[0]
        for s in stops:
            if t <= s[0]:
                if s[0] == prev[0]:
                    return s[1]
                return mix(prev[1], s[1], (t - prev[0]) / (s[0] - prev[0]))
            prev = s
        return stops[-1][1]
    return f


def shade(c, k):
    """k < 1 darkens, k > 1 lightens, both without leaving the gamut."""
    if k <= 1:
        return tuple(int(round(v * k)) for v in c)
    return tuple(int(round(v + (255 - v) * (k - 1))) for v in c)


# ----------------------------------------------------------------------
# the airbrush: compute small, upscale smooth
# ----------------------------------------------------------------------
def airbrush(size, fn, coarse=(96, 120)):
    """fn(x, y) -> rgb, x and y in 0..1, evaluated on a coarse grid and
    upscaled bicubic.  The whole point: soft light costs nothing."""
    cw, ch = coarse
    small = Image.new("RGB", (cw, ch))
    put = small.load()
    for j in range(ch):
        v = (j + 0.5) / ch
        for i in range(cw):
            put[i, j] = fn((i + 0.5) / cw, v)
    return small.resize(size, Image.BICUBIC)


def alpha_brush(size, fn, coarse=(96, 120)):
    """fn(x, y) -> 0..255 coverage; the same trick for a soft mask."""
    cw, ch = coarse
    small = Image.new("L", (cw, ch))
    put = small.load()
    for j in range(ch):
        v = (j + 0.5) / ch
        for i in range(cw):
            put[i, j] = max(0, min(255, int(fn((i + 0.5) / cw, v))))
    return small.resize(size, Image.BICUBIC)


def glow(img, cx, cy, r, colour, strength=1.0, aspect=1.0):
    """Lay a soft radial light over img (design units)."""
    w, h = img.size
    cx, cy, r = cx * WORK, cy * WORK, r * WORK

    def f(x, y):
        dx = (x * w - cx) / r
        dy = (y * h - cy) / (r * aspect)
        d = math.hypot(dx, dy)
        return 255 * strength * max(0.0, 1.0 - d) ** 2
    img.paste(Image.new("RGB", img.size, colour), (0, 0), alpha_brush(img.size, f))
    return img


def wash(size, stops, horizontal=False):
    """A straight linear gradient across the whole panel."""
    f = ramp(stops)
    return airbrush(size, lambda x, y: f(x if horizontal else y), (64, 96))


# ----------------------------------------------------------------------
# period print texture
# ----------------------------------------------------------------------
def posterise(img, levels):
    """Flatten to N steps per channel -- four-colour-press flatness."""
    n = max(2, levels)
    lut = [min(255, int(round(round(v * (n - 1) / 255) * 255 / (n - 1))))
           for v in range(256)]
    return img.point(lut * 3)


def halftone(img, pitch=9.0, angle=15.0, ink=(0, 0, 0), strength=0.5):
    """A rosette of dots whose size follows the image's darkness -- the
    one texture that says 'printed in 1987' louder than any filter."""
    w, h = img.size
    dots = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(dots)
    small = img.convert("L").resize((max(1, int(w / pitch)), max(1, int(h / pitch))),
                                    Image.BOX)
    sp = small.load()
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    n = int(math.hypot(w, h) / pitch) + 2
    for j in range(-n, n):
        for i in range(-n, n):
            x = (i * ca - j * sa) * pitch + w / 2
            y = (i * sa + j * ca) * pitch + h / 2
            if not (-pitch < x < w + pitch and -pitch < y < h + pitch):
                continue
            sx = min(small.size[0] - 1, max(0, int(x / pitch)))
            sy = min(small.size[1] - 1, max(0, int(y / pitch)))
            r = pitch * 0.62 * (1.0 - sp[sx, sy] / 255.0)
            if r > 0.35:
                d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    dots = dots.point(lambda v: int(v * strength))
    out = img.copy()
    out.paste(Image.new("RGB", img.size, ink), (0, 0), dots)
    return out


def scanlines(img, period=3, strength=0.12):
    w, h = img.size
    veil = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(veil)
    for y in range(0, h, period):
        d.line((0, y, w, y), fill=int(255 * strength))
    out = img.copy()
    out.paste(Image.new("RGB", (w, h), (0, 0, 0)), (0, 0), veil)
    return out


def grain(img, amount=6, seed=1):
    """Cheap paper/press noise: a coarse random field, blown up soft."""
    import random
    r = random.Random(seed)
    cw, ch = 200, 250
    n = Image.new("L", (cw, ch))
    n.putdata([128 + r.randint(-amount * 4, amount * 4) for _ in range(cw * ch)])
    n = n.resize(img.size, Image.BICUBIC)
    out = img.copy()
    out.paste(Image.new("RGB", img.size, (255, 255, 255)), (0, 0),
              n.point(lambda v: max(0, v - 128)))
    out.paste(Image.new("RGB", img.size, (0, 0, 0)), (0, 0),
              n.point(lambda v: max(0, 128 - v)))
    return out


# ----------------------------------------------------------------------
# shapes as masks -- the only sane way to light a drawn object
#
# Build the silhouette once, then everything else falls out of it: the
# rim light is the silhouette minus itself shifted away from the lamp,
# the cast shadow is the silhouette shifted and blurred, and a gradient
# can be poured into it without touching a single other pixel.
# ----------------------------------------------------------------------
def mask_of(size, polys, ellipses=(), lines=()):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for poly in polys:
        d.polygon([(px(a), px(b)) for a, b in poly], fill=255)
    for (x0, y0, x1, y1) in ellipses:
        d.ellipse((px(x0), px(y0), px(x1), px(y1)), fill=255)
    for (x0, y0, x1, y1, w) in lines:
        d.line((px(x0), px(y0), px(x1), px(y1)), fill=255, width=px(w))
    return m


def paint(img, mask, fill):
    """Pour a colour or a whole image through a mask."""
    src = fill if isinstance(fill, Image.Image) else Image.new("RGB", img.size, fill)
    img.paste(src, (0, 0), mask)
    return img


def rim(img, mask, dx, dy, colour, width=2.0):
    """The lit edge: silhouette minus silhouette shifted away from the
    lamp.  (dx, dy) is the direction the LIGHT COMES FROM, so (-1, -1)
    lights the top-left edge of the object."""
    w = max(1, px(width))
    cut = Image.new("L", img.size, 0)
    cut.paste(mask, (int(round(-dx * w)), int(round(-dy * w))))
    edge = Image.composite(mask, Image.new("L", img.size, 0),
                           cut.point(lambda v: 255 - v))
    img.paste(Image.new("RGB", img.size, colour), (0, 0), edge)
    return img


def drop(img, mask, dx, dy, colour=(0, 0, 0), blur=6.0, opacity=0.55):
    """A soft cast shadow, in design units."""
    sh = Image.new("L", img.size, 0)
    sh.paste(mask, (px(dx), px(dy)))
    sh = sh.filter(ImageFilter.GaussianBlur(px(blur)))
    sh = sh.point(lambda v: int(v * opacity))
    img.paste(Image.new("RGB", img.size, colour), (0, 0), sh)
    return img


def beam(img, apex, left, right, colour, opacity=0.5, softness=18.0):
    """A shaft of light: a triangle, blurred, fading along its length."""
    m = mask_of(img.size, [[apex, left, right]])
    m = m.filter(ImageFilter.GaussianBlur(px(softness)))
    fade = alpha_brush(img.size, lambda x, y: 255 * (1.0 - y) ** 1.4, (48, 64))
    m = Image.composite(m, Image.new("L", img.size, 0), fade)
    m = m.point(lambda v: int(v * opacity))
    img.paste(Image.new("RGB", img.size, colour), (0, 0), m)
    return img


# ----------------------------------------------------------------------
# hand-built logotype: drawn letterforms, not set type.
#
# Every glyph is (advance, solids, holes) in a 10 x 14 cell.  The word is
# rasterised into a mask first, which is what makes a real bevel
# possible: the lit edge is simply "the mask minus the mask shifted down
# and right", and the counters of A, R, 8 and B are punched out of the
# mask instead of being painted over.
# ----------------------------------------------------------------------
BAR = 3.0                                   # stroke weight in cell units

GLYPHS = {
    "T": (10, [[(0, 0), (10, 0), (10, BAR), (6.5, BAR), (6.5, 14), (3.5, 14),
                (3.5, BAR), (0, BAR)]], []),
    "H": (10, [[(0, 0), (3, 0), (3, 5.5), (7, 5.5), (7, 0), (10, 0), (10, 14),
                (7, 14), (7, 8.5), (3, 8.5), (3, 14), (0, 14)]], []),
    "E": (9.4, [[(0, 0), (9.4, 0), (9.4, BAR), (3, BAR), (3, 5.6), (8.2, 5.6),
                 (8.2, 8.4), (3, 8.4), (3, 11), (9.4, 11), (9.4, 14), (0, 14)]], []),
    # the S is chamfered at all four outer corners: built square it reads
    # as a seven-segment 5, and the lift readout in the game IS seven-segment
    "S": (9.6, [[(1.3, 0), (9.6, 0), (9.6, 3), (3, 3), (3, 5.4), (8.3, 5.4),
                 (9.6, 6.7), (9.6, 12.7), (8.3, 14), (0, 14), (0, 11),
                 (6.6, 11), (6.6, 8.6), (1.3, 8.6), (0, 7.3), (0, 1.3)]], []),
    "A": (10, [[(3.0, 1.3), (3.7, 0), (6.3, 0), (7.0, 1.3), (10, 14),
                (6.9, 14), (6.35, 11.3), (3.65, 11.3), (3.1, 14), (0, 14)]],
          [[(4.3, 8.5), (5.7, 8.5), (5.0, 5.0)]]),
    "F": (9.4, [[(0, 0), (9.4, 0), (9.4, BAR), (3, BAR), (3, 5.8), (8.2, 5.8),
                 (8.2, 8.8), (3, 8.8), (3, 14), (0, 14)]], []),
    "I": (4.2, [[(0.6, 0), (3.6, 0), (3.6, 14), (0.6, 14)]], []),
    "R": (9.8, [[(0, 0), (8.2, 0), (9.8, 1.8), (9.8, 6.6), (8.3, 8.2),
                 (9.8, 14), (6.6, 14), (5.3, 8.8), (3, 8.8), (3, 14), (0, 14)]],
          [[(3, 2.8), (6.4, 2.8), (6.8, 3.2), (6.8, 5.4), (6.4, 5.8), (3, 5.8)]]),
    "V": (10, [[(0, 0), (3.1, 0), (5, 10.2), (6.9, 0), (10, 0), (6.7, 14),
                (3.3, 14)]], []),
    "B": (9.8, [[(0, 0), (8.2, 0), (9.8, 1.6), (9.8, 5.4), (8.6, 6.8),
                 (9.8, 8.2), (9.8, 12.4), (8.2, 14), (0, 14)]],
          [[(3, 2.6), (6.4, 2.6), (6.8, 3.0), (6.8, 5.0), (6.4, 5.4), (3, 5.4)],
           [(3, 8.6), (6.6, 8.6), (7.0, 9.0), (7.0, 11.0), (6.6, 11.4), (3, 11.4)]]),
    "8": (9.6, [[(1.4, 0), (8.2, 0), (9.6, 1.4), (9.6, 5.4), (8.4, 6.8),
                 (9.6, 8.2), (9.6, 12.6), (8.2, 14), (1.4, 14), (0, 12.6),
                 (0, 8.2), (1.2, 6.8), (0, 5.4), (0, 1.4)]],
          [[(3, 2.6), (6.6, 2.6), (6.6, 5.2), (3, 5.2)],
           [(3, 8.6), (6.6, 8.6), (6.6, 11.4), (3, 11.4)]]),
    "0": (9.6, [[(1.4, 0), (8.2, 0), (9.6, 1.4), (9.6, 12.6), (8.2, 14),
                 (1.4, 14), (0, 12.6), (0, 1.4)]],
          [[(3, 2.8), (6.6, 2.8), (6.6, 11.2), (3, 11.2)]]),
    "-": (7.0, [[(0.6, 5.6), (6.4, 5.6), (6.4, 8.4), (0.6, 8.4)]], []),
    " ": (4.6, [], []),
}


def word_width(text, h, gap):
    """Design-unit width of `text` set at cap height h."""
    u = h / 14.0
    return sum(GLYPHS.get(c.upper(), GLYPHS[" "])[0] for c in text) * u \
        + gap * u * max(0, len(text) - 1)


def word_mask(text, h, gap, size, org):
    """Rasterise the word into an L mask the size of the target panel."""
    u = h / 14.0
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    x = org[0]
    for c in text:
        adv, solids, holes = GLYPHS.get(c.upper(), GLYPHS[" "])
        for poly in solids:
            d.polygon([(px(x + a * u), px(org[1] + b * u)) for a, b in poly], fill=255)
        for poly in holes:
            d.polygon([(px(x + a * u), px(org[1] + b * u)) for a, b in poly], fill=0)
        x += (adv + gap) * u
    return m


def _shift(mask, dx, dy):
    out = Image.new("L", mask.size, 0)
    out.paste(mask, (px(dx), px(dy)))
    return out


def _grow(mask, r_u):
    r = max(1, px(r_u))
    return mask.filter(ImageFilter.MaxFilter(r * 2 + 1 if r * 2 + 1 <= 9 else 9))


def logotype(img, text, x, y, h, *, gap=1.1, face=(255, 206, 30), lit=None,
             dark=None, outline=(0, 0, 0), ow=0.07, shadow=None,
             sh=(0.09, 0.11), bevel=0.055, fit=None):
    """Paint `text` onto img as a drawn logotype and return its width.

    fit  -- if given, the cap height is reduced until the word fits that
            width, so a long title can never run off the cover.
    lit / dark -- bevel colours: the top-left edge and the bottom-right
            edge of every stroke, which is what gives the plastic, moulded
            look those logos had.
    """
    if fit:
        while word_width(text, h, gap) > fit and h > 4:
            h *= 0.98
    mask = word_mask(text, h, gap, img.size, (x, y))
    if shadow:
        img.paste(Image.new("RGB", img.size, shadow), (0, 0),
                  _shift(mask, sh[0] * h, sh[1] * h))
    if outline:
        img.paste(Image.new("RGB", img.size, outline), (0, 0), _grow(mask, ow * h))
    img.paste(Image.new("RGB", img.size, face), (0, 0), mask)
    if lit:
        b = px(bevel * h)
        edge = Image.new("L", img.size, 0)
        edge.paste(mask, (0, 0))
        cut = Image.new("L", img.size, 0)
        cut.paste(mask, (b, b))
        img.paste(Image.new("RGB", img.size, lit), (0, 0),
                  Image.composite(edge, Image.new("L", img.size, 0), cut.point(
                      lambda v: 255 - v)))
    if dark:
        b = px(bevel * h)
        edge = Image.new("L", img.size, 0)
        edge.paste(mask, (0, 0))
        cut = Image.new("L", img.size, 0)
        cut.paste(mask, (-b, -b))
        img.paste(Image.new("RGB", img.size, dark), (0, 0),
                  Image.composite(edge, Image.new("L", img.size, 0), cut.point(
                      lambda v: 255 - v)))
    return word_width(text, h, gap)


def text_at(draw, s, x, y, fnt, fill, anchor="la", spacing=0.0, stroke=0,
            stroke_fill=(0, 0, 0)):
    """Letter-spaced text in design units (PIL has no tracking)."""
    if not spacing:
        draw.text((px(x), px(y)), s, font=fnt, fill=fill, anchor=anchor,
                  stroke_width=px(stroke) if stroke else 0,
                  stroke_fill=stroke_fill)
        return
    widths = [draw.textlength(c, font=fnt) for c in s]
    total = sum(widths) + px(spacing) * (len(s) - 1)
    cx = px(x)
    if anchor[0] == "m":
        cx -= total / 2
    elif anchor[0] == "r":
        cx -= total
    for c, w in zip(s, widths):
        draw.text((cx, px(y)), c, font=fnt, fill=fill, anchor="l" + anchor[1],
                  stroke_width=px(stroke) if stroke else 0,
                  stroke_fill=stroke_fill)
        cx += w + px(spacing)


# ----------------------------------------------------------------------
# panels and output
# ----------------------------------------------------------------------
def panel(w_u, h_u, bg=(0, 0, 0)):
    img = Image.new("RGB", (px(w_u), px(h_u)), bg)
    return img, ImageDraw.Draw(img)


def to_print(img, mm_w):
    """Resample a working panel down to 300 dpi print pixels."""
    w = int(round(mm_w * MM))
    h = int(round(img.size[1] * w / img.size[0]))
    return img.resize((w, h), Image.LANCZOS)


def shot(path, w_u, border=None, frame=3.0):
    """A real screenshot, scaled into the design grid.  Period sleeves
    printed these on the back with a proud caption, and ours can be the
    genuine article: docs/shot_*.png come out of the emulator."""
    im = Image.open(path).convert("RGB")
    h_u = w_u * im.size[1] / im.size[0]
    im = im.resize((px(w_u), px(h_u)), Image.LANCZOS)
    if border:
        bg = Image.new("RGB", (px(w_u + 2 * frame), px(h_u + 2 * frame)), border)
        bg.paste(im, (px(frame), px(frame)))
        return bg, w_u + 2 * frame, h_u + 2 * frame
    return im, w_u, h_u


def inlay(back, spine, front):
    """back | spine | front, in one 210 x 125 mm wrap.  The spine is
    forced to the faces' exact height -- a rounding difference of one
    pixel leaves a black hairline along the fold."""
    h = back.size[1]
    if spine.size[1] != h:
        spine = spine.resize((spine.size[0], h), Image.LANCZOS)
    if front.size[1] != h:
        front = front.resize((front.size[0], h), Image.LANCZOS)
    w = back.size[0] + spine.size[0] + front.size[0]
    out = Image.new("RGB", (w, h), rgb("#14161F"))
    out.paste(back, (0, 0))
    out.paste(spine, (back.size[0], 0))
    out.paste(front, (back.size[0] + spine.size[0], 0))
    return out


def save(img, path, mm_w, ink=True):
    out = to_print(img, mm_w)
    if ink:
        out = clamp_ink(out)
    out.save(path, dpi=(DPI, DPI))
    print(f"{path}  {out.size[0]}x{out.size[1]} px  "
          f"{mm_w:.0f}mm @ {DPI}dpi")
    return out


# ----------------------------------------------------------------------
def selftest():
    os.makedirs("build", exist_ok=True)
    img, d = panel(1000, 1250, (10, 8, 20))
    sky = wash(img.size, [(0.0, rgb("#1a0f3a")), (0.55, rgb("#c8202a")),
                          (1.0, rgb("#ffce1e"))])
    img.paste(sky)
    glow(img, 500, 520, 320, rgb("#fff0a0"), 0.85)
    logotype(img, "THE SHAFT", 60, 880, 170, fit=880, face=rgb("#ffce1e"),
             lit=rgb("#fff6c0"), dark=rgb("#b45a00"), outline=(0, 0, 0),
             shadow=(50, 8, 8))
    logotype(img, "REVIVE8BIT", 60, 1110, 72, fit=560, face=rgb("#29b6d8"),
             lit=rgb("#d8f6ff"), dark=rgb("#12607a"), outline=(0, 0, 0))
    text_at(d, "AMSTRAD CPC 6128", 500, 60, font("bold", 46), (255, 255, 255),
            anchor="ma", spacing=6)
    img = halftone(img, pitch=10, strength=0.35)
    img = grain(img, 5)
    save(img, "build/cover_selftest.png", 100)




# ======================================================================
#
#   THE FRONT FACE
#
#   A mile of shaft above a mechanic who is looking up it.  The barrel
#   is built from one geometric idea: every level k is the near one
#   scaled by p^k towards the vanishing point, so the walls come out as
#   two straight lines to the aperture and the decks as ellipses whose
#   radii, thickness and spacing all shrink by the same factor.  Nothing
#   can cross anything else, which is the failure that would quietly
#   ruin the frame.
#
#   The three zones of the game are told by the deck lips alone --
#   sodium orange at the bottom, grow-lamp green in the middle, cold
#   blue at the top, doubled where a zone changes so they can be
#   counted.  The hue never touches the ring bodies; if it did, the
#   shaft would turn into a rainbow.
#
# ======================================================================
APX, APY = 556.0, 228.0             # the aperture: vanishing point and sun
X0, Y0 = 596.0, 980.0               # the nearest deck, mostly below frame
W0, RY0, T0 = 980.0, 215.0, 52.0    # its half-width, flatness, thickness
P, NLEV = 0.845, 18                 # each level = the last one x P
ART_BOT = 900.0

INK = {
    "void":   "#07060C",            # the black the press can actually hold
    "wall_lo": "#0B0F1E",
    "wall_hi": "#26304E",
    "mech":   "#C24D00",            # zone 1 lip: sodium light on the decks
    "agri":   "#3A7A34",            # zone 2 lip: grow lamps, held back
    "admin":  "#7FB4F0",            # zone 3 lip: cold glass at the top
    "lamp":   "#FFF0B4",            # the elite's windows
    "sun":    "#FFFDF0",
    "halo":   "#FFD36B",
    "hills":  "#D8F0C0",            # the green in the photographs
    "sky":    "#9FD8FF",
    "hat":    "#FFCE1E",
    "skin":   "#FFB07A",
    "denim":  "#2F5DB0",
    "rope":   "#FFE27A",
    "cyan":   "#35C6E8",
}


def level(k):
    """Deck k, counting away from the viewer: centre, radii, thickness."""
    s = P ** k
    return (APX + (X0 - APX) * s, APY + (Y0 - APY) * s,
            W0 * s, RY0 * s, T0 * s)


def _check_geometry():
    """The three things that must hold for the barrel to read, asserted
    because each would fail invisibly in the code and obviously on the
    print: every level is smaller than the one below it; every level
    sits inside the walls of the one below it, so the shaft never
    flares; and every level's lit lip clears the top of the level below,
    so painting far-to-near occludes deck bottoms and never a lip."""
    for k in range(NLEV - 1):
        cx, cy, rx, ry, t = level(k)
        nx, ny, nrx, nry, nt = level(k + 1)
        assert nrx < rx and nry < ry and nt < t, k
        assert nx - nrx > cx - rx and nx + nrx < cx + rx, k
        assert ny - nry < cy - ry, k                    # lip stays visible
        assert ny < cy, k


def _ell(size, cx, cy, rx, ry):
    return mask_of(size, [], [(cx - rx, cy - ry, cx + rx, cy + ry)])


def _crescent(size, cx, cy, rx, ry, drop):
    """The underside of a deck: the ring shifted down, minus the ring.
    Built as the difference of two FILLS -- a stroked ellipse would
    straddle its own radius and leak outside the level above."""
    low = _ell(size, cx, cy + drop, rx, ry)
    high = _ell(size, cx, cy, rx, ry)
    return Image.composite(low, Image.new("L", size, 0),
                           high.point(lambda v: 255 - v))


def zone_lip(k):
    """Lip colour, and whether this level starts a new zone."""
    if k < 6:
        return rgb(INK["mech"]), k == 5
    if k < 12:
        return rgb(INK["agri"]), k == 11
    return rgb(INK["admin"]), k == 17


def draw_shaft(img, rnd):
    size = img.size
    _check_geometry()

    # --- the near wall we are standing against.  Without it the corners
    # outside the barrel are empty black and the whole thing reads as a
    # cone seen from outside rather than a shaft seen from inside.
    img.paste(wash(size, [(0.0, rgb("#12182C")), (0.45, rgb("#0A0E1C")),
                          (1.0, rgb(INK["void"]))]))
    d0 = ImageDraw.Draw(img)
    for i in range(46):
        rx = rnd.choice([28, 62, 96, 904, 938, 972])
        ry = 40 + rnd.random() * 820
        r = 4.0
        d0.ellipse((px(rx - r), px(ry - r), px(rx + r), px(ry + r)),
                   fill=rgb("#161E36"))

    # --- the barrel: two straight walls to the vanishing point, washed
    # from near-black at the feet to the light spilling down from above
    lx = APX + (X0 - W0 - APX) / (Y0 - APY) * (ART_BOT - APY) - APX
    wedge = mask_of(size, [[(APX, APY),
                            (APX + lx, ART_BOT), (APX - lx, ART_BOT)]])
    grad = wash(size, [(0.0, rgb(INK["wall_hi"])), (0.18, rgb("#171F38")),
                       (0.62, rgb(INK["wall_lo"])), (1.0, rgb("#090D1A"))])
    paint(img, wedge, grad)

    # --- ribs: pipe runs and ladder rails converging on the aperture.
    # They must die out before their gaps fall under a pixel, or the
    # top of the shaft turns into moire.
    for i in range(15):
        # by angle around the barrel, not evenly across the picture:
        # spaced in x, a cylinder reads as a flat cone with lines on it
        f = math.sin(-1.30 + 2.60 * i / 14.0)
        cx, cy, rx, _ry, _t = level(0)
        x = cx + f * rx
        fade = 1.0 - abs(f) * 0.35
        for kk in range(9):
            a, b = level(kk), level(kk + 1)
            xa = a[0] + f * a[2]
            xb = b[0] + f * b[2]
            w = max(1.0, 9.0 * (P ** kk))
            al = max(0.0, (1.0 - kk / 9.0)) * fade
            if al <= 0.02:
                break
            m = mask_of(size, [[(xa - w, a[1]), (xa + w, a[1]),
                                (xb + w * P, b[1]), (xb - w * P, b[1])]])
            m = m.point(lambda v, al=al: int(v * 0.5 * al))
            paint(img, m, rgb("#1B2440"))
        del x

    # --- the decks, far first so the near ones overlap them
    for k in range(NLEV - 1, -1, -1):
        cx, cy, rx, ry, t = level(k)
        band = _crescent(size, cx, cy, rx, ry, t)
        paint(img, band, rgb("#0D1222") if k > 5 else rgb("#12172A"))
        lip, boundary = zone_lip(k)
        lw = max(1.4, 7.0 * (P ** k))
        paint(img, _crescent(size, cx, cy, rx, ry, lw), lip)
        if boundary:                      # a doubled lip: the zone changes
            paint(img, _crescent(size, cx, cy + t * 1.9, rx, ry, lw), lip)
        if k >= NLEV - 4:                 # the elite, with their lights on
            d = ImageDraw.Draw(img)
            for j in range(9):
                u = math.sin(-1.20 + 2.40 * j / 8.0) * 0.92
                wx = cx + u * rx
                wy = cy + ry * math.sqrt(max(0.0, 1 - u * u)) + t * 0.45
                r = max(1.0, 3.0 * (P ** (k - NLEV + 4)))
                d.ellipse((px(wx - r), px(wy - r), px(wx + r), px(wy + r)),
                          fill=rgb(INK["lamp"]))
    return img


def draw_aperture(img):
    """Daylight, and inside it -- three shapes and no more -- what was
    in the box of photographs."""
    size = img.size
    r = 50.0
    glow(img, APX, APY, 430, rgb(INK["halo"]), 0.46)
    glow(img, APX, APY, 190, rgb("#FFF2C4"), 0.72)
    d = ImageDraw.Draw(img)
    d.ellipse((px(APX - r), px(APY - r), px(APX + r), px(APY + r)),
              fill=rgb(INK["sun"]))
    # the photograph: a chord of sky, a low arc of hills, nothing else
    core = _ell(size, APX, APY, r, r)
    # sky, then a real horizon -- three overlapping hills and a small
    # hot sun, not three flat bands.  This is the photograph in the box.
    paint(img, Image.composite(
        _ell(size, APX, APY - r * 0.5, r * 1.2, r * 0.7),
        Image.new("L", size, 0), core), rgb(INK["sky"]))
    d0 = ImageDraw.Draw(img)
    sun = mask_of(size, [], [(APX + r * 0.34, APY - r * 0.46,
                              APX + r * 0.62, APY - r * 0.18)])
    paint(img, Image.composite(sun, Image.new("L", size, 0), core),
          rgb("#FFF6C8"))
    for cx, cy, hx, hy, col in (
            (APX - r * 0.55, APY + r * 0.62, r * 0.80, r * 0.44, "#3F8C33"),
            (APX + r * 0.60, APY + r * 0.66, r * 0.78, r * 0.40, "#347A2B"),
            (APX + r * 0.02, APY + r * 0.86, r * 1.05, r * 0.52, "#62B04A")):
        h = _ell(size, cx, cy, hx, hy)
        paint(img, Image.composite(h, Image.new("L", size, 0), core), rgb(col))
    # concrete rim, not a green ring: a green one reads as a badge
    ring = Image.composite(_ell(size, APX, APY, r * 1.16, r * 1.16),
                           Image.new("L", size, 0),
                           _ell(size, APX, APY, r * 1.0, r * 1.0)
                           .point(lambda v: 255 - v))
    paint(img, ring, rgb("#7A7360"))
    # and the bite: the topmost deck must genuinely CROSS the opening,
    # not sit tangent to it -- tangent gives neither a clean disc nor a
    # bitten one, and the aperture reads as a floating badge
    cx, cy, rx, ry, t = level(NLEV - 1)
    cy -= r * 0.52
    paint(img, _crescent(size, cx, cy, rx, ry, t), rgb("#0D1222"))
    lip, _b = zone_lip(NLEV - 1)
    paint(img, _crescent(size, cx, cy, rx, ry, 1.6), lip)
    return img


# ----------------------------------------------------------------------
# the mechanic
#
# Backlit, seen from behind and three-quarters, leaning back to look up
# the shaft with the whip arm raised.  Two deliberate decisions:
#
#   * no face.  He is a silhouette against the only light in the
#     picture, which is both the honest way to light him and the reason
#     this does not end up as a badly drawn portrait at 10 cm.
#   * every limb is a four-point polygon between two joints with two
#     different widths, so it TAPERS.  A stroked line with round caps
#     is what the obvious code would give, and it produces a balloon
#     man every time.
# ----------------------------------------------------------------------
def limb(p0, p1, w0, w1):
    """A tapered segment: two joints, two widths, four points."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = -dy / n, dx / n
    return [(p0[0] + ux * w0 / 2, p0[1] + uy * w0 / 2),
            (p1[0] + ux * w1 / 2, p1[1] + uy * w1 / 2),
            (p1[0] - ux * w1 / 2, p1[1] - uy * w1 / 2),
            (p0[0] - ux * w0 / 2, p0[1] - uy * w0 / 2)]


def bezier(p0, p1, p2, p3, n=48):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0]
                    + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1]
                    + t**3 * p3[1]))
    return out


def taper_path(pts, w0, w1):
    """A polyline with a width that falls off along its length."""
    left, right = [], []
    for i, p in enumerate(pts):
        t = i / max(1, len(pts) - 1)
        w = w0 + (w1 - w0) * t
        q = pts[min(i + 1, len(pts) - 1)] if i < len(pts) - 1 else pts[i - 1]
        dx, dy = (q[0] - p[0], q[1] - p[1]) if i < len(pts) - 1 else \
                 (p[0] - q[0], p[1] - q[1])
        n = math.hypot(dx, dy) or 1.0
        ux, uy = -dy / n, dx / n
        left.append((p[0] + ux * w / 2, p[1] + uy * w / 2))
        right.append((p[0] - ux * w / 2, p[1] - uy * w / 2))
    return left + right[::-1]


# joints, in face units.  Feet on the hazard rule at y 876.  The stance
# is braced and wide, the shoulders are tilted (whip shoulder up, other
# dropped), and the skull sits back of the neck with the hat brim tipped
# up -- those three things are what make him read as LOOKING UP rather
# than standing to attention.
HERO = {
    "back_thigh": (limb((280, 650), (250, 762), 55, 42)),
    "back_calf": (limb((250, 762), (232, 858), 42, 27)),
    "front_thigh": (limb((322, 652), (342, 764), 59, 44)),
    "front_calf": (limb((342, 764), (352, 862), 44, 29)),
    "torso": [(266, 658), (350, 652), (358, 492), (230, 516)],
    "whip_upper": (limb((350, 500), (412, 436), 38, 29)),
    "whip_fore": (limb((412, 436), (448, 352), 29, 20)),
    "back_upper": (limb((234, 522), (198, 592), 34, 26)),
    "back_fore": (limb((198, 592), (210, 662), 26, 17)),
    "neck": [(276, 508), (308, 502), (304, 474), (278, 480)],
    "brim": [(258, 466), (322, 446), (325, 459), (261, 479)],
}
BOOTS = [[(202, 846), (264, 842), (268, 878), (200, 880)],
         [(326, 850), (394, 848), (398, 882), (324, 882)]]
HEAD = (288, 454, 26, 30)           # cx, cy, rx, ry -- tipped back
HAT_DOME = (290, 436, 23, 15)   # sits ON the skull, not instead of it
# a lash, not a fishing line: it leaves the hand thick, whips over and
# cracks back down -- the S is what separates the two readings
WHIP = bezier((448, 352), (556, 244), (664, 434), (806, 318))


# He is deliberately SMALL.  A hand-built figure large enough to be
# "painted" cannot survive close inspection when every limb is a
# polygon; the same figure at a fifth of the height reads instantly and
# does the job the cover actually needs -- it puts a human being at the
# bottom of a mile of shaft, which is the whole premise.
HS, HX, HFEET = 0.50, 208.0, 874.0      # scale, x of his centre, ground line


def _t(p):
    """Place the authored figure: scaled about his boots, moved left so
    the barrel keeps the middle of the cover."""
    return (HX + (p[0] - 300.0) * HS, HFEET - (876.0 - p[1]) * HS)


def _tp(poly):
    return [_t(q) for q in poly]


def _te(cx, cy, rx, ry):
    a, b = _t((cx - rx, cy - ry)), _t((cx + rx, cy + ry))
    return (a[0], a[1], b[0], b[1])


def hero_mask(size, with_whip=True):
    polys = [_tp(v) for v in list(HERO.values()) + BOOTS]
    if with_whip:
        polys = polys + [_tp(taper_path(WHIP, 9, 1.6))]
    return mask_of(size, polys, [_te(*HEAD), _te(*HAT_DOME)])


def silhouette_gate(path="build/hero_gate.png"):
    """The direction's own gate: flat black on white, small.  If it does
    not read 'man, leaning back, arm up' with no shading at all, the
    drawing is wrong and no amount of lighting will rescue it."""
    img = Image.new("RGB", (px(1000), px(900)), (255, 255, 255))
    paint(img, hero_mask(img.size), (0, 0, 0))
    img.resize((190, 171), Image.LANCZOS).save(path)
    print(path)


def draw_hero(img):
    size = img.size
    body = hero_mask(size, with_whip=False)
    whip = mask_of(size, [_tp(taper_path(WHIP, 9, 1.6))])

    # the wall behind him has to be lighter than he is or a silhouette
    # is not a silhouette -- this is the pool of light he is standing in
    glow(img, 262, 690, 360, rgb("#4A5C93"), 0.85, aspect=1.15)
    drop(img, body, 7, 5, (0, 0, 0), blur=7, opacity=0.55)

    # the body: near-black, a little open to the light at the shoulders
    # dark, but never as dark as the deepest shadow behind him -- a
    # silhouette has to be a MASS against the light, and a mass that
    # matches its background is just an outline
    paint(img, body, rgb("#0A0D18"))        # flat: a true silhouette
    # the hat is the one saturated note on him, and the brightest thing
    # in the picture after the daylight
    hat = mask_of(size, [_tp(HERO["brim"])], [_te(*HAT_DOME)])
    paint(img, hat, rgb("#C89312"))
    # rim: the light is up and to the right, so that is the only edge lit.
    # Computed on the UNION, never per limb, or crescents appear across
    # the chest where the arm crosses it.
    rim(img, body, 1, -1, rgb("#FFD07A"), 4.0)     # 0.4mm: survives a press
    rim(img, hat, 1, -1, rgb("#FFE9A0"), 3.0)

    # the whip, and the crack at the end of it
    paint(img, whip, rgb(INK["rope"]))
    rim(img, whip, 1, -1, rgb("#FFF6C0"), 1.6)
    tip = _t(WHIP[-1])
    glow(img, tip[0], tip[1], 34, rgb("#FFF0B0"), 0.45)
    d = ImageDraw.Draw(img)
    for a in range(6):
        th = a * math.pi / 3 + 0.35
        r0, r1 = 7, 21
        d.line((px(tip[0] + math.cos(th) * r0), px(tip[1] + math.sin(th) * r0),
                px(tip[0] + math.cos(th) * r1), px(tip[1] + math.sin(th) * r1)),
               fill=rgb("#FFF6C0"), width=px(1.8))
    return img


# ======================================================================
#   SLEEVE FURNITURE
# ======================================================================
def cut_rect(size, inset, cut):
    """The trim shape: a rectangle with 45-degree cut corners.  1987
    clipped its corners; it did not round them."""
    w, h = UNITS[0], UNITS[1]
    a, b = inset, cut
    return mask_of(size, [[(a + b, a), (w - a - b, a), (w - a, a + b),
                           (w - a, h - a - b), (w - a - b, h - a),
                           (a + b, h - a), (a, h - a - b), (a, a + b)]])


def hazard(img, y, h, pitch=22.0, lo=0.0, hi=1000.0):
    """The game's own hazard tile, run as a rule: 45-degree yellow on
    black.  It closes the foot of the picture and doubles as the deck
    the mechanic is standing on."""
    d = ImageDraw.Draw(img)
    d.rectangle((px(lo), px(y), px(hi), px(y + h)), fill=rgb("#131018"))
    x = lo - h
    while x < hi + h:
        d.polygon([(px(x), px(y + h)), (px(x + pitch * 0.5), px(y + h)),
                   (px(x + pitch * 0.5 + h), px(y)), (px(x + h), px(y))],
                  fill=rgb("#F0B41A"))
        x += pitch
    d.rectangle((px(lo), px(y), px(hi), px(y + 2)), fill=rgb("#0A0A10"))
    d.rectangle((px(lo), px(y + h - 2), px(hi), px(y + h)), fill=rgb("#0A0A10"))


def publisher(img, y=0.0, h=92.0):
    """A press-black masthead: the name reversed out, the 8 in a second
    ink, SOFTWARE ranged right under a cyan rule."""
    d = ImageDraw.Draw(img)
    d.rectangle((0, px(y), px(UNITS[0]), px(y + h)), fill=rgb("#0A0A11"))
    d.rectangle((0, px(y + h), px(UNITS[0]), px(y + h + 5)), fill=rgb(INK["cyan"]))
    w = logotype(img, "REVIVE", 40, y + 26, 44, gap=1.0, face=rgb(INK["cyan"]),
                 lit=None, dark=None, outline=None)
    w2 = logotype(img, "8", 40 + w + 5, y + 26, 44, gap=1.0,
                  face=rgb("#FFB020"), outline=None)
    logotype(img, "BIT", 40 + w + w2 + 10, y + 26, 44, gap=1.0,
             face=rgb(INK["cyan"]), outline=None)
    text_at(d, "SOFTWARE", 962, y + 36, font("bold", 22), rgb("#8FA2C0"),
            anchor="ra", spacing=7)


def flash(img, lines, cx, cy, w, h, angle=-8.0):
    """The angled sticker that told you which machine it was for."""
    tile = Image.new("RGBA", (px(w) + 8, px(h) + 8), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.polygon([(px(6), 4), (px(w) - 4, 4), (px(w) + 4, px(h) - px(6)),
                (px(w) - px(6), px(h) + 4), (4, px(h) + 4), (4, px(6))],
               fill=rgb("#F0B41A"), outline=rgb("#8C1218"), width=px(3))
    td.text((tile.size[0] / 2, px(h * 0.30)), lines[0], font=font("bold", 26),
            fill=rgb("#8C1218"), anchor="mm")
    td.text((tile.size[0] / 2, px(h * 0.66)), lines[1], font=font("narrow", 21),
            fill=rgb("#3A0A0C"), anchor="mm")
    tile = tile.rotate(angle, resample=Image.BICUBIC, expand=True)
    img.paste(tile, (px(cx) - tile.size[0] // 2, px(cy) - tile.size[1] // 2), tile)


def trim(img):
    """Nothing to do but hand the face back.  There WAS a hairline
    keyline inset 0.8mm from the trim here; a guillotine drifts half a
    millimetre on a short run, which would shave it off one edge and
    leave it doubled on the other.  The art bleeds instead -- which is
    what inlays did from 1986 anyway."""
    return img


def marks(img, bleed_mm, folds):
    """Crop marks at the four trim corners and fold marks at the two
    spine creases, drawn in the bleed where they get cut away."""
    b = int(round(bleed_mm * MM))
    d = ImageDraw.Draw(img)
    w, h = img.size
    t = max(1, int(MM * 0.15))
    ln = int(MM * 2.2)
    for x in (b, w - b):
        for y in (b, h - b):
            d.line((x, y - b, x, y - b + ln if y == b else y - b), fill=(120, 120, 120), width=t)
    for x in (b, w - b):
        d.line((x, 0, x, b - int(MM * 0.8)), fill=(120, 120, 120), width=t)
        d.line((x, h - b + int(MM * 0.8), x, h), fill=(120, 120, 120), width=t)
    for y in (b, h - b):
        d.line((0, y, b - int(MM * 0.8), y), fill=(120, 120, 120), width=t)
        d.line((w - b + int(MM * 0.8), y, w, y), fill=(120, 120, 120), width=t)
    for fx in folds:                       # the two spine creases
        x = b + fx
        d.line((x, 0, x, b - int(MM * 0.8)), fill=(200, 120, 40), width=t)
        d.line((x, h - b + int(MM * 0.8), x, h), fill=(200, 120, 40), width=t)
    return img


def add_bleed(img, mm=3.0):
    """Extend the ground past the trim by replicating the edge pixels,
    so a cut that wanders still lands on artwork and never on paper."""
    b = int(round(mm * MM))
    w, h = img.size
    out = Image.new("RGB", (w + 2 * b, h + 2 * b))
    out.paste(img, (b, b))
    out.paste(img.crop((0, 0, 1, h)).resize((b, h)), (0, b))
    out.paste(img.crop((w - 1, 0, w, h)).resize((b, h)), (w + b, b))
    out.paste(out.crop((0, b, w + 2 * b, b + 1)).resize((w + 2 * b, b)), (0, 0))
    out.paste(out.crop((0, h + b - 1, w + 2 * b, h + b)).resize((w + 2 * b, b)),
              (0, h + b))
    return out


# ======================================================================
def front_face(rnd):
    img, d = panel(*UNITS, rgb(INK["void"]))
    art = Image.new("RGB", (px(UNITS[0]), px(ART_BOT)), rgb(INK["void"]))
    draw_shaft(art, rnd)
    draw_aperture(art)
    draw_hero(art)
    img.paste(art, (0, 0))

    text_at(d, "THE TRUTH IS ABOVE", 500, 824, font("bold", 30),
            rgb(INK["cyan"]), anchor="ma", spacing=10)
    hazard(img, ART_BOT - 26, 26)
    publisher(img)

    cap = 148.0                       # fit FIRST, then centre the result
    while word_width("THE SHAFT", cap, 1.1) > 850 and cap > 4:
        cap *= 0.98
    w = word_width("THE SHAFT", cap, 1.1)
    logotype(img, "THE SHAFT", (UNITS[0] - w) / 2, 938, cap, fit=850,
             face=rgb(INK["hat"]), lit=rgb("#FFF6C0"), dark=rgb("#A34A00"),
             outline=rgb("#12060A"), shadow=rgb("#4A0A0C"), sh=(0.085, 0.10))
    text_at(d, "59 SCREENS TO THE SURFACE", 500, 1120, font("narrow", 27),
            rgb("#E8B830"), anchor="ma", spacing=6)
    text_at(d, "PROGRAMMED BY VASPER", 40, 1194, font("narrow", 23),
            rgb("#AFBECC"))
    text_at(d, "REVIVE8BIT SOFTWARE  ·  2026", 960, 1194, font("narrow", 23),
            rgb("#AFBECC"), anchor="ra")
    # the flash lands ON the picture, the way a sticker did
    flash(img, ["AMSTRAD CPC 6128", "128K  ·  3 INCH DISC"],
          768, 782, 356, 86, angle=-7)
    return trim(img)


def back_face(rnd):
    img, d = panel(*UNITS, rgb("#0A0D18"))
    img.paste(wash(img.size, [(0.0, rgb("#141C34")), (0.5, rgb("#0A0D18")),
                              (1.0, rgb("#07060C"))]))
    publisher(img)

    text_at(d, "THEY TOLD YOU THE SKY WOULD KILL YOU.", 40, 132,
            font("bold", 36), rgb("#F0B41A"))
    body = [
        "Fifty generations have been born and buried inside the Shaft.  At the bottom",
        "you keep the great pumps turning for people who have never seen daylight.",
        "Then you found the photographs — green hills, a burning sun, and an",
        "inspector's log signed on the surface.  There is one way to the truth: UP.",
    ]
    for i, ln in enumerate(body):
        text_at(d, ln, 40, 200 + i * 31, font("reg", 22), rgb("#C9D3E6"))

    y = 342
    for i, name in enumerate(("shot_level01", "shot_level30", "shot_level45")):
        # 640 halves EXACTLY to 320: any other reduction beats the Mode 0
        # chunky pixels into a mix of 2- and 3-pixel columns
        im, w, h = shot(f"docs/{name}.png", 300, border=rgb("#39445E"), frame=6)
        img.paste(im, (px(28 + i * 316), px(y)))
    text_at(d, "ACTUAL AMSTRAD CPC 6128 SCREENS", 500, y + 210,
            font("narrow", 22), rgb("#AFBECC"), anchor="ma", spacing=3)

    y = 600
    feats = [
        ("59 SCREENS", "of flip-screen action — machine decks, agricultural"),
        ("", "terraces and the administrative tiers of the elite"),
        ("CABLE WHIP,", "duck, slide and ballistic jumps with air control"),
        ("", "and ladder grabs in mid-fall"),
        ("FIVE KEYCARDS,", "armoured doors and sealed vaults opened by"),
        ("", "switches thrown on entirely different floors"),
        ("SEVEN SIDE-SHOWS", "behind the marked doorways — and each one"),
        ("", "of them is playable once, and once only"),
        ("100% MACHINE CODE", "with AY music, compiled sprites and"),
        ("", "hardware double buffering throughout"),
    ]
    for i, (lead, rest) in enumerate(feats):
        yy = y + i * 26
        x = 40
        if lead:
            d.rectangle((px(22), px(yy + 7), px(32), px(yy + 17)),
                        fill=rgb("#F0B41A"))
            text_at(d, lead, x, yy, font("bold", 20), rgb("#DCE4F2"))
            x += d.textlength(lead, font=font("bold", 20)) / WORK + 7
        text_at(d, rest, x, yy + 1, font("reg", 18), rgb("#8FA2C0"))

    y = 886
    text_at(d, "CURSORS — MOVE AND CLIMB   ·   SPACE — JUMP   ·   Z — WHIP"
            "   ·   DOWN — DUCK AND SLIDE   ·   JOYSTICK",
            500, y, font("narrow", 21), rgb("#AFBECC"), anchor="ma")

    y = 930
    d.rectangle((px(22), px(y), px(978), px(y + 48)), fill=rgb("#11182C"))
    text_at(d, "LOADING:  insert the disc, type  RUN\"SHAFT  and press ENTER",
            500, y + 13, font("bold", 21), rgb("#DCE4F2"), anchor="ma")

    y = 1010
    d.rectangle((px(22), px(y), px(978), px(y + 88)), outline=rgb("#F0B41A"),
                width=px(3))
    text_at(d, "COPYING IS PERMITTED — AND ENCOURAGED", 500, y + 16,
            font("bold", 25), rgb("#F0B41A"), anchor="ma", spacing=1)
    text_at(d, "Lend it.  Copy it.  Upload it.  Software survives only "
            "because people did.", 500, y + 54, font("reg", 18),
            rgb("#9FB0C8"), anchor="ma")

    text_at(d, "THE SHAFT  ·  PROGRAMMED BY VASPER  ·  REVIVE8BIT SOFTWARE 2026",
            500, 1150, font("narrow", 21), rgb("#9FB0C8"), anchor="ma",
            spacing=2)
    text_at(d, "MADE IN 2026 FOR A MACHINE FROM 1985", 500, 1184,
            font("narrow", 20), rgb("#8090A8"), anchor="ma", spacing=2)
    return trim(img)


def spine_face():
    """10 mm of chrome yellow -- the one thing you see on a shelf."""
    strip = Image.new("RGB", (px(UNITS[1]), px(100)), rgb("#F0B41A"))
    d = ImageDraw.Draw(strip)
    d.rectangle((0, 0, strip.size[0], px(4)), fill=rgb("#8C1218"))
    d.rectangle((0, px(96), strip.size[0], px(100)), fill=rgb("#8C1218"))
    d.text((px(80), px(50)), "THE SHAFT", font=font("bold", 54),
           fill=rgb("#12060A"), anchor="lm")
    d.text((px(UNITS[1] - 80), px(50)), "REVIVE8BIT  ·  CPC 6128  ·  DISC",
           font=font("narrow", 30), fill=rgb("#8C1218"), anchor="rm")
    return strip.rotate(-90, expand=True)


def disc_label():
    """The label for a 3-inch CF-2 disc is a RECTANGLE stuck on the face
    of the plastic shell -- about 70 x 45 mm, no centre hole.  (A round
    label with a hub punched out belongs to 5.25-inch media and would
    be the wrong object entirely.)  It carries the one thing every
    8-bit label had to carry: what you type at the keyboard."""
    W, H = 700.0, 450.0
    img = Image.new("RGB", (px(W), px(H)), rgb("#0E1426"))
    img.paste(wash(img.size, [(0.0, rgb("#18203A")), (1.0, rgb("#0A0D18"))]))
    d = ImageDraw.Draw(img)
    d.rectangle((px(6), px(6), px(W - 6), px(H - 6)), outline=rgb("#F0B41A"),
                width=px(5))
    d.rectangle((px(6), px(6), px(W - 6), px(64)), fill=rgb("#0A0A11"))
    text_at(d, "REVIVE8BIT SOFTWARE", 40, 22, font("bold", 26),
            rgb(INK["cyan"]), spacing=3)
    text_at(d, "SIDE 1", W - 40, 22, font("bold", 26), rgb("#F0B41A"),
            anchor="ra", spacing=3)

    cap = 62.0
    while word_width("THE SHAFT", cap, 1.1) > 560 and cap > 4:
        cap *= 0.98
    w = word_width("THE SHAFT", cap, 1.1)
    logotype(img, "THE SHAFT", (W - w) / 2, 96, cap, fit=560,
             face=rgb(INK["hat"]), outline=rgb("#12060A"), ow=0.05,
             shadow=rgb("#8C1218"), sh=(0.07, 0.08))

    text_at(d, "AMSTRAD CPC 6128  ·  128K  ·  3 INCH DISC", W / 2, 208,
            font("narrow", 24), rgb("#8FA2C0"), anchor="ma", spacing=2)

    d.rectangle((px(70), px(256), px(W - 70), px(322)), fill=rgb("#F0B41A"))
    text_at(d, "RUN\"SHAFT", W / 2, 268, font("bold", 40), rgb("#12060A"),
            anchor="ma", spacing=2)

    text_at(d, "59 SCREENS TO THE SURFACE", W / 2, 352, font("narrow", 22),
            rgb("#C9D3E6"), anchor="ma", spacing=3)
    text_at(d, "PROGRAMMED BY VASPER  ·  2026", W / 2, 392,
            font("narrow", 21), rgb("#9FB0C8"), anchor="ma", spacing=2)
    return img


# ======================================================================
#   THE PRESS PASS
#
#   The likeliest way for a sleeve like this to fail is not a wrong
#   vanishing point, it is being too clean: hard vector edges, pure
#   black, pure white, perfect registration.  None of those existed on
#   a 1987 inlay.  So: the warm plate goes very slightly out of
#   register (over the PICTURE only -- misregistered type reads as a
#   fault, not as period), a little press noise goes everywhere, and
#   the ink is clamped off both ends of the scale afterwards, because
#   paper cannot hold #000 and no press lays down #FFF.
# ======================================================================
def misregister(img, box, dx=2, dy=1):
    """Slip the warm plate.  Red channel only, picture only, and only
    by a pixel or so: the deck lips are two pixels wide and a bigger
    offset tears them into colour fringes instead of reading as a press
    that was very slightly out."""
    region = img.crop(box)
    r, g, b = region.split()
    off = Image.new("L", region.size, 0)
    off.paste(r, (dx, dy))
    img.paste(Image.merge("RGB", (off, g, b)), box)
    return img


def press(img, seed=1, screen=True):
    """The surface pass, at PRINT resolution.  A halftone screen is what
    a 1987 inlay actually was; the red-channel slip that used to live
    here produced a red edge on one side and a cyan edge on the other,
    which is digital chromatic aberration -- a 2010s artefact, not a
    press going out of register."""
    if screen:
        # 5 print pixels at 300 dpi is a 0.42mm rosette, about 60 lines
        # per inch -- coarse enough to see in the hand, fine enough that
        # it reads as a press and not as newsprint
        img = halftone(img, pitch=5.0, angle=15.0, strength=0.15)
    return fine_grain(img, 3, seed)


def fine_grain(img, amount=3, seed=1):
    """Press noise at 1:1 -- no upsampling, so it is grain and not the
    0.5mm worm-like mottle a bicubic-blown random field gives."""
    import random
    r = random.Random(seed)
    w, h = img.size
    n = Image.new("L", (w, h))
    n.putdata([128 + r.randint(-amount * 8, amount * 8) for _ in range(w * h)])
    out = img.copy()
    out.paste(Image.new("RGB", img.size, (255, 255, 255)), (0, 0),
              n.point(lambda v: max(0, v - 128)))
    out.paste(Image.new("RGB", img.size, (0, 0, 0)), (0, 0),
              n.point(lambda v: max(0, 128 - v)))
    return out


def clamp_ink(img):
    """Paper cannot hold #000 and no press lays down #FFF.  This has to
    run AFTER the resample down to print size: LANCZOS rings at hard
    edges and will push pixels back past both ends if it runs last."""
    lo, hi = rgb("#14161F"), rgb("#FFF6D2")   # ~250% TAC, printable
    lut = []
    for ch in range(3):
        lut += [int(round(lo[ch] + (hi[ch] - lo[ch]) * (v / 255.0)))
                for v in range(256)]
    return img.point(lut)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--gate" in sys.argv:
        return silhouette_gate()
    import random
    os.makedirs("docs", exist_ok=True)

    front = to_print(front_face(random.Random(11)), FRONT_MM)
    back = to_print(back_face(random.Random(12)), FRONT_MM)
    sp = to_print(spine_face(), SPINE_MM)

    wrap = clamp_ink(press(inlay(back, sp, front), seed=11))
    wrap.save("docs/cover_front.png", dpi=(DPI, DPI))   # placeholder, replaced
    fw = front.size[0]
    wrap.crop((wrap.size[0] - fw, 0, wrap.size[0], wrap.size[1])).save(
        "docs/cover_front.png", dpi=(DPI, DPI))
    wrap.crop((0, 0, fw, wrap.size[1])).save("docs/cover_back.png",
                                             dpi=(DPI, DPI))
    print(f"docs/cover_front.png  {fw}x{wrap.size[1]} px  "
          f"{FRONT_MM:.0f}x{INLAY_MM[1]:.0f}mm @ {DPI}dpi")
    print(f"docs/cover_back.png   {fw}x{wrap.size[1]} px  "
          f"{FRONT_MM:.0f}x{INLAY_MM[1]:.0f}mm @ {DPI}dpi")
    wrap.save("docs/cover_inlay.png", dpi=(DPI, DPI))
    print(f"docs/cover_inlay.png  {wrap.size[0]}x{wrap.size[1]} px  "
          f"{INLAY_MM[0]:.0f}x{INLAY_MM[1]:.0f}mm @ {DPI}dpi (trim size)")

    bled = marks(add_bleed(wrap, 3.0), 3.0,
                 [back.size[0], back.size[0] + sp.size[0]])
    bled.save("docs/cover_inlay_bleed.png", dpi=(DPI, DPI))
    print(f"docs/cover_inlay_bleed.png  {bled.size[0]}x{bled.size[1]} px  "
          f"{INLAY_MM[0] + 6:.0f}x{INLAY_MM[1] + 6:.0f}mm @ {DPI}dpi "
          "(3mm bleed all round)")

    lab = clamp_ink(press(to_print(disc_label(), LABEL_MM), seed=13))
    lab.save("docs/disc_label.png", dpi=(DPI, DPI))
    add_bleed(lab, 3.0).save("docs/disc_label_bleed.png", dpi=(DPI, DPI))
    print(f"docs/disc_label.png  {lab.size[0]}x{lab.size[1]} px  "
          f"{LABEL_MM:.0f}x45mm @ {DPI}dpi  (+ _bleed at 76x51mm)")


if __name__ == "__main__":
    main()
