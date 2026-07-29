#!/usr/bin/env bash
# Build the DEMO disc: a scripted 16:9 camera move over a set made from
# the game's own tiles and compiled sprites.  Needs build.sh to have run
# (it produces src/levels.asm and src/sprites_c.asm, which are reused
# here verbatim -- the demo ships the game's real art, not a copy).
set -euo pipefail
cd "$(dirname "$0")"

RASM="${RASM:-rasm}"
IDSK="${IDSK:-iDSK}"
mkdir -p build
# WSL2 can leave a stale __pycache__ whose mtime matches a fresh edit;
# python then imports PRE-EDIT modules and the build quietly uses old
# art or maps.  Costed us twice -- purge it before any tool runs.
rm -rf tools/__pycache__

python3 tools/sprite_compile.py > src/sprites_c.asm
python3 tools/level_gen.py src/levels.asm build >/dev/null
python3 tools/demo_scene.py src/demo_scene.asm

"$RASM" src/demo.asm -ob build/demo.bin -os build/demo.sym -s -I src

# Execute the assembled binary and check the camera, the set edits and
# the rendered picture against an independent model (tools/z80mini.py).
python3 tools/verify_demo.py
python3 tools/verify_demo_loop.py
python3 tools/verify_demo_sfx.py
python3 tools/verify_demo_timeline.py

awk '{printf "%s\r\n", $0}' src/demo.bas > build/demo.bas
printf '\032' >> build/demo.bas

rm -f build/theshaftdemo.dsk
"$IDSK" build/theshaftdemo.dsk -n
"$IDSK" build/theshaftdemo.dsk -i build/demo.bas -t 0
"$IDSK" build/theshaftdemo.dsk -i build/demo.bin -t 1 -c 1000 -e 1000

echo
"$IDSK" build/theshaftdemo.dsk -l
echo
echo 'OK -> build/theshaftdemo.dsk   (in the emulator: RUN"DEMO)'
