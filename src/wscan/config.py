"""Configuration: one TOML file; anything left out takes the default.

The file is looked up, in order, at: --config, $WSCAN_CONFIG, <repo>/config.toml,
~/.config/wscan/config.toml, /etc/wscan/config.toml. With no file at all the defaults
match a gateway fresh from the factory (192.168.0.7, CAN1 on 20001, CAN2 on 20005).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_CHANNELS = ("can1", "can2")
SEARCH_PATHS = (
    REPO_ROOT / "config.toml",
    Path("~/.config/wscan/config.toml").expanduser(),
    Path("/etc/wscan/config.toml"),
)


class ConfigError(Exception):
    pass


@dataclass
class Channel:
    name: str  # gateway channel: can1 | can2
    port: int  # gateway TCP port of that channel
    interface: str  # SocketCAN interface on this host
    enabled: bool = True
    gateway: dict = field(default_factory=dict)  # gateway settings enforced by `wscan gw apply`


@dataclass
class Network:
    interface: str = ""
    address: str = ""  # this host's address on the gateway subnet, CIDR: 192.168.0.10/24


@dataclass
class Config:
    host: str = "192.168.0.7"
    http_port: int = 80
    user: str = "admin"
    password_file: str = "~/.config/wscan/auth"
    flush_seconds: float = 0.3
    keepalive_idle: int = 4
    keepalive_interval: int = 2
    keepalive_count: int = 3
    status_file: str = ""
    network: Network = field(default_factory=Network)
    channels: list[Channel] = field(default_factory=lambda: [
        Channel("can1", 20001, "vcan0"),
        Channel("can2", 20005, "vcan1"),
    ])
    source: Path | None = None

    @property
    def enabled_channels(self) -> list[Channel]:
        return [c for c in self.channels if c.enabled]


def find(explicit: str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        return p
    if os.environ.get("WSCAN_CONFIG"):
        return find(os.environ["WSCAN_CONFIG"])
    return next((p for p in SEARCH_PATHS if p.is_file()), None)


def _take(table: dict, where: str, allowed: dict) -> dict:
    """Copies the known keys, checking their type; an unknown key is almost always a typo."""
    out = {}
    for key, value in table.items():
        if key not in allowed:
            raise ConfigError(f"[{where}] unknown key '{key}' (known: {', '.join(allowed)})")
        typ = allowed[key]
        if typ is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if typ is not None and not isinstance(value, typ):
            raise ConfigError(f"[{where}] '{key}' must be {typ.__name__}, got {value!r}")
        out[key] = value
    return out


def load(explicit: str | None = None) -> Config:
    path = find(explicit)
    cfg = Config(source=path)
    if path is None:
        return cfg
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None

    unknown = set(data) - {"gateway", "bridge", "network", "channel"}
    if unknown:
        raise ConfigError(f"{path}: unknown section(s) {sorted(unknown)}")

    gw = _take(data.get("gateway", {}), "gateway",
               {"host": str, "http_port": int, "user": str, "password_file": str})
    br = _take(data.get("bridge", {}), "bridge",
               {"flush_seconds": float, "keepalive_idle": int, "keepalive_interval": int,
                "keepalive_count": int, "status_file": str})
    for k, v in {**gw, **br}.items():
        setattr(cfg, k, v)
    cfg.network = Network(**_take(data.get("network", {}), "network", {"interface": str, "address": str}))

    if "channel" in data:
        cfg.channels = []
        for i, raw in enumerate(data["channel"]):
            ch = _take(raw, f"channel #{i + 1}",
                       {"name": str, "port": int, "interface": str, "enabled": bool, "gateway": dict})
            missing = {"name", "port", "interface"} - set(ch)
            if missing:
                raise ConfigError(f"[[channel]] #{i + 1}: missing {sorted(missing)}")
            cfg.channels.append(Channel(**ch))
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    where = cfg.source or "defaults"
    if not cfg.host:
        raise ConfigError(f"{where}: [gateway] host is empty")
    names, ifaces = set(), set()
    for ch in cfg.channels:
        if ch.name not in GATEWAY_CHANNELS:
            raise ConfigError(f"{where}: channel name '{ch.name}' must be one of {GATEWAY_CHANNELS}")
        if not 0 < ch.port < 65536:
            raise ConfigError(f"{where}: {ch.name}: port {ch.port} out of range")
        if not ch.interface or len(ch.interface) > 15:
            raise ConfigError(f"{where}: {ch.name}: interface name must be 1-15 characters")
        if ch.name in names or ch.interface in ifaces:
            raise ConfigError(f"{where}: {ch.name}/{ch.interface} used twice")
        names.add(ch.name)
        ifaces.add(ch.interface)
        ch.gateway = {k: str(v) for k, v in ch.gateway.items()}
    if bool(cfg.network.interface) != bool(cfg.network.address):
        raise ConfigError(f"{where}: [network] needs both 'interface' and 'address'")
    if cfg.network.address:
        try:
            iface = ipaddress.IPv4Interface(cfg.network.address)
        except ValueError:
            raise ConfigError(f"{where}: [network] address must look like 192.168.0.10/24") from None
        if iface.network.prefixlen == 32:
            raise ConfigError(f"{where}: [network] address needs a prefix length, e.g. /24")


def password_path(cfg: Config) -> Path:
    return Path(cfg.password_file).expanduser()
