"""End-to-end bridge test against the fake data port.

Needs Linux and an existing virtual CAN interface named in $WSCAN_TEST_VCAN:
  sudo modprobe vcan && sudo ip link add dev vcantest type vcan && sudo ip link set vcantest up
"""
import os
import socket
import time

import pytest
from fake_gateway import FakeData

from wscan.bridge import Link
from wscan.codec import CAN_FRAME_SIZE, Frame, from_can_frame, to_can_frame
from wscan.config import Channel, Config

IFACE = os.environ.get("WSCAN_TEST_VCAN")
pytestmark = pytest.mark.skipif(not IFACE or not hasattr(socket, "AF_CAN"),
                                reason="needs Linux and WSCAN_TEST_VCAN=<existing vcan interface>")


def can_socket():
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((IFACE,))
    s.settimeout(3)
    return s


SEEN = []


def recv_until(s, can_id):
    end = time.time() + 3
    while time.time() < end:
        f = from_can_frame(s.recv(CAN_FRAME_SIZE))
        SEEN.append(f.can_id)
        if f and f.can_id == can_id:
            return f
    raise AssertionError(f"0x{can_id:X} never arrived")


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.02)
    return cond()


def test_bridge_both_ways_and_reconnect():
    data = FakeData()
    data.send(Frame(0x7AA, b"old"))  # sent before the bridge connects: must be dropped
    cfg = Config(host="127.0.0.1", flush_seconds=0.3, keepalive_idle=1, keepalive_interval=1,
                 keepalive_count=2, channels=[Channel("can1", data.port, IFACE)])
    link = Link(cfg.channels[0], cfg)
    app = can_socket()
    link.start()
    assert data.wait_clients()
    time.sleep(0.5)

    # gateway -> interface
    data.send(Frame(0x101, b"\x0a\x69"), Frame(0x18FF50E5, b"\xca\xfe", ext=True))
    assert recv_until(app, 0x101).data == b"\x0a\x69"
    assert recv_until(app, 0x18FF50E5).ext
    assert 0x7AA not in SEEN  # the old frame was dropped, not delivered
    assert wait_for(lambda: link.rx == 2)  # rx is counted right after the write to the interface

    # interface -> gateway
    app.send(to_can_frame(Frame(0x200, b"\xc0\xff\xee")))
    assert wait_for(lambda: data.received, 3)
    assert data.received == [Frame(0x200, b"\xc0\xff\xee")]

    # the gateway restarts: the bridge reconnects by itself
    data.drop_clients()
    assert data.wait_clients(timeout=10)
    time.sleep(0.5)
    data.send(Frame(0x102, b"\x01"))
    assert recv_until(app, 0x102).data == b"\x01"
    assert link.reconnects >= 1 and link.connected
    data.close()
