"""Try settings live, then keep or drop them, all from the command line.

  wscan set can1.bitrate=500000   applied now (gateway written and restarted, bridge restarted
                                  if needed), recorded as unsaved next to the config file
  wscan pending                   what is applied but not saved
  wscan save                      writes it into config.toml (comments kept)
  wscan revert                    puts back what was there before

Targets: can1|can2.<plain name> (bitrate, mode, filter, packet_ms, packet_frames: see friendly.py),
can1|can2.name|on|port (bridge side; port is changed on the gateway too), gateway.host, and the raw
gateway fields of `wscan gw show`: can1|can2.<field>, serial.<field>, func.<field>.
"""
from __future__ import annotations

import copy
import os
import shutil
import subprocess
from pathlib import Path

from . import tomledit
from .config import (CHANNEL_KEYS, GATEWAY_CHANNELS, GATEWAY_PAGES, REPO_ROOT, Config, ConfigError, get_value,
                     load, read_pending, set_value, validate, write_pending)
from .friendly import BRIDGE_ALIASES, PLAIN_GATEWAY, FriendlyError, change_lines, expand
from .gateway import WRITABLE, Gateway, GatewayError

SERVICE = "wscan-bridge"


class ChangeError(Exception):
    pass


def _convert(target: str, typ, value: str):
    if typ is int:
        try:
            return int(value)
        except ValueError:
            raise ChangeError(f"{target} needs a number, got '{value}'") from None
    if typ is bool:
        low = value.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ChangeError(f"{target} needs true or false, got '{value}'")
    return value


def parse(items: list[str]) -> tuple[dict, dict]:
    """-> (gateway {page: {field: value}}, bridge {"can1.port": value, ...})"""
    gw, br = {}, {}
    for item in items:
        target, sep, value = item.partition("=")
        name, dot, field = target.partition(".")
        if not sep or not dot or not field:
            raise ChangeError(f"expected name.field=value, got '{item}'")
        field = BRIDGE_ALIASES.get(field, field) if name in GATEWAY_CHANNELS else field
        if target == "gateway.host":
            br[target] = value
        elif name in GATEWAY_CHANNELS and field in CHANNEL_KEYS:
            key = f"{name}.{field}"
            br[key] = _convert(key, CHANNEL_KEYS[field], value)
            if field == "port":  # the gateway must listen where the bridge connects
                gw.setdefault(name, {})["localport"] = str(br[key])
        elif name in GATEWAY_CHANNELS and field in PLAIN_GATEWAY:
            try:
                gw.setdefault(name, {}).update(expand(field, value))
            except FriendlyError as e:
                raise ChangeError(f"{name}.{e}" if str(e).startswith(field) else f"{name}: {e}") from None
        elif name in WRITABLE:
            gw.setdefault(name, {})[field] = value
        else:
            raise ChangeError(f"unknown setting '{target}'. On a channel (can1/can2): bitrate, mode, filter, "
                              "packet_ms, packet_frames, name, on, port; also gateway.host "
                              "(and the raw fields of `wscan gw show`)")
    return gw, br


def _record(entries: dict, key: str, old, new) -> None:
    """Keeps the value from before the first change; drops the entry when it is back there."""
    orig = entries[key][0] if key in entries else old
    if orig == new:
        entries.pop(key, None)
    else:
        entries[key] = [orig, new]


def service_config() -> Path | None:
    """Config file the bridge service runs with, or None if there is no such service."""
    if not shutil.which("systemctl"):
        return None
    r = subprocess.run(["systemctl", "show", "-p", "Environment", "--value", SERVICE],
                       capture_output=True, text=True)
    for token in r.stdout.split():
        if token.startswith("WSCAN_CONFIG="):
            return Path(token.split("=", 1)[1])
    return None


def restart_bridge(cfg: Config, say=print, quiet_if_missing: bool = False) -> bool:
    svc = service_config()
    if svc is None and quiet_if_missing:
        return False
    active = svc is not None and subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
    mine = cfg.source or REPO_ROOT / "config.toml"
    if not active or Path(svc).resolve() != Path(mine).resolve():
        say("bridge: restart it to use the change (the service is not running with this config)")
        return False
    cmd = ["systemctl", "restart", SERVICE]
    if os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        say(f"bridge: restart failed ({r.stderr.strip()}): sudo systemctl restart {SERVICE}")
        return False
    say("bridge restarted")
    return True


def set_live(cfg: Config, items: list[str], restart: bool = True, say=print, hint: bool = True) -> bool:
    """hint: end with the save/revert reminder (off when the caller saves right away)."""
    gw_changes, br_changes = parse(items)
    pending = read_pending(cfg.pending_file)

    br_diff = {k: (get_value(cfg, k), v) for k, v in br_changes.items() if get_value(cfg, k) != v}
    if br_diff:  # check the result is a valid config before touching anything
        trial = copy.deepcopy(cfg)
        for k, (_, new) in br_diff.items():
            set_value(trial, k, new)
        try:
            validate(trial)
        except ConfigError as e:
            raise ChangeError(str(e)) from None

    written = False
    if gw_changes:
        gw = Gateway.from_config(cfg)
        for page, changes in gw_changes.items():
            gw.check_fields(page, changes)
            current = gw.form(page)[0]
            diff = {f: (current[f], str(v)) for f, v in changes.items() if current[f] != str(v)}
            if not diff:
                continue
            gw.write(page, {f: new for f, (_, new) in diff.items()})
            written = True
            page_entries = pending["gateway"].setdefault(page, {})
            for f, (old, new) in diff.items():
                _record(page_entries, f, old, new)
            for line in change_lines(page, diff, current):
                say(f"  {line}")
            write_pending(cfg.pending_file, pending)  # after each page: a failure later keeps the record
        if written and restart:
            say(f"gateway restarted, back after {gw.restart():.0f} s")
        elif written:
            say("gateway: saved on the device, active after its next restart (wscan gw restart)")

    for k, (old, new) in br_diff.items():
        _record(pending["bridge"], k, old, new)
        say(f"  {k}: {old} -> {new}")
    write_pending(cfg.pending_file, pending)
    if br_diff:
        restart_bridge(load(str(cfg.source) if cfg.source else None), say)

    if not written and not br_diff:
        say("nothing to change")
        return False
    if hint:
        say("not saved yet: `wscan save` keeps it in the config, `wscan revert` undoes it")
    return True


def pending_lines(cfg: Config, gw: Gateway | None = None) -> list[str]:
    """With a gateway to read the current values from, filter changes show as one line."""
    p = read_pending(cfg.pending_file)
    lines = []
    for page, fields in p["gateway"].items():
        current = None
        if gw is not None:
            try:  # values now = the new ones; put the old ones back to describe the "before"
                current = {**gw.form(page)[0], **{f: old for f, (old, _) in fields.items()}}
            except GatewayError:
                current = None
        lines += [f"  {line}  (gateway)" for line in change_lines(page, {f: tuple(v) for f, v in fields.items()}, current)]
    lines += [f"  {k}: {old} -> {new}  (bridge)" for k, (old, new) in p["bridge"].items()]
    return lines


def save(cfg: Config, say=print) -> bool:
    pending = read_pending(cfg.pending_file)
    if not pending["gateway"] and not pending["bridge"]:
        say("nothing to save")
        return False
    path = cfg.source or REPO_ROOT / "config.toml"
    if not path.exists():
        example = REPO_ROOT / "config.example.toml"
        path.write_text(example.read_text() if example.exists() else "")
        say(f"created {path}")
    original = path.read_text()
    lines = original.splitlines()
    base = load(str(path), with_pending=False)
    if not tomledit.has_channels(lines):  # defaults in use: write the channels out first
        for ch in base.channels:
            tomledit.add_channel(lines, ch.name, ch.port, ch.interface, ch.enabled)

    expected = {}
    for key, (_, new) in pending["bridge"].items():
        if key == "gateway.host":
            tomledit.set_section_key(lines, "gateway", "host", new)
        else:
            name, attr = key.split(".", 1)
            tomledit.set_channel_key(lines, name, attr, new)
        expected[key] = new
    for page, fields in pending["gateway"].items():
        for f, (_, new) in fields.items():
            if page in GATEWAY_CHANNELS:
                if f == "localport":  # follows the channel port, enforced by gw apply
                    continue
                tomledit.set_channel_gateway(lines, page, f, new)
            elif page in GATEWAY_PAGES:
                tomledit.set_section_key(lines, f"gateway.{page}", f, new)
            expected[f"{page}:{f}"] = str(new)

    path.with_name(path.name + ".bak").write_text(original)
    path.write_text("\n".join(lines) + "\n")
    try:
        check = load(str(path), with_pending=False)
        for key, want in expected.items():
            if ":" in key:
                page, f = key.split(":")
                got = check.channel(page).gateway.get(f) if page in GATEWAY_CHANNELS \
                    else check.gateway_pages.get(page, {}).get(f)
            else:
                got = get_value(check, key)
            if got != want:
                raise ChangeError(f"{key}: wrote {want!r}, read back {got!r}")
    except (ConfigError, ChangeError) as e:
        path.write_text(original)
        raise ChangeError(f"could not save, {path} left as it was: {e}") from None

    write_pending(cfg.pending_file, {"gateway": {}, "bridge": {}})
    say(f"saved into {path} (previous version: {path.name}.bak)")
    return True


def revert(cfg: Config, restart: bool = True, say=print) -> bool:
    pending = read_pending(cfg.pending_file)
    if not pending["gateway"] and not pending["bridge"]:
        say("nothing to revert")
        return False
    if pending["gateway"]:
        gw = Gateway.from_config(cfg)
        for page, fields in pending["gateway"].items():
            current = gw.form(page)[0]
            gw.write(page, {f: old for f, (old, _) in fields.items()})
            for line in change_lines(page, {f: (new, old) for f, (old, new) in fields.items()}, current):
                say(f"  {line}")
        if restart:
            say(f"gateway restarted, back after {gw.restart():.0f} s")
    for k, (old, new) in pending["bridge"].items():
        say(f"  {k}: {new} -> {old}")
    write_pending(cfg.pending_file, {"gateway": {}, "bridge": {}})
    if pending["bridge"]:
        restart_bridge(load(str(cfg.source) if cfg.source else None), say)
    say("back to the saved configuration")
    return True
