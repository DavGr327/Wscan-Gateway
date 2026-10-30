#!/usr/bin/env python3
"""Sends a command to the PMC_CAN_Sim sketch and prints what comes back (needs pyserial).

  pmc_cmd.py baud 500000
  pmc_cmd.py load 1000
  pmc_cmd.py                 just listen: status lines with counters and CAN error state
"""
import os
import sys
import time

import serial

PORT = os.environ.get("PORT", "/dev/ttyACM0")

with serial.Serial(PORT, 115200, timeout=0.2) as ser:
    if len(sys.argv) > 1:
        ser.write((" ".join(sys.argv[1:]) + "\n").encode())
    end, buf = time.time() + 2.2, b""
    while time.time() < end:
        buf += ser.read(4096)
    print(buf.decode("utf-8", "replace").strip())
