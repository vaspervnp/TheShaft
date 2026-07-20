#!/usr/bin/env bash
# Build The Shaft: assemble with rasm, pack into a CPC disk image with iDSK.
set -euo pipefail
cd "$(dirname "$0")"

RASM="${RASM:-rasm}"
IDSK="${IDSK:-iDSK}"

mkdir -p build

# Regenerate level data (tiles + map + collision rects) from ASCII art.
python3 tools/level_gen.py > src/level01.asm

# Assemble to a raw binary (org #1000 is set in the source).
"$RASM" src/main.asm -ob build/shaft.bin

# BASIC loader: AMSDOS ASCII wants CR/LF line ends and a ^Z EOF marker.
awk '{printf "%s\r\n", $0}' src/shaft.bas > build/shaft.bas
printf '\032' >> build/shaft.bas

# Fresh DSK: BASIC loader, title screen, game binary (load/exec #1000).
# RUN"SHAFT finds SHAFT.BAS first (AMSDOS tries "", .BAS, .BIN in order).
rm -f build/theshaft.dsk
"$IDSK" build/theshaft.dsk -n
"$IDSK" build/theshaft.dsk -i build/shaft.bas -t 0
"$IDSK" build/theshaft.dsk -i docs/revive8b.scr -t 1 -c C000
"$IDSK" build/theshaft.dsk -i build/shaft.bin -t 1 -c 1000 -e 1000

echo
"$IDSK" build/theshaft.dsk -l
echo
echo 'OK -> build/theshaft.dsk   (in the emulator: RUN"SHAFT)'
