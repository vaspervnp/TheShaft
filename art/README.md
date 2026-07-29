# Editing the sprites

Every frame of the game lives here as an ordinary PNG you can open in
any pixel editor:

```
art/sprites/spr_mech_r.png      the mechanic, and all 21 other frames
art/cpc-pens.gpl                the 16 CPC pens, as an editor palette
```

## The workflow

```sh
python3 tools/sprite_export.py   # sprites_art.py -> art/sprites/*.png
# ...edit the PNGs...
python3 tools/sprite_import.py   # art/sprites/*.png -> sprites_art.py
./build.sh                       # -> build/theshaft.dsk
```

The importer patches only the pixel rows of each frame — the file's
structure and the frame order (which the compiled-sprite stub table
depends on) never move. The round trip is lossless: exporting and
importing with no edits changes nothing, byte for byte.

## The rules the importer enforces

- **8×16 pixels, native.** Do not scale the images. A Mode 0 pixel is
  twice as wide as it is tall, so set your editor's **pixel aspect
  ratio to 2:1** to see the sprite as the CPC shows it
  (Aseprite/LibreSprite: *Sprite → Properties → Pixel Ratio*).
- **Transparency is the alpha channel.** Fully transparent pixels are
  the background; everything else must be opaque.
- **Only the 16 pens.** Load `art/cpc-pens.gpl` into your editor and
  paint with it. Any other colour is rejected by name, with the
  nearest pen suggested — a soft anti-aliased edge cannot slip into
  the game silently.
- Pens marked **ZONE-VARIES** in the palette (4, 6, 10, 11, 13, 14,
  15) change ink in the agricultural and administrative zones. The
  thrower's tunic uses pen 13 on purpose; the player avoids them all,
  and the importer warns if a `spr_mech_*` frame picks one up.

## What the frames are

| frames | what |
|---|---|
| `spr_mech_r/_b`, `spr_mech_l/_b` | the mechanic walking, two legs per side |
| `spr_mech_climb/_b` | on the ladder, seen from behind |
| `spr_mech_duck` | crouched (keep the top 8 rows empty — thrown rocks pass through there) |
| `spr_riot_*`, `spr_coat_*` | the two patrollers, right/left × two legs |
| `spr_throw_a/_b` | the thrower: idle, and boulder overhead |
| `spr_rock`, `spr_drip_red/white`, `spr_steam_a/b` | debris, drips, the steam plume |

`_b` frames are the second animation phase of the frame beside them;
the engine finds them at a fixed offset, so **edit both** or the walk
will limp. Left-facing frames are by convention mirrors of the
right-facing ones — the importer does not enforce it, but the game
reads better when it holds.

## An editor

**[Aseprite](https://www.aseprite.org/)** is the recommendation: it is
built for exactly this — palette-locked drawing, a 2:1 pixel ratio
setting so Mode 0 looks right while you work, onion-skinning to flip
between the `_a`/`_b` walk phases, and it reads `.gpl` palettes
directly. Paid (~$20), or free if you compile it yourself.

Free alternatives that also handle the 2:1 aspect:
**[LibreSprite](https://libresprite.github.io/)** (a free fork of an
older Aseprite) and **[GrafX2](http://grafx2.chez.com/)** (free, born
for exactly this era of wide-pixel art). GIMP works too — load the
`.gpl`, paint with the Pencil (never the Brush: it anti-aliases), and
live with the squashed view.
