"""wscan command line."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .codec import Deframer, encode, format_frame, parse_cansend
from .config import Config, ConfigError, load, password_path
from .gateway import PAGES, WRITABLE, Gateway, GatewayError, describe, wanted_from_config

SERVICE = "wscan-bridge"


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"{question} -> not confirmed (no terminal): add --yes")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes", "s", "si")


def _assignments(items: list[str]) -> dict:
    out = {}
    for item in items:
        k, sep, v = item.partition("=")
        if not sep:
            raise SystemExit(f"expected field=value, got '{item}'")
        out[k] = v
    return out


def _channel(cfg: Config, name: str):
    for ch in cfg.channels:
        if ch.name == name:
            return ch
    raise SystemExit(f"no channel '{name}' in the config")


# --- commands -------------------------------------------------------------------------

def cmd_config(cfg: Config, a) -> int:
    print(f"# loaded from: {cfg.source or 'built-in defaults (no config file found)'}")
    print(f"gateway   {cfg.host}  web :{cfg.http_port}  user {cfg.user}  password file {password_path(cfg)}")
    print(f"bridge    drop {cfg.flush_seconds} s of backlog on connect, keepalive "
          f"{cfg.keepalive_idle}+{cfg.keepalive_interval}x{cfg.keepalive_count} s")
    if cfg.network.address:
        print(f"network   {cfg.network.interface} = {cfg.network.address}")
    for ch in cfg.channels:
        extra = f"  gateway settings {ch.gateway}" if ch.gateway else ""
        print(f"channel   {ch.name}: {cfg.host}:{ch.port} <-> {ch.interface}"
              f"{'' if ch.enabled else '  (disabled)'}{extra}")
    return 0


def cmd_bridge(cfg: Config, a) -> int:
    from .bridge import run
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(cfg)


def cmd_vcan_up(cfg: Config, a) -> int:
    from .vcan import VcanError, vcan_up
    try:
        for line in vcan_up([c.interface for c in cfg.enabled_channels]):
            print(line)
    except (VcanError, subprocess.CalledProcessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_status(cfg: Config, a) -> int:
    from .bridge import DEFAULT_STATUS, status_path
    ok = True
    if shutil.which("systemctl"):
        state = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True).stdout.strip()
        print(f"service   {SERVICE}: {state}")
        ok &= state == "active"
    spath = status_path(cfg) or DEFAULT_STATUS
    try:
        doc = json.loads(spath.read_text())
        age = time.time() - doc["time"]
        print(f"bridge    status {spath} ({age:.0f} s old{', STALE' if age > 15 else ''})")
        ok &= age <= 15
        for s in doc["channels"]:
            since = datetime.fromtimestamp(s["since"]).strftime("%d/%m %H:%M:%S")
            last = f"{s['last_rx_age']} s ago" if s["last_rx_age"] is not None else "never"
            print(f"  {s['channel']} -> {s['interface']}  {'UP' if s['connected'] else 'DOWN'} since {since}"
                  f"  rx {s['rx']} tx {s['tx']}  last frame {last}"
                  f"  lost rx/tx {s['lost_rx']}/{s['lost_tx']}  reconnects {s['reconnects']}")
            ok &= s["connected"]
    except (OSError, ValueError, KeyError):
        print(f"bridge    no status file at {spath} (bridge not running as a service?)")
    try:
        with socket.create_connection((cfg.host, cfg.http_port), timeout=2):
            print(f"gateway   {cfg.host} answers")
    except OSError as e:
        print(f"gateway   {cfg.host} NOT answering ({e}): try `wscan net check`")
        ok = False
    return 0 if ok else 1


def cmd_discover(cfg: Config, a) -> int:
    from . import discover
    if a.arp:
        nets = a.net or ["192.168.0.0/24", "192.168.1.0/24"]
        print(f"ARP sweep on {a.arp}: {', '.join(nets)} ...")
        try:
            found = discover.arp_sweep(a.arp, nets)
        except PermissionError:
            print("needs root: sudo wscan discover --arp IFACE")
            return 1
        for ip, mac in found:
            print(f"  {ip:15}  {mac}")
        if not found:
            print("  nothing answered (gateway powered? cable on this interface?)")
        else:
            print("then: put that address in [gateway] host, give this host an address on the same\n"
                  "subnet in [network] and run `sudo wscan net apply`")
        return 0 if found else 1
    print("sweeping the local networks for CAN gateways (port 20001) ...")
    found, notes = discover.tcp_sweep(port=a.port)
    for n in notes:
        print(f"  {n}")
    for g in found:
        tag = "Waveshare/USR gateway" if g["match"] else "something"
        print(f"  {g['ip']:15}  {tag}  web: {g['web']}  data ports: {g['ports']}")
    if not found:
        print("  none found. If the gateway is on a subnet this host has no address on, try:\n"
              "  sudo wscan discover --arp eth0")
    return 0 if found else 1


def cmd_net(cfg: Config, a) -> int:
    from .netsetup import NetError, apply, check
    if a.net_cmd == "check":
        lines, ok = check(cfg)
        print("\n".join(lines))
        return 0 if ok else 1
    try:
        apply(cfg, rollback_seconds=a.rollback)
    except NetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_gw(cfg: Config, a) -> int:
    try:
        gw = Gateway.from_config(cfg)
        return _gw(gw, cfg, a)
    except GatewayError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _gw(gw: Gateway, cfg: Config, a) -> int:
    if a.gw_cmd == "status":
        for k, v in gw.status().items():
            print(f"  {k:12} {v}")
    elif a.gw_cmd == "show":
        for page in (PAGES if a.page == "all" else [a.page]):
            print(f"[{page}]")
            for field, val in gw.read(page).items():
                d = describe(page, field, val)
                print(f"  {field:18} {val}" + (f"   ({d})" if d else ""))
            print()
    elif a.gw_cmd == "backup":
        path = Path(a.file or f"gateway-backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        path.write_text(json.dumps(gw.backup(), indent=2, ensure_ascii=False))
        print(f"saved {path}")
    elif a.gw_cmd == "set":
        diff = gw.diff(a.page, _assignments(a.assign))
        if not diff:
            print("nothing to change")
            return 0
        for f, (old, new) in diff.items():
            print(f"  {a.page}.{f}: {old} -> {new}")
        if a.dry_run or not _confirm("write to the gateway?", a.yes):
            return 0
        gw.write(a.page, {f: new for f, (_, new) in diff.items()})
        print("saved and verified")
        if a.restart:
            print(f"restarted, back after {gw.restart():.0f} s")
        else:
            print("takes effect at the next gateway restart (wscan gw restart, or --restart)")
    elif a.gw_cmd == "apply":
        plan = gw.plan(wanted_from_config(cfg))
        if not plan:
            print(f"gateway already matches {cfg.source or 'the defaults'}")
            return 0
        for page, d in plan.items():
            for f, (old, new) in d.items():
                print(f"  {page}.{f}: {old} -> {new}")
        if a.dry_run or not _confirm("apply to the gateway and restart it?", a.yes):
            return 0
        gw.apply(wanted_from_config(cfg), restart=not a.no_restart)
        print("applied and verified" + ("" if a.no_restart else ", gateway restarted"))
    elif a.gw_cmd == "restart":
        if _confirm("restart the gateway?", a.yes):
            print(f"back after {gw.restart():.0f} s")
    return 0


def _data_socket(cfg: Config, name: str) -> socket.socket:
    ch = _channel(cfg, name)
    return socket.create_connection((cfg.host, ch.port), timeout=3)


def cmd_dump(cfg: Config, a) -> int:
    """Frames straight from the gateway's TCP port, no bridge needed (works on any OS)."""
    s = _data_socket(cfg, a.channel)
    s.settimeout(0.2)
    deframer, t0, n = Deframer(), time.time(), 0
    try:
        while not a.seconds or time.time() - t0 < a.seconds:
            try:
                data = s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                print("closed by the gateway")
                return 1
            for f in deframer.feed(data):
                n += 1
                print(f"{time.time() - t0:9.3f}  {a.channel}  {format_frame(f)}")
    except KeyboardInterrupt:
        pass
    print(f"-- {n} frames", file=sys.stderr)
    return 0


def cmd_send(cfg: Config, a) -> int:
    s = _data_socket(cfg, a.channel)
    for item in a.frames:
        f = parse_cansend(item)
        s.sendall(encode(f))
        print(f"sent  {a.channel}  {format_frame(f)}")
    time.sleep(0.2)  # let the gateway take it before the connection closes
    return 0


# --- parser ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wscan", description="SocketCAN bridge and tools for the "
                                 "Waveshare 2-CH-CAN-TO-ETH (USR-CANET200 family).")
    ap.add_argument("--config", help="config file (default: $WSCAN_CONFIG, <repo>/config.toml, "
                    "~/.config/wscan/config.toml, /etc/wscan/config.toml)")
    ap.add_argument("--version", action="version", version=f"wscan {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    sub.add_parser("config", help="show the effective configuration and where it came from")
    sub.add_parser("status", help="bridge service, channels and gateway at a glance")
    sub.add_parser("bridge", help="run the bridge in the foreground (what the service runs)")
    sub.add_parser("vcan-up", help="create the CAN interfaces named in the config (root)")

    d = sub.add_parser("discover", help="find gateways on the local networks")
    d.add_argument("--arp", metavar="IFACE", help="ARP sweep on an interface with no address on the "
                   "gateway's subnet (root)")
    d.add_argument("--net", action="append", metavar="CIDR", help="networks for --arp "
                   "(default 192.168.0.0/24 and 192.168.1.0/24)")
    d.add_argument("--port", type=int, default=20001, help="data port to look for (default 20001)")

    n = sub.add_parser("net", help="this host's address on the gateway's network")
    nsub = n.add_subparsers(dest="net_cmd", required=True)
    nsub.add_parser("check", help="how this host reaches the gateway")
    na = nsub.add_parser("apply", help="set [network] address via NetworkManager, with rollback (root)")
    na.add_argument("--rollback", type=int, default=120, metavar="S", help="rollback delay (default 120 s)")

    g = sub.add_parser("gw", help="gateway settings (web pages; needs the gateway password)")
    gsub = g.add_subparsers(dest="gw_cmd", required=True)
    gsub.add_parser("status", help="model, firmware, uptime")
    gs = gsub.add_parser("show", help="current settings")
    gs.add_argument("page", nargs="?", default="all", choices=list(PAGES) + ["all"])
    gb = gsub.add_parser("backup", help="save every setting to a JSON file")
    gb.add_argument("file", nargs="?")
    gset = gsub.add_parser("set", help="change fields of one page: wscan gw set can1 baud=500000 --restart")
    gset.add_argument("page", choices=WRITABLE)
    gset.add_argument("assign", nargs="+", metavar="field=value")
    gset.add_argument("--restart", action="store_true", help="restart the gateway so it takes effect")
    gset.add_argument("--dry-run", action="store_true")
    gset.add_argument("--yes", action="store_true")
    gap = gsub.add_parser("apply", help="make the gateway match the config ([[channel]] port, "
                          "[channel.gateway] fields), then restart it")
    gap.add_argument("--dry-run", action="store_true")
    gap.add_argument("--yes", action="store_true")
    gap.add_argument("--no-restart", action="store_true")
    gr = gsub.add_parser("restart", help="restart the gateway")
    gr.add_argument("--yes", action="store_true")

    du = sub.add_parser("dump", help="print frames straight from the gateway (no bridge needed)")
    du.add_argument("--channel", default="can1", choices=["can1", "can2"])
    du.add_argument("--seconds", type=float, default=0, help="stop after N seconds (default: Ctrl-C)")
    se = sub.add_parser("send", help="send frames straight to the gateway: wscan send 123#DEADBEEF")
    se.add_argument("frames", nargs="+", metavar="ID#DATA")
    se.add_argument("--channel", default="can1", choices=["can1", "can2"])
    return ap


COMMANDS = {"config": cmd_config, "status": cmd_status, "bridge": cmd_bridge, "vcan-up": cmd_vcan_up,
            "discover": cmd_discover, "net": cmd_net, "gw": cmd_gw, "dump": cmd_dump, "send": cmd_send}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        cfg = load(a.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    try:
        return COMMANDS[a.cmd](cfg, a)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
