"""This host's address on the gateway's network.

check(): how this host reaches the gateway, plus a warning when two interfaces sit on the
same subnet: both then answer ARP for this host's addresses ("ARP flux") and TCP turns
intermittent while ping keeps working.

apply(): gives [network] interface the [network] address through NetworkManager, with no
default gateway and never as default route. A rollback is armed first: if afterwards this
host has lost its default route or the gateway does not answer, the old profile comes back
by itself after `rollback_seconds`.
"""
from __future__ import annotations

import ipaddress
import json
import os
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .config import Config
from .discover import local_networks

ROLLBACK_UNIT = "wscan-net-rollback"
NM_KEYS = ("ipv4.method", "ipv4.addresses", "ipv4.gateway", "ipv4.never-default",
           "ipv4.route-metric", "ipv6.method")


class NetError(Exception):
    pass


def _run(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise NetError(f"{shlex.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def route_to(host: str) -> dict | None:
    try:
        return json.loads(_run("ip", "-j", "route", "get", host))[0]
    except (NetError, IndexError, json.JSONDecodeError):
        return None


def default_route() -> tuple[str, str] | None:
    routes = json.loads(_run("ip", "-j", "route", "show", "default") or "[]")
    return (routes[0].get("dev", ""), routes[0].get("gateway", "")) if routes else None


def _reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def shared_subnets() -> list[str]:
    seen: dict = {}
    for name, iface in local_networks():
        seen.setdefault(iface.network, []).append(name)
    return [f"{net} is on {', '.join(names)}: ARP flux risk, keep each subnet on one interface"
            for net, names in seen.items() if len(set(names)) > 1]


def check(cfg: Config) -> tuple[list[str], bool]:
    lines, ok = [], True
    r = route_to(cfg.host)
    if r is None:
        lines.append(f"no route to {cfg.host}")
        ok = False
    elif r.get("gateway"):
        lines.append(f"{cfg.host} reached through router {r['gateway']} on {r['dev']} (not on a local subnet)")
    else:
        lines.append(f"{cfg.host} is on the local subnet of {r['dev']}, this host is {r.get('prefsrc', '?')}")
    web = _reachable(cfg.host, cfg.http_port)
    lines.append(f"web page :{cfg.http_port} {'answers' if web else 'NOT answering'}")
    for ch in cfg.enabled_channels:
        up = _reachable(cfg.host, ch.port)
        ok &= up
        lines.append(f"{ch.name} data port :{ch.port} {'answers' if up else 'NOT answering'}")
    for w in shared_subnets():
        lines.append(f"warning: {w}")
    if cfg.network.address:
        have = any(n == cfg.network.interface and str(i) == cfg.network.address for n, i in local_networks())
        lines.append(f"[network] {cfg.network.address} on {cfg.network.interface}: "
                     f"{'present' if have else 'not set yet (sudo wscan net apply)'}")
    return lines, ok


# --- NetworkManager -------------------------------------------------------------------

def _nm_connection_for(ifname: str) -> str | None:
    for line in _run("nmcli", "-t", "-f", "UUID,DEVICE", "con", "show", "--active").splitlines():
        uuid, _, dev = line.partition(":")
        if dev == ifname:
            return uuid
    for line in _run("nmcli", "-t", "-f", "UUID", "con", "show").splitlines():
        if _run("nmcli", "-g", "connection.interface-name", "con", "show", line) == ifname:
            return line
    return None


def _nm_get(uuid: str) -> dict:
    values = _run("nmcli", "-g", ",".join(NM_KEYS), "con", "show", uuid).split("\n")
    return dict(zip(NM_KEYS, values + [""] * (len(NM_KEYS) - len(values))))


def _carrier(ifname: str) -> bool:
    try:
        return Path(f"/sys/class/net/{ifname}/carrier").read_text().strip() == "1"
    except OSError:
        return False


def apply(cfg: Config, rollback_seconds: int = 120, say=print) -> None:
    net = cfg.network
    if not net.address:
        raise NetError("no [network] section in the config: nothing to apply")
    if os.geteuid() != 0:
        raise NetError("needs root: sudo wscan net apply")
    if not shutil.which("nmcli"):
        raise NetError("NetworkManager (nmcli) not found: give "
                       f"{net.interface} the address {net.address} with your system's network tool")
    addr = ipaddress.IPv4Interface(net.address)
    if ipaddress.IPv4Address(cfg.host) not in addr.network:
        raise NetError(f"gateway {cfg.host} is not inside {addr.network}: check [gateway] host and [network] address")
    for name, iface in local_networks():
        if name != net.interface and iface.network.overlaps(addr.network):
            raise NetError(f"{name} is already on {iface.network}: a second interface on the same subnet "
                           "causes ARP flux; give the gateway a different subnet")

    uuid = _nm_connection_for(net.interface)
    if uuid:
        old = _nm_get(uuid)
        if (old["ipv4.method"] == "manual" and old["ipv4.addresses"] == str(addr)
                and old["ipv4.never-default"] == "yes"):
            say(f"{net.interface} already has {addr} (NetworkManager connection {uuid})")
            return
        restore = ["nmcli", "con", "mod", uuid]
        for k in NM_KEYS:
            restore += [k, old[k]]
        rollback = f"{shlex.join(restore)}; nmcli con up {uuid}"
    else:
        uuid = _run("nmcli", "-t", "con", "add", "type", "ethernet", "ifname", net.interface,
                    "con-name", f"wscan-{net.interface}", "ipv4.method", "disabled", "ipv6.method", "disabled")
        uuid = _nm_connection_for(net.interface) or uuid.split("(")[-1].rstrip(") .")
        rollback = f"nmcli con delete {uuid}"
        say(f"created NetworkManager connection wscan-{net.interface}")

    _run("nmcli", "con", "mod", uuid, "ipv4.method", "manual", "ipv4.addresses", str(addr),
         "ipv4.gateway", "", "ipv4.never-default", "yes", "ipv4.route-metric", "600", "ipv6.method", "disabled")
    if not _carrier(net.interface):
        say(f"{net.interface} has no link: profile saved, it activates when the cable is plugged in")
        return

    before = default_route()
    _run("systemctl", "reset-failed", f"{ROLLBACK_UNIT}.timer", f"{ROLLBACK_UNIT}.service", check=False)
    _run("systemd-run", f"--unit={ROLLBACK_UNIT}", f"--on-active={rollback_seconds}", "/bin/sh", "-c", rollback)
    say(f"rollback armed: the previous setup comes back in {rollback_seconds} s unless verified")
    _run("nmcli", "con", "up", uuid, check=False)

    t_end = time.monotonic() + 20
    while time.monotonic() < t_end:
        r = route_to(cfg.host) or {}
        if default_route() == before and r.get("dev") == net.interface and _reachable(cfg.host, cfg.http_port):
            _run("systemctl", "stop", f"{ROLLBACK_UNIT}.timer", check=False)
            say(f"{net.interface} = {addr}, gateway {cfg.host} answers, default route unchanged: rollback disarmed")
            return
        time.sleep(1)
    raise NetError(f"not verified (default route {default_route()} vs {before}, gateway reachable: "
                   f"{_reachable(cfg.host, cfg.http_port)}): the previous setup comes back by itself "
                   f"within {rollback_seconds} s")
