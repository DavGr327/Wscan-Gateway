#!/usr/bin/env bash
# Builds (if arduino-cli is here) and flashes PMC_CAN_Sim onto a Portenta Machine Control on USB:
# 1200 bps touch -> DFU bootloader (2341:035b) -> dfu-util -> the sketch starts again (2341:025b)
#
#   ./flash.sh                     build + flash
#   ./flash.sh firmware.bin        flash a .bin built elsewhere (arduino-cli compile --output-dir ...)
#
# Needs: dfu-util (apt install dfu-util, or DFU_UTIL=/path/to/dfu-util), python3 with pyserial.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PORT="${PORT:-/dev/ttyACM0}"
DFU="${DFU_UTIL:-dfu-util}"
FQBN=arduino:mbed_portenta:envie_m7

BIN="${1:-}"
if [ -z "$BIN" ]; then
    command -v arduino-cli >/dev/null || { echo "arduino-cli not found: build elsewhere and pass the .bin" >&2; exit 1; }
    arduino-cli compile --fqbn "$FQBN" --output-dir "$HERE/build" "$HERE/PMC_CAN_Sim"
    BIN="$HERE/build/PMC_CAN_Sim.ino.bin"
fi
[ -f "$BIN" ] || { echo "no such file: $BIN" >&2; exit 1; }

python3 -c "import serial; s = serial.Serial('$PORT', 1200); s.dtr = False; s.close()" 2>/dev/null || true
for _ in $(seq 1 20); do lsusb | grep -q 2341:035b && break; sleep 0.5; done
lsusb | grep -q 2341:035b || { echo "no DFU bootloader: double-click the PMC reset button and retry" >&2; exit 2; }

sudo "$DFU" -d 2341:035b -a 0 --dfuse-address=0x08040000:leave -D "$BIN"
for _ in $(seq 1 30); do lsusb | grep -q 2341:025b && [ -e "$PORT" ] && break; sleep 0.5; done
lsusb | grep -q 2341:025b && echo "sketch running on $PORT" || { echo "the PMC did not restart" >&2; exit 3; }
