#!/usr/bin/env python3
"""Measurements through the bridge, with the PMC simulator on the bus (needs pyserial).

  measure.py latency [--n 200]          round trip: 0x200 on the interface, the PMC answers 0x280
  measure.py load 1000 2000 4000 ...    losses under load: the PMC sends 0x300 with a counter

  --if vcan0 (SocketCAN interface of the channel)   --serial /dev/ttyACM0 (the PMC)
"""
import argparse
import socket
import statistics
import struct
import threading
import time

import serial

CAN_FRAME = struct.Struct("=IB3x8s")
ID_PING, ID_PONG, ID_LOAD = 0x200, 0x280, 0x300


def open_can(ifname):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    s.bind((ifname,))
    return s


def recv(s, timeout):
    s.settimeout(timeout)
    can_id, dlc, data = CAN_FRAME.unpack(s.recv(CAN_FRAME.size))
    return can_id & 0x1FFFFFFF, data[:dlc]


def latency(s, n, hz):
    rtts, lost = [], 0
    for i in range(n):
        tag = struct.pack(">I", i)
        t0 = time.perf_counter()
        s.send(CAN_FRAME.pack(ID_PING, 4, tag.ljust(8, b"\x00")))
        end = t0 + 0.5
        while True:
            try:
                can_id, data = recv(s, max(end - time.perf_counter(), 0.001))
            except socket.timeout:
                lost += 1
                break
            if can_id == ID_PONG and data[:4] == tag:
                rtts.append((time.perf_counter() - t0) * 1000)
                break
        time.sleep(1 / hz)
    if not rtts:
        print(f"no answer to {n} pings: is the PMC simulator on the bus?")
        return
    q = statistics.quantiles(rtts, n=20) if len(rtts) > 1 else rtts * 19
    print(f"round trip over {n} pings: min {min(rtts):.1f}  median {statistics.median(rtts):.1f}"
          f"  p95 {q[18]:.1f}  max {max(rtts):.1f} ms, lost {lost}")


class PmcSerial(threading.Thread):
    """Keeps reading the PMC serial port and remembers its last status line."""

    def __init__(self, ser):
        super().__init__(daemon=True)
        self.ser, self.last, self.lines = ser, "", 0

    def run(self):
        buf = b""
        while True:
            try:
                buf += self.ser.read(4096)
            except (OSError, TypeError, serial.SerialException):  # port closed at the end
                return
            *done, buf = buf.split(b"\n")
            for ln in done:
                self.lines += 1
                if ln.startswith(b"tx "):
                    self.last = ln.decode("utf-8", "replace").strip()


def drain(s, quiet=1.0):
    # wait until the 0x300 frames of a previous burst stop arriving
    last = time.time()
    while time.time() - last < quiet:
        try:
            if recv(s, 0.1)[0] == ID_LOAD:
                last = time.time()
        except socket.timeout:
            pass


def load(s, ser, mon, fps, secs):
    ser.write(b"load 0\n")
    drain(s)
    ser.write(f"load {fps}\n".encode())
    seen, first, last, t_first, t_last = 0, None, None, None, None
    missing = gaps = 0
    t_end = time.time() + secs + 1
    while time.time() < t_end:
        try:
            can_id, data = recv(s, 0.2)
        except socket.timeout:
            continue
        if can_id != ID_LOAD:
            continue
        c = struct.unpack(">I", data[:4])[0]
        if first is None:
            if c > 50:  # tail of an older burst
                continue
            first, t_first = c, time.time()
        elif c > last + 1:
            missing += c - last - 1
            gaps += 1
        last, t_last = c, time.time()
        seen += 1
        if t_last - t_first >= secs:
            break
    status = mon.last
    ser.write(b"load 0\n")
    if first is None:
        print(f"{fps:5d} frames/s asked: no 0x300 frame arrived")
        return
    span = last - first + 1
    rate = span / (t_last - t_first) if t_last > t_first else 0
    bus = status.split("|")[-1].strip() if "|" in status else "?"
    txq = " ".join(x for x in status.split() if x.startswith("txq_full"))
    print(f"{fps:5d} frames/s asked: {rate:6.0f}/s through, lost {missing} of {span}"
          f" ({100 * missing / span:.2f} %) in {gaps} gaps | PMC: {bus} {txq}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--if", dest="ifname", default="vcan0")
    ap.add_argument("--serial", default="/dev/ttyACM0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    la = sub.add_parser("latency")
    la.add_argument("--n", type=int, default=200)
    la.add_argument("--hz", type=float, default=20)
    lo = sub.add_parser("load")
    lo.add_argument("fps", type=int, nargs="+")
    lo.add_argument("--secs", type=float, default=5)
    a = ap.parse_args()

    s = open_can(a.ifname)
    if a.cmd == "latency":
        latency(s, a.n, a.hz)
        return
    with serial.Serial(a.serial, 115200, timeout=0.2) as ser:
        mon = PmcSerial(ser)
        mon.start()
        for fps in a.fps:
            load(s, ser, mon, fps, a.secs)
            time.sleep(1)
        if not mon.lines:
            print("warning: no output on the PMC serial port")


if __name__ == "__main__":
    main()
