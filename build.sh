#!/usr/bin/env bash
# Build The Shaft: assemble with rasm, pack into a CPC disk image with iDSK.
set -euo pipefail
cd "$(dirname "$0")"

RASM="${RASM:-rasm}"
IDSK="${IDSK:-iDSK}"

mkdir -p build
# WSL2 can leave a stale __pycache__ whose mtime matches a fresh edit;
# python then imports PRE-EDIT modules and the build quietly uses old
# art or maps.  Costed us twice -- purge it before any tool runs.
rm -rf tools/__pycache__

# Compile every sprite frame into draw-itself routines at #8000.
python3 tools/sprite_compile.py > src/sprites_c.asm

# Generate + verify all 59 levels: src/levels.asm (tables, tiles, font)
# and build/levels0-2.bin (raw blobs, one per extra-RAM bank).
python3 tools/level_gen.py src/levels.asm build

# Assemble to a raw binary (org #1000 is set in the source).
"$RASM" src/main.asm -ob build/shaft.bin -os build/shaft.sym -s

# Execute the assembled music player: the title theme must lock its
# 15-second loop on the right AY registers with a keyboard-safe mixer.
python3 tools/verify_music.py

# ...and the footsteps: a quiet click every 5 walking frames that never
# clips a ringing effect and never sounds in the air or on a ladder.
python3 tools/verify_steps.py

# ...and the enemies: the rifle ramp from level 10, the duck under the
# bullet, the drone dispatch from 20 and the whip aims that reach it.
python3 tools/verify_enemies.py

# ...and the attract loop: both pages land in both buffers, the
# exhibits leave the prompt's lines black.
python3 tools/verify_menu.py

# ...and the lift: the LED floor readout decoded off the screen, the
# double doors' catchment, LVL in front of the HUD's number.
python3 tools/verify_lift.py

# ...and the seven mini-games behind the background arches: entry,
# each show's rules driven with scripted keys, and the way home.
python3 tools/verify_minigames.py

# BASIC loader: AMSDOS ASCII wants CR/LF line ends and a ^Z EOF marker.
awk '{printf "%s\r\n", $0}' src/shaft.bas > build/shaft.bas
printf '\032' >> build/shaft.bas

# Fresh DSK: BASIC loader, title screen, game binary (load/exec #1000).
# RUN"SHAFT finds SHAFT.BAS first (AMSDOS tries "", .BAS, .BIN in order).
rm -f build/theshaft.dsk
"$IDSK" build/theshaft.dsk -n
"$IDSK" build/theshaft.dsk -i build/shaft.bas -t 0
"$IDSK" build/theshaft.dsk -i docs/revive8b.scr -t 1 -c C000
"$IDSK" build/theshaft.dsk -i build/levels0.bin -t 1 -c 4000
"$IDSK" build/theshaft.dsk -i build/levels1.bin -t 1 -c 4000
"$IDSK" build/theshaft.dsk -i build/levels2.bin -t 1 -c 4000
"$IDSK" build/theshaft.dsk -i build/shaft.bin -t 1 -c 1000 -e 1000

echo
"$IDSK" build/theshaft.dsk -l
echo
echo 'OK -> build/theshaft.dsk   (in the emulator: RUN"SHAFT)'
