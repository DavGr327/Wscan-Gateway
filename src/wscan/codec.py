"""Frame formats: the gateway's 13-byte TCP format and the kernel's SocketCAN struct.

Gateway data ports (TCP/UDP), one CAN frame = 13 bytes:

  byte 0      frame info: bit7 = extended ID, bit6 = remote frame (RTR), bit3..0 = DLC
  bytes 1-4   CAN ID, big-endian
  bytes 5-12  data, always 8 bytes, zero padded

TCP is a byte stream: one recv() can hold several frames or half of one, so incoming
data goes through a Deframer.
"""
from __future__ import annotations

import struct
from typing import NamedTuple

FRAME_LEN = 13
SFF_MASK, EFF_MASK = 0x7FF, 0x1FFFFFFF

_GW = struct.Struct(">BI8s")
_CAN = struct.Struct("=IB3x8s")  # struct can_frame
CAN_FRAME_SIZE = _CAN.size
CAN_EFF_FLAG, CAN_RTR_FLAG, CAN_ERR_FLAG = 0x80000000, 0x40000000, 0x20000000


class Frame(NamedTuple):
    can_id: int
    data: bytes = b""
    ext: bool = False
    rtr: bool = False
    dlc: int = -1  # -1: len(data); set it only for remote frames, which carry a length but no data

    @property
    def length(self) -> int:
        return len(self.data) if self.dlc < 0 else self.dlc


def _check(f: Frame) -> None:
    if len(f.data) > 8 or not 0 <= f.length <= 8:
        raise ValueError("classic CAN carries at most 8 data bytes")
    if not 0 <= f.can_id <= (EFF_MASK if f.ext else SFF_MASK):
        raise ValueError(f"CAN ID 0x{f.can_id:X} out of range for a {'29' if f.ext else '11'}-bit ID")


def encode(f: Frame) -> bytes:
    """Frame -> 13 bytes for the gateway."""
    _check(f)
    info = (0x80 if f.ext else 0) | (0x40 if f.rtr else 0) | f.length
    return _GW.pack(info, f.can_id, bytes(f.data).ljust(8, b"\x00"))


def decode(raw: bytes) -> Frame:
    """13 bytes from the gateway -> Frame."""
    info, can_id, data = _GW.unpack(raw)
    ext, rtr, dlc = bool(info & 0x80), bool(info & 0x40), min(info & 0x0F, 8)
    can_id &= EFF_MASK if ext else SFF_MASK
    if rtr:
        return Frame(can_id, b"", ext, True, dlc)
    return Frame(can_id, data[:dlc], ext, False)


def to_can_frame(f: Frame) -> bytes:
    """Frame -> struct can_frame for a SocketCAN socket."""
    _check(f)
    can_id = f.can_id | (CAN_EFF_FLAG if f.ext else 0) | (CAN_RTR_FLAG if f.rtr else 0)
    return _CAN.pack(can_id, f.length, bytes(f.data).ljust(8, b"\x00"))


def from_can_frame(raw: bytes) -> Frame | None:
    """struct can_frame -> Frame; None for error frames, which have no place on the gateway."""
    can_id, dlc, data = _CAN.unpack(raw[:CAN_FRAME_SIZE])
    if can_id & CAN_ERR_FLAG:
        return None
    ext, rtr, dlc = bool(can_id & CAN_EFF_FLAG), bool(can_id & CAN_RTR_FLAG), min(dlc, 8)
    can_id &= EFF_MASK if ext else SFF_MASK
    if rtr:
        return Frame(can_id, b"", ext, True, dlc)
    return Frame(can_id, data[:dlc], ext, False)


class Deframer:
    """Cuts the gateway's TCP stream into 13-byte frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        n = len(self._buf) - len(self._buf) % FRAME_LEN
        frames = [decode(bytes(self._buf[i:i + FRAME_LEN])) for i in range(0, n, FRAME_LEN)]
        del self._buf[:n]
        return frames

    def reset(self) -> None:
        self._buf.clear()

    @property
    def pending(self) -> int:
        return len(self._buf)


def parse_cansend(text: str) -> Frame:
    """'123#DEADBEEF', '18FF50E5#01', '123#R4' (remote frame of length 4), like can-utils' cansend."""
    sid, sep, sdata = text.partition("#")
    if not sep or not sid:
        raise ValueError(f"expected ID#DATA, got {text!r}")
    can_id = int(sid, 16)
    ext = len(sid) > 3 or can_id > SFF_MASK
    if sdata[:1].upper() == "R":
        return Frame(can_id, b"", ext, True, int(sdata[1:] or 0))
    return Frame(can_id, bytes.fromhex(sdata.replace(".", "")), ext)


def format_frame(f: Frame) -> str:
    sid = f"{f.can_id:08X}" if f.ext else f"{f.can_id:03X}"
    body = "remote request" if f.rtr else f.data.hex(" ").upper()
    return f"{sid:>8}  [{f.length}]  {body}"
