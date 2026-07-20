#!/usr/bin/env bash
# Build The Shaft: assemble with rasm, pack into a CPC disk image with iDSK.
set -euo pipefail
cd "$(dirname "$0")"

RASM="${RASM:-rasm}"
IDSK="${IDSK:-iDSK}"

mkdir -p build

# Assemble to a raw binary (org #1000 is set in the source).
"$RASM" src/main.asm -ob build/shaft.bin

# Fresh DSK; import the binary with AMSDOS header: load #1000, exec #1000.
rm -f build/theshaft.dsk
"$IDSK" build/theshaft.dsk -n
"$IDSK" build/theshaft.dsk -i build/shaft.bin -t 1 -c 1000 -e 1000

echo
"$IDSK" build/theshaft.dsk -l
echo
echo 'OK -> build/theshaft.dsk   (in the emulator: RUN"SHAFT)'
