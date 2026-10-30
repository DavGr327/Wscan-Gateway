"""`wscan setup`: the guided configuration, also started by install.sh.

Every question shows a default in brackets (the current value, or the factory one): Enter
keeps it. At the end it writes config.toml, saves the gateway password, gives this device
an address on the gateway's network if needed, writes the channel settings into the gateway
and restarts the bridge.
"""
from __future__ import annotations

import getpass
import ipaddress
import json
import os
import re
import socket
import subprocess
from pathlib import Path

from . import tomledit
from .changes import restart_bridge
from .config import REPO_ROOT, Config, ConfigError, load, password_path
from .friendly import check_bitrate
from .gateway import Gateway, GatewayError, wanted_from_config, write_password

IFNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


class SetupError(Exception):
    pass


class Prompter:
    def __init__(self, ask=input, secret=getpass.getpass, say=print):
        self.ask, self.secret, self.say = ask, secret, say

    def text(self, question: str, default, check=None) -> str:
        while True:
            answer = self.ask(f"{question} [{default}]: ").strip() or str(default)
            if check is None:
                return answer
            try:
                return check(answer)
            except ValueError as e:
                self.say(f"  {e}")

    def yes(self, question: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            a = self.ask(f"{question} [{hint}]: ").strip().lower()
            if not a:
                return default
            if a in ("y", "yes", "s", "si"):
                return True
            if a in ("n", "no"):
                return False

    def choice(self, question: str, options: list[str], default: int) -> int:
        self.say(question)
        for i, o in enumerate(options, 1):
            self.say(f"  {i}) {o}")

        def check(a):
            if a not in [str(i) for i in range(1, len(options) + 1)]:
                raise ValueError(f"answer 1-{len(options)}")
            return a
        return int(self.text("choice", default, check))


# --- checks and guesses ---------------------------------------------------------------

def _ip(a: str) -> str:
    return str(ipaddress.IPv4Address(a))


def _port_name(a: str) -> str:
    if not IFNAME_RE.match(a):
        raise ValueError("not a network port name (e.g. eth0)")
    return a


def _address_for(host: str):
    def check(a: str) -> str:
        if "/" not in a:
            a += "/24"
        iface = ipaddress.IPv4Interface(a)
        if ipaddress.IPv4Address(host) not in iface.network:
            raise ValueError(f"must be on the gateway's network ({host}), e.g. {_suggest_address(host)}")
        if str(iface.ip) == host:
            raise ValueError("that is the gateway's own address")
        return str(iface)
    return check


def _suggest_address(host: str) -> str:
    net = ipaddress.IPv4Interface(f"{host}/24").network
    for last in (10, 11, 20, 50):
        ip = net.network_address + last
        if str(ip) != host:
            return f"{ip}/24"
    return f"{net.network_address + 2}/24"


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ethernet_ports() -> list[str]:
    try:
        out = subprocess.run(["ip", "-j", "link"], capture_output=True, text=True).stdout
        return [link["ifname"] for link in json.loads(out) if link["ifname"].startswith(("eth", "en"))]
    except (OSError, ValueError, KeyError):
        return []


def _local_iface_for(host: str) -> str | None:
    from .discover import local_networks
    try:
        return next((n for n, i in local_networks() if ipaddress.IPv4Address(host) in i.network), None)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _discover() -> list[str]:
    from .discover import tcp_sweep
    try:
        return [g["ip"] for g in tcp_sweep()[0] if g["match"]]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _sudo_net_apply(path: Path) -> int:
    cmd = [str(REPO_ROOT / "wscan"), "--config", str(path), "net", "apply"]
    if os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    return subprocess.run(cmd).returncode


# --- the questions --------------------------------------------------------------------

def ask_all(base: Config, p: Prompter, discover=_discover) -> dict:
    p.say("wscan setup: answer the questions, Enter keeps the value in [brackets].\n")

    default_host = base.host
    if not _reachable(default_host, base.http_port):
        p.say("looking for the gateway on this device's networks ...")
        found = discover()
        if found:
            default_host = found[0]
            p.say(f"  found: {', '.join(found)}")
    host = p.text("Gateway address", default_host, _ip)

    local = _local_iface_for(host)
    default_mode = 2 if base.network.address or not local or local == base.network.interface else 1
    mode = p.choice("\nHow is the gateway connected?",
                    ["to the same network as this device (switch / router)",
                     "with a cable to a free Ethernet port of this device"], default_mode)
    network = None
    if mode == 2:
        ports = _ethernet_ports()
        iface = p.text("Ethernet port", base.network.interface or (ports[0] if ports else "eth0"), _port_name)
        check = _address_for(host)
        try:
            addr_default = check(base.network.address) if base.network.address else _suggest_address(host)
        except ValueError:
            addr_default = _suggest_address(host)
        network = (iface, p.text("This device's address on that port", addr_default, check))

    p.say("")
    password = None
    has_saved = bool(os.environ.get("WSCAN_PASSWORD")) or password_path(base).is_file()
    if not (has_saved and p.yes("Gateway web password: keep the saved one?", True)):
        password = p.secret("Gateway web password (Enter = admin, the factory one): ") or "admin"

    channels, used = {}, set()
    for ch in base.channels:
        label = ch.name.upper()
        p.say("")
        if not p.yes(f"Use {label}?", ch.enabled):
            channels[ch.name] = (False, ch.interface, None)
            continue

        def ifname_check(a, label=label):
            if not IFNAME_RE.match(a):
                raise ValueError("1-15 letters, digits, - _ .")
            if a in used:
                raise ValueError("already used by the other channel")
            return a
        ifname = p.text(f"  {label}: interface name on this device", ch.interface, ifname_check)
        used.add(ifname)
        bitrate = p.text(f"  {label}: bus bit rate", ch.gateway.get("baud", "1000000"), check_bitrate)
        channels[ch.name] = (True, ifname, bitrate)

    return {"host": host, "network": network, "password": password, "channels": channels}


def write_config(base: Config, answers: dict) -> Path:
    path = base.source or REPO_ROOT / "config.toml"
    existed = path.exists()
    text = path.read_text() if existed else (REPO_ROOT / "config.example.toml").read_text()
    lines = text.splitlines()
    tomledit.set_section_key(lines, "gateway", "host", answers["host"])
    if answers["network"]:
        iface, address = answers["network"]
        tomledit.set_section_key(lines, "network", "interface", iface)
        tomledit.set_section_key(lines, "network", "address", address)
    else:
        tomledit.remove_section(lines, "network")
    if not tomledit.has_channels(lines):
        for ch in base.channels:
            tomledit.add_channel(lines, ch.name, ch.port, ch.interface, ch.enabled)
    for name, (on, ifname, bitrate) in answers["channels"].items():
        tomledit.set_channel_key(lines, name, "enabled", on)
        tomledit.set_channel_key(lines, name, "interface", ifname)
        if bitrate:
            tomledit.set_channel_gateway(lines, name, "baud", bitrate)
    if existed:
        path.with_name(path.name + ".bak").write_text(text)
    path.write_text("\n".join(lines) + "\n")
    try:
        load(str(path), with_pending=False)
    except ConfigError as e:
        if existed:
            path.write_text(text)
        else:
            path.unlink()
        raise SetupError(f"the answers do not make a valid config ({e}): nothing saved") from None
    return path


def run(cfg: Config, p: Prompter, discover=_discover, net_apply=_sudo_net_apply) -> int:
    if cfg.unsaved:
        raise SetupError("there are changes tried with `wscan set` and not saved: "
                         "`wscan save` or `wscan revert` first")
    base = load(str(cfg.source), with_pending=False) if cfg.source else cfg
    answers = ask_all(base, p, discover)

    p.say("\nsummary")
    p.say(f"  gateway        {answers['host']}")
    if answers["network"]:
        p.say(f"  this device    {answers['network'][1]} on {answers['network'][0]}")
    else:
        p.say("  this device    already on the gateway's network")
    for name, (on, ifname, bitrate) in answers["channels"].items():
        p.say(f"  {name.upper():<14} " + (f"{ifname}, {bitrate} bit/s" if on else "not used"))
    if not p.yes("\nSave and apply?", True):
        p.say("nothing changed")
        return 1

    path = write_config(base, answers)
    new = load(str(path), with_pending=False)
    p.say(f"saved {path}")
    if answers["password"] is not None:
        p.say(f"password saved in {write_password(new, answers['password'])} (only you can read it)")

    if answers["network"]:
        p.say("setting this device's network address ...")
        if net_apply(path) != 0:
            p.say("network not set: check the cable and run `sudo wscan net apply`")

    if _reachable(new.host, new.http_port, 2):
        try:
            gw = Gateway.from_config(new, prompt=False)
            wanted = wanted_from_config(new)
            if gw.plan(wanted):
                p.say("writing the channel settings into the gateway (it restarts, a few seconds) ...")
                gw.apply(wanted)
            p.say("gateway set")
        except GatewayError as e:
            p.say(f"gateway not set ({e}): run `wscan gw apply` once it is fixed")
    else:
        p.say(f"gateway {new.host} not reachable now: when it is, run `wscan gw apply`")

    restart_bridge(new, p.say, quiet_if_missing=True)
    p.say("\ndone. From now on:\n"
          "  wscan show                       everything at a glance\n"
          "  wscan set can1.bitrate=500000    change something (try it)\n"
          "  wscan save | wscan revert        keep it | undo it\n"
          "  wscan status                     is it working?\n"
          "  wscan setup                      these questions again")
    return 0
