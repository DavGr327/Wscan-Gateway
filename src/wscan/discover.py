"""Finding gateways.

tcp_sweep: on the networks this host already has an address on, looks for the CAN data
port. No root needed.
arp_sweep: on an interface with no address on the gateway's network (a gateway fresh from
the factory sits on 192.168.0.7) it asks every address by ARP, sender 0.0.0.0, without
touching the network configuration. Needs root (raw socket).
"""
from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import json
import select
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request

FINGERPRINT_REALM = "DSP_CLIENT_DEMO_AUTHENTICATE1"  # Basic auth realm of the web pages
DATA_PORTS = (20001, 20005, 20003)
MAX_PREFIX = 22  # networks bigger than /22 are not swept


def local_networks() -> list[tuple[str, ipaddress.IPv4Interface]]:
    out = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, check=True).stdout
    nets = []
    for link in json.loads(out):
        name = link.get("ifname", "")
        if name == "lo" or name.startswith(("docker", "br-", "veth", "virbr")):
            continue
        for a in link.get("addr_info", []):
            nets.append((name, ipaddress.IPv4Interface(f"{a['local']}/{a['prefixlen']}")))
    return nets


def _open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def identify(ip: str, http_port: int = 80, timeout: float = 2) -> dict:
    """What answers at ip: web page realm (fingerprint) and open data ports."""
    info = {"ip": ip, "web": None, "match": False}
    try:
        urllib.request.urlopen(f"http://{ip}:{http_port}/", timeout=timeout)
        info["web"] = "open (no login)"
    except urllib.error.HTTPError as e:
        realm = e.headers.get("WWW-Authenticate", "")
        info["web"] = f"HTTP {e.code} {realm}".strip()
        info["match"] = FINGERPRINT_REALM in realm
    except OSError:
        pass
    info["ports"] = [p for p in DATA_PORTS if _open(ip, p, timeout)]
    return info


def tcp_sweep(port: int = 20001, timeout: float = 0.4, workers: int = 64,
              nets: list | None = None) -> tuple[list[dict], list[str]]:
    """-> (gateways found, notes about networks that were skipped)"""
    notes, targets = [], []
    for name, iface in (nets if nets is not None else local_networks()):
        net = iface.network
        if net.prefixlen < MAX_PREFIX:
            notes.append(f"{name} {net}: too big to sweep, skipped")
            continue
        targets += [str(h) for h in net.hosts() if h != iface.ip]
    with cf.ThreadPoolExecutor(workers) as ex:
        hits = [ip for ip, ok in zip(targets, ex.map(lambda ip: _open(ip, port, timeout), targets)) if ok]
    return [identify(ip) for ip in hits], notes


def arp_sweep(ifname: str, networks: list[str], wait: float = 2.0) -> list[tuple[str, str]]:
    """-> [(ip, mac)] of everything that answers on ifname (root)."""
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
    s.bind((ifname, 0))
    mac = s.getsockname()[4]
    for cidr in networks:
        for host in ipaddress.IPv4Network(cidr, strict=False).hosts():
            pkt = (b"\xff" * 6 + mac + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
                   + mac + b"\x00" * 4 + b"\x00" * 6 + socket.inet_aton(str(host)))
            s.send(pkt.ljust(60, b"\x00"))
            time.sleep(0.001)
    found, end = {}, time.monotonic() + wait
    while (left := end - time.monotonic()) > 0:
        if not select.select([s], [], [], left)[0]:
            continue
        pkt = s.recv(2048)
        if len(pkt) >= 42 and pkt[12:14] == b"\x08\x06" and struct.unpack("!H", pkt[20:22])[0] == 2:
            found[socket.inet_ntoa(pkt[28:32])] = ":".join(f"{b:02x}" for b in pkt[22:28])
    s.close()
    return sorted(found.items(), key=lambda kv: ipaddress.IPv4Address(kv[0]))
