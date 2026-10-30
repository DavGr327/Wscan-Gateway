"""Writes single values into config.toml line by line, so comments and layout survive.

It only knows the shape of wscan's own config: top-level sections ([gateway], [bridge],
[network]), [[channel]] blocks identified by their `name`, and each channel's optional
[channel.gateway] sub-table.
"""
from __future__ import annotations

import json
import re

HEADER_RE = re.compile(r"^\s*(\[\[?)\s*([A-Za-z0-9_.]+)\s*\]\]?\s*(#.*)?$")
NAME_RE = re.compile(r"^\s*name\s*=\s*[\"']([^\"']*)[\"']")


def fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return value  # gateway fields are strings on the wire but read better as numbers
    return json.dumps(value)


def _key_re(key: str):
    return re.compile(rf"^\s*{re.escape(key)}\s*=")


def _regions(lines: list[str]) -> list[dict]:
    """[{start, end, name, array, channel}] for each header; `channel` is the channel name
    a [[channel]] block or its [channel.gateway] sub-table belongs to."""
    regions, current_channel = [], None
    for i, line in enumerate(lines):
        m = HEADER_RE.match(line)
        if not m:
            continue
        if regions:
            regions[-1]["end"] = i
        array, name = m.group(1) == "[[", m.group(2)
        if array and name == "channel":
            current_channel = None  # filled in from the name = line below
        elif not name.startswith("channel."):
            current_channel = None
        regions.append({"start": i, "end": len(lines), "name": name, "array": array, "channel": current_channel})
        if array and name == "channel":
            for j in range(i + 1, len(lines)):
                if HEADER_RE.match(lines[j]):
                    break
                n = NAME_RE.match(lines[j])
                if n:
                    current_channel = regions[-1]["channel"] = n.group(1)
                    break
    return regions


def _content_end(lines: list[str], r: dict) -> int:
    """Index after the last non-blank line of a region: new keys go there, before blank lines."""
    end = r["end"]
    while end > r["start"] + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _set_in_region(lines: list[str], r: dict, key: str, value, after: int | None = None) -> None:
    rx = _key_re(key)
    for i in range(r["start"] + 1, r["end"]):
        if rx.match(lines[i]):
            lines[i] = f"{key} = {fmt(value)}"
            return
    lines.insert(after if after is not None else _content_end(lines, r), f"{key} = {fmt(value)}")


def has_channels(lines: list[str]) -> bool:
    return any(r["array"] and r["name"] == "channel" for r in _regions(lines))


def add_channel(lines: list[str], name: str, port: int, interface: str, enabled: bool = True) -> None:
    if lines and lines[-1].strip():
        lines.append("")
    lines += ["[[channel]]", f'name = "{name}"', f"port = {port}", f'interface = "{interface}"']
    if not enabled:
        lines.append("enabled = false")


def set_section_key(lines: list[str], section: str, key: str, value) -> None:
    regions = _regions(lines)
    for r in regions:
        if not r["array"] and r["name"] == section:
            _set_in_region(lines, r, key, value)
            return
    # missing section: [gateway.serial] goes right after [gateway] (and its sub-tables),
    # a top-level one before the [[channel]] blocks
    parent = section.rpartition(".")[0]
    at = None
    for r in regions:
        if not r["array"] and (r["name"] == parent or r["name"].startswith(parent + ".")) and parent:
            at = _content_end(lines, r)
    if at is None:
        at = next((r["start"] for r in regions if r["array"]), len(lines))
        lines[at:at] = [f"[{section}]", f"{key} = {fmt(value)}", ""]
    else:
        lines[at:at] = ["", f"[{section}]", f"{key} = {fmt(value)}"]


def remove_section(lines: list[str], section: str) -> None:
    """Drops a table's header and keys; its comments stay."""
    for r in _regions(lines):
        if not r["array"] and r["name"] == section:
            keep = [ln for ln in lines[r["start"] + 1:r["end"]] if not ln.strip() or ln.lstrip().startswith("#")]
            lines[r["start"]:r["end"]] = keep
            return


def _channel_region(lines: list[str], channel: str) -> dict:
    for r in _regions(lines):
        if r["array"] and r["name"] == "channel" and r["channel"] == channel:
            return r
    raise KeyError(f"no [[channel]] with name = \"{channel}\" in the file")


def set_channel_key(lines: list[str], channel: str, key: str, value) -> None:
    r = _channel_region(lines, channel)
    name_line = next(i for i in range(r["start"] + 1, r["end"]) if NAME_RE.match(lines[i]))
    _set_in_region(lines, r, key, value, after=name_line + 1)


def set_channel_gateway(lines: list[str], channel: str, field: str, value) -> None:
    owner = _channel_region(lines, channel)
    regions = _regions(lines)
    idx = next(i for i, r in enumerate(regions) if r["start"] == owner["start"])
    last = owner
    for r in regions[idx + 1:]:
        if r["array"] or not r["name"].startswith("channel."):
            break
        if r["name"] == "channel.gateway":
            _set_in_region(lines, r, field, value)
            return
        last = r
    at = _content_end(lines, last)
    lines[at:at] = ["", "[channel.gateway]", f"{field} = {fmt(value)}"]
