import pytest

from wscan.codec import (FRAME_LEN, Deframer, Frame, decode, encode, format_frame, from_can_frame,
                         parse_cansend, to_can_frame)


def test_standard_frame_bytes():
    raw = encode(Frame(0x101, bytes.fromhex("0A6900734 51C02D5".replace(" ", ""))))
    assert raw.hex(" ") == "08 00 00 01 01 0a 69 00 73 45 1c 02 d5"
    assert decode(raw) == Frame(0x101, bytes.fromhex("0A690073451C02D5"))


def test_extended_frame_bytes():
    raw = encode(Frame(0x18FF50E5, b"\x00\x00\x02\xd5\xca\xfe\xba\xbe", ext=True))
    assert raw[:5].hex(" ") == "88 18 ff 50 e5"
    assert decode(raw).ext and decode(raw).can_id == 0x18FF50E5


def test_short_data_is_padded_and_trimmed_back():
    raw = encode(Frame(0x200, b"\xc0\xff\xee"))
    assert len(raw) == FRAME_LEN and raw[0] == 3 and raw[5:] == b"\xc0\xff\xee" + b"\x00" * 5
    assert decode(raw).data == b"\xc0\xff\xee"


def test_remote_frame_keeps_its_length():
    f = decode(encode(Frame(0x123, rtr=True, dlc=4)))
    assert f.rtr and f.length == 4 and f.data == b""


@pytest.mark.parametrize("bad", [Frame(0x800), Frame(0x20000000, ext=True), Frame(1, b"x" * 9)])
def test_out_of_range(bad):
    with pytest.raises(ValueError):
        encode(bad)


def test_deframer_handles_split_and_joined_frames():
    a, b = encode(Frame(1, b"\x01")), encode(Frame(2, b"\x02"))
    d = Deframer()
    assert d.feed(a[:5]) == []
    assert [f.can_id for f in d.feed(a[5:] + b + a[:2])] == [1, 2]
    assert d.pending == 2


def test_socketcan_roundtrip():
    for f in (Frame(0x7FF, b"\x01\x02"), Frame(0x1ABCDEF, b"", ext=True), Frame(0x10, rtr=True, dlc=8)):
        raw = to_can_frame(f)
        assert len(raw) == 16
        assert from_can_frame(raw) == f


def test_error_frames_are_dropped():
    assert from_can_frame((0x20000004).to_bytes(4, "little") + bytes(12)) is None


def test_cansend_syntax():
    assert parse_cansend("123#DEADBEEF") == Frame(0x123, bytes.fromhex("DEADBEEF"))
    assert parse_cansend("00000123#01").ext
    assert parse_cansend("123#R3") == Frame(0x123, b"", False, True, 3)
    assert "18FF50E5" in format_frame(parse_cansend("18FF50E5#CAFE"))
    with pytest.raises(ValueError):
        parse_cansend("123")
