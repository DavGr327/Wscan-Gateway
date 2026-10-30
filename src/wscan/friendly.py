"""Plain names for the channel settings, used by `wscan set`, `wscan show` and `wscan setup`.

  bitrate=500000               bus bit rate, 10000..1000000                 (gateway: baud)
  mode=normal|loopback                                                       (gateway: paritybit)
  filter=off                   let every frame through                       (gateway: ID_FITER_TEPE ...)
  filter=allow:100-1FF         only these standard IDs (hex); block:... drops them instead;
                               allow-ext:/block-ext: for 29-bit IDs; a single ID works too: allow:101
  packet_ms=10                 the gateway sends what it collected every N ms   (gateway: packttime)
  packet_frames=50             ... or as soon as it has N frames                (gateway: packtlen)
  name=can0                    interface name on this host (bridge)
  on=true|false                use the channel or not (bridge)
"""
from __future__ import annotations

BITRATE_MIN, BITRATE_MAX = 10000, 1000000
MODES = {"normal": "2", "loopback": "1"}
FILTER_KINDS = {"allow": ("1", False), "block": ("2", False), "allow-ext": ("3", True), "block-ext": ("4", True)}
PLAIN_GATEWAY = ("bitrate", "mode", "filter", "packet_ms", "packet_frames")
BRIDGE_ALIASES = {"name": "interface", "ifname": "interface", "on": "enabled"}
DISPLAY = {"baud": "bitrate", "paritybit": "mode", "packttime": "packet_ms", "packtlen": "packet_frames"}
FILTER_HELP = "filter=off, allow:100-1FF, block:100, allow-ext:18FF0000-18FFFFFF, block-ext:... (IDs in hex)"


class FriendlyError(ValueError):
    pass


def _int(name: str, value: str, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except ValueError:
        raise FriendlyError(f"{name} needs a number, got '{value}'") from None
    if not lo <= n <= hi:
        raise FriendlyError(f"{name} must be between {lo} and {hi}, got {n}")
    return n


def check_bitrate(value: str) -> str:
    return str(_int("bitrate", value, BITRATE_MIN, BITRATE_MAX))


def parse_filter(text: str) -> dict:
    t = text.strip().lower()
    if t in ("off", "none", "0"):
        return {"ID_FITER_TEPE": "0"}
    kind, sep, rng = t.partition(":")
    if kind not in FILTER_KINDS or not sep:
        raise FriendlyError(FILTER_HELP)
    code, ext = FILTER_KINDS[kind]
    lo_s, _, hi_s = rng.partition("-")
    try:
        lo, hi = int(lo_s, 16), int(hi_s or lo_s, 16)
    except ValueError:
        raise FriendlyError(FILTER_HELP) from None
    limit = 0x1FFFFFFF if ext else 0x7FF
    if not 0 <= lo <= hi <= limit:
        raise FriendlyError(f"filter range {lo:X}-{hi:X} must be ascending and within 0-{limit:X}")
    p = "Exd" if ext else "Std"
    return {"ID_FITER_TEPE": code, f"{p}IDLow": str(lo), f"{p}IDHigh": str(hi)}


def expand(field: str, value: str) -> dict:
    """A plain channel setting -> {gateway field: value}."""
    if field == "bitrate":
        return {"baud": check_bitrate(value)}
    if field == "mode":
        if value.lower() not in MODES:
            raise FriendlyError("mode must be normal or loopback")
        return {"paritybit": MODES[value.lower()]}
    if field == "packet_ms":
        return {"packttime": str(_int("packet_ms", value, 1, 1000))}
    if field == "packet_frames":
        return {"packtlen": str(_int("packet_frames", value, 1, 1000))}
    if field == "filter":
        return parse_filter(value)
    raise FriendlyError(f"'{field}' is not one of {', '.join(PLAIN_GATEWAY)}")


def describe_filter(values: dict) -> str:
    code = values.get("ID_FITER_TEPE", "0")
    kind = next((k for k, (c, _) in FILTER_KINDS.items() if c == code), None)
    if kind is None:
        return "off"
    p = "Exd" if FILTER_KINDS[kind][1] else "Std"
    lo, hi = int(values.get(f"{p}IDLow", 0)), int(values.get(f"{p}IDHigh", 0))
    return f"{kind}:{lo:X}" if lo == hi else f"{kind}:{lo:X}-{hi:X}"


FILTER_FIELDS = ("ID_FITER_TEPE", "StdIDLow", "StdIDHigh", "ExdIDLow", "ExdIDHigh")
_MODE_NAME = {v: k for k, v in MODES.items()}


def change_lines(page: str, changes: dict, current: dict | None = None) -> list[str]:
    """{field: (old, new)} on a gateway page -> readable lines. With the page's full values
    (`current`, before the change) the filter fields become a single `filter` line."""
    channel = page in ("can1", "can2")
    rest, lines = dict(changes), []
    if channel and current is not None and set(FILTER_FIELDS) & set(changes):
        before, after = dict(current), dict(current)
        for f, (old, new) in changes.items():
            before[f], after[f] = old, new
        lines.append(f"{page}.filter: {describe_filter(before)} -> {describe_filter(after)}")
        for f in FILTER_FIELDS:
            rest.pop(f, None)
    for f, (old, new) in rest.items():
        if channel and f == "paritybit":
            old, new = _MODE_NAME.get(old, old), _MODE_NAME.get(new, new)
        lines.append(f"{page}.{DISPLAY.get(f, f) if channel else f}: {old} -> {new}")
    return lines


def describe_channel(values: dict) -> dict:
    """Gateway form values of a channel -> plain words."""
    mode = {v: k for k, v in MODES.items()}.get(values.get("paritybit", ""), values.get("paritybit", "?"))
    return {
        "bitrate": values.get("baud", "?"),
        "mode": mode,
        "filter": describe_filter(values),
        "packets": f"{values.get('packttime', '?')} ms / {values.get('packtlen', '?')} frames",
        "port": values.get("localport", "?"),
        "tcp_server": values.get("workmode") == "1",
    }
