# THE SHAFT

A 2D flip-screen vertical platformer for the **Amstrad CPC 6128**, written in Z80
assembly. Climb from the grease-black machine decks at the bottom of a miles-deep
concrete cylinder to the sealed airlock at its top — and find out what the elite
have been lying about.

## Premise

Humanity has lived for generations inside the Shaft. The surface is taught to be
toxic and lethal. Mechanics keep the great pumps alive at the bottom; the
agricultural terraces feed everyone from the middle levels; the elite rule from
the light-flooded top. You are a bottom-level mechanic who has found a forbidden
artifact proving the official history is a lie. There is only one way to the
truth: **up**.

## Building

Requires [rasm](https://github.com/EdouardBERGE/rasm) and
[iDSK](https://github.com/cpcsdk/idsk) on your PATH (override with the `RASM` /
`IDSK` environment variables).

```sh
./build.sh          # -> build/theshaft.dsk
```

Load the DSK in any CPC emulator (Caprice32, WinAPE, ACE, CPCEC, ...) as a
6128 and type `RUN"SHAFT`. That boots the BASIC loader (`src/shaft.bas`):
it draws the REVIVE8BIT title screen (`docs/revive8b.scr`, inks per
`docs/revive8b.txt`), waits for Space — or 10 seconds — then loads and
starts the game.

**Controls:** cursor keys + Space + **Z**, or joystick 0 (fire 1 = jump,
fire 2 = lasso). Walk left/right, climb ladders with up/down (mount-assist
snaps you onto the rails). Space jumps — real ballistic arcs with air
control, head bumps, mid-fall ladder grabs. **Z cracks the cable lasso**:
snares any person on your walkway within 16 pixels, in the direction you
face. **Down ducks** (debris and swings pass over your head), **Down +
direction slides** — a fast low burst, one per press, momentum enough to
carry you off an edge. Platforms have **jumpable gaps**: leap them, or
drop through as a one-floor shortcut — but a fall of more than one floor
costs a life (the landing stings, play continues where you hit).
Everyone faces where they walk: the mechanic has a two-frame leg cycle
(walking, a back-view hand-over-hand cycle on ladders, and a stride pose
mid-air), and patrollers carry shield or crowbar on their facing side.
Coat men step every 2nd frame, riot guards every 4th — you can outrun
both.

The climb is fully **bidirectional**: ladders are continuous between
screens, so climbing down off the bottom floor descends to the level
below. Levels 1, 10, 20, 30, 40 and 50 have an **elevator** on the
bottom floor — stand at the door and press up/down to ride between
stops *you have already reached on foot*.

**Health**: you carry energy (5) and lives. Any hit — enemy contact,
falling debris, red lubricant drips from the ceiling pipes (white ones
are just oily water), or a fall of more than one floor — costs one
energy point and grants 3 seconds of immunity (the sprite flickers).
At zero energy a life goes and the tank refills. **Medical crates**
appear every few levels, worth 1–4 points; spill past 5 and the
surplus banks a whole life (cap 9).

**Security doors come in five colours** (green, cyan, yellow, white,
red — tinted by each zone's palette) and their keycards are never
beside them: a door's key waits on a **lower platform, the bottom
floor — or an earlier level entirely**. Levels sow *spare* keys for
doors further up the shaft; 28 doors have no key on their own level at
all. And about **a quarter of all keys sit inside sealed vaults**:
somewhere on an *earlier* level is the wall switch that opens each one
(its state persists for the whole run), so the deepest chains run
switch → vault → door across three different levels. **Opened doors
stay open for the whole run** — every door has a persistent id in a
128-bit ledger, and revisited levels reload with their unlocked doors
already gone (keys, by contrast, respawn on revisit). Medical crates
only start appearing from level 20.

**The lasso aims from the cursor**: plain Z whips sideways as before;
hold **up** for a straight overhead snare (2 px wide, 16 tall), or
**up + a direction** for a 45° rising diagonal — all clamped at walls
and roof, all drawn as stepped rope segments. The build-time verifier threads one global key inventory through
the whole 59-level climb, so the forward route always works — and if
you miss a key, levels reload fresh when revisited (taken keys are
shown again), so with downward travel and the elevators nothing is
ever softlocked. Mechanical runs at most one local pair; Agricultural
introduces cross-level keys; Administrative chains up to three doors a
level. The HUD: key icon with five colour counts (left), level number
(centre), energy bolt, and a heart with lives (right).

**Speed**: the two per-sprite costs are hand-tuned. The masked blit
walks the sprite data with the *stack pointer* — one `POP` fetches a
mask+data pair in 10 T-states (38 T/byte vs 54, interrupts held off
per sprite). Background restore computes its map and screen pointers
once per area and steps them linearly (+20/+80 a row, +1/+4 a column)
instead of re-deriving addresses per cell — the busier the screen, the
more this matters.

## Technical design

* **Machine:** Amstrad CPC 6128 (Z80A @ 4 MHz, 64K main + 64K banked RAM)
* **Video:** Mode 0 — 160×200, 16 colours, 2 pixels per byte. Chunky and
  colourful; exactly right for an industrial palette of rust, steam and hazard
  stripes.
* **Display:** true **hardware double buffering**. Two 16K screen buffers
  (`#C000` and `#4000`); the CRTC's R12 register is reprogrammed during the
  frame flyback to swap which one is displayed. The swap is one register write —
  no copying — and because it happens while the beam is off-screen the game can
  never tear or flicker, by construction.
* **Firmware:** switched off at boot (both ROMs paged out, own IM 1 handler,
  own stack). The whole machine belongs to the game.

### Memory map (main 64K)

| Range | Contents |
|---|---|
| `#0000-#003F` | RST vectors; our IM 1 interrupt stub is copied to `#0038` |
| `#0040-#0FFF` | Stack (SP starts at `#1000`, grows down) |
| `#1000-#3FFF` | Engine code, tables, sprite data (`src/main.asm`) |
| `#4000-#7FFF` | **Screen buffer B** (16K) |
| `#8000-#A5FF` | Tile graphics, current level tilemap, actor tables (future) |
| `#A600-#BFFF` | AMSDOS work RAM during load, then unpack/scratch space |
| `#C000-#FFFF` | **Screen buffer A** (16K, visible at boot) |

### The second 64K

The 6128's extra bank stores the entire shaft: compressed level maps and
per-zone tile sets. Writing `#C4`–`#C7` to the Gate Array port (`#7Fxx`) maps
one of the four extra 16K pages over `#4000-#7FFF` **for the CPU only** — the
video hardware always reads the main 64K. So level loading works like this:
show buffer A (`#C000`), bank a page in over `#4000`, unpack the next screen's
tilemap to `#8000+`, write `#C0` to restore normal RAM, redraw. The player sees
a rock-steady screen throughout.

### Engine tour (`src/main.asm`)

| Routine | What it does |
|---|---|
| `start` | Machine takeover: Mode 0, ROMs off, IM 1 stub to `#0038`, palette, clear both buffers |
| `main_loop` | sync → flip → input → update → erase → draw, once per 1/50 s |
| `frame_sync` | Polls the CRTC VSYNC bit (PPI port B), then `HALT`s onto the VSYNC-locked Gate Array interrupt — parks the CPU at a deterministic spot inside the flyback |
| `flip_buffers` | One CRTC R12 write shows the finished buffer; swaps all per-buffer draw state |
| `read_input` / `scan_keyboard` | Full 10-row matrix scan through the PPI/AY-3-8912 handshake; merges cursors+Space with joystick 0; also computes *newly pressed* bits for jump edge-detection |
| `screen_addr` | Line-offset table lookup (generated at assembly time) + buffer base OR + X — no multiplies at runtime |
| `draw_sprite_8x16` | Masked software sprite: `screen = (screen AND mask) OR data`, unrolled 4-byte rows, classic `+#800` interleave stepping. ~4,600 T-states ≈ 6% of a frame |
| `box_overlap` | The fundamental AABB test: IX rect vs a B/C/D/E box, carry = hit. Exclusive edges, so "standing on" never reads as "stuck in" |
| `probe_level` | Walks the current rect list with `box_overlap`, ORs together the type bits (SOLID / LADDER) of everything a probe box touches |
| `find_ladder` | Climb rules: x within 1 byte of the rails (then snapped), whole body inside the ladder's climb volume — end-stops fall out of the data, no "top of ladder" special case |
| `draw_tilemap` / `draw_map_tile` | Full-screen render of the 20×25 map (~7 frames, transitions only) and the single-cell blit it is built from |
| `draw_tile` | The hot loop: 32 unrolled LDIs per 8×8 tile. Tiles sit on CRTC character rows, so every line step is a constant `+#800` — no wrap test, unlike the sprite |
| `restore_tiles` | Re-blits the ≤2×3 tile neighbourhood under the sprite's old image — replaces both the black-box erase and the ladder-repair special case |
| `fill_rect` | General rectangle fill, kept for HUD bars, wipes and debug overlays |
| `update_player` | The physics state machine: GROUND / CLIMB / AIR. 8.8 fixed-point vertical velocity (`player_yfrac`+`player_y` read as one word), gravity with terminal velocity capped below one tile so falls can't tunnel |
| `is_supported` | "Can I stand here?": a solid under the feet, or feet exactly at a ladder's through-hole (`ladder.y+16`, from the head-room convention). The raw ladder bit deliberately doesn't count — that would let you stand on air beside a ladder |
| `start_jump` / `start_fall` | Enter AIR with `JUMP_VY` or from rest; both arm the apex tracker that feeds the fall-height hook (`last_fall`) for future damage |
| `update_drones` / `check_drone_hit` | Security drones: 8-byte entity records, patrol between bounds compiled from the map, rotor frame swap every 8 frames (ticker bit 3), AABB contact check → death |
| `copy_levels_to_bank` / `load_level` | The 128K: all level blobs live in extra-RAM bank 4 (`#C4` over `#4000-#7FFF`, video unaffected); entering a level copies one blob into a writable main-RAM buffer |
| `next_level` / `enter_level` | Flip-screen climb: y<8 → next level from the bank, player re-enters at the floor keeping X (exit and entry ladders share a column, checked by the generator) |
| `check_keycards` / `check_doors` | Keycards are tiles (edited out of map RAM, re-blitted on both buffers); doors are SOLID\|DOOR rects + tiles — push with a card to delete both |
| `draw_char` / `draw_text` (+`_2x`) | 38-glyph 8×8 font, 1 bit/pixel, expanded through `pen_left` at draw time to any pen; glyphs 0–15 are the hex digits, so the HUD prints values directly |
| `psg_write` / `sfx_*` | AY-3-8912 via the PPI: jump chirp (shrinking tone period), damage noise burst with volume decay, keycard ping. Mixer bit 6 stays 0 — it's the keyboard's port direction! |

All art is authored as ASCII, one character per pixel:

- **Sprites**: `tools/sprite_gen.py` — edit the art, re-run, paste the
  masked `defb` lines.
- **Levels**: `tools/level_gen.py` — tile art plus a 25×20 character map.
  The build regenerates `src/level01.asm` from it, emitting the tileset,
  the tilemap **and the collision rects compiled from the same map** —
  solids greedy-merged into rectangles, each ladder the bounding box of
  its tile column. One source of truth: the picture and the physics can
  never disagree, and the Z80 collision code never changes.

### Why the erase code tracks two positions

With double buffering, the stale image to wipe is the one drawn into *this*
buffer **two** frames ago, not last frame's. The engine keeps one previous
(x,y) slot per buffer and `flip_buffers` toggles which slot is active.

## Roadmap

1. ~~Engine foundation~~ — video, frame sync, input, masked sprites *(done)*
2. ~~Collision~~ — AABB tests between the player and typed geometry rects;
   walls/crates/slabs block, ladders climb with mount-assist snap *(done)*
3. ~~Level drawing~~ — 8×8 tile renderer over a generated 20×25 tilemap,
   tile-restore under the sprite, collision rects compiled from the map
   *(done — flip-screen advance moves to the physics/progression pass)*
4. ~~Physics~~ — GROUND/CLIMB/AIR state machine, 8.8 fixed-point gravity and
   jump arcs, landing/head-bump snapping to tile-aligned surfaces, walk-off
   falls, mid-air ladder grabs, fall-height hook for future damage *(done)*
5. ~~Gameplay loop~~ — title menu with font renderer, flip-screen climb,
   keycards and security doors, keycard/lives HUD, AY sound effects, level
   data banked in the second 64K, lives/death/respawn/win flow *(done)*
6. ~~The full shaft~~ — **59 levels** in three zones (Mechanical 1–19,
   Agricultural 20–39, Administrative 40–59), procedurally generated by
   `tools/level_gen.py` around a fixed climb skeleton and **verified
   completable at build time** by a built-in playthrough simulator; three
   16K banks of level data loaded by the BASIC loader; zone palettes.
   Human enemies replace the drones: riot-gear guards (slow patrol),
   long-coat crowbar men (fast patrol), throwers dropping debris from the
   platform above. Player fights back: lasso, duck, slide *(done)*
7. Next: steam vents on the 300 Hz tick, fall damage using `last_fall`,
   walk animation, an ending sequence at the airlock, AY music, and a
   speed pass (compiled sprites) for the busiest screens
