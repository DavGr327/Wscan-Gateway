"""SocketCAN bridge: every enabled gateway channel becomes a CAN interface on this host.

Frames from the gateway are written to the interface; frames that other programs write to
the interface (cansend, python-can, CODESYS...) go to the gateway and out on the bus.
Each channel reconnects by itself (gateway restart, cable unplugged) and on connect drops
the backlog the gateway piles up while nobody is connected.
"""
from __future__ import annotations

import json
import logging
import os
import select
import signal
import socket
import threading
import time
from pathlib import Path

from .codec import CAN_FRAME_SIZE, FRAME_LEN, Deframer, encode, from_can_frame, to_can_frame
from .config import Channel, Config

log = logging.getLogger("wscan.bridge")
STATUS_EVERY, STATS_EVERY = 5, 300
DEFAULT_STATUS = Path("/run/wscan/status.json")


def tcp_connect(host: str, port: int, cfg: Config) -> socket.socket:
    s = socket.create_connection((host, port), timeout=3)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # a rebooting gateway vanishes without closing the connection: keepalive notices it
    # within idle + interval * count seconds even when the bus is silent
    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, cfg.keepalive_idle)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, cfg.keepalive_interval)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, cfg.keepalive_count)
    s.settimeout(None)
    return s


def drop_backlog(s: socket.socket, seconds: float) -> int:
    s.settimeout(0.05)
    end, n = time.monotonic() + seconds, 0
    while time.monotonic() < end:
        try:
            d = s.recv(65536)
        except socket.timeout:
            continue
        if not d:
            raise ConnectionError("closed by the gateway")
        n += len(d)
    s.settimeout(None)
    return n // FRAME_LEN


class Link(threading.Thread):
    def __init__(self, ch: Channel, cfg: Config):
        super().__init__(name=ch.name, daemon=True)
        self.ch, self.cfg = ch, cfg
        self.rx = self.tx = self.lost_rx = self.lost_tx = self.reconnects = 0
        self.connected, self.since, self.last_rx = False, time.time(), 0.0
        # bind here, in the main thread: if the interface is missing the bridge stops at once
        self.cs = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.cs.bind((ch.interface,))

    def snapshot(self) -> dict:
        return {"channel": self.ch.name, "interface": self.ch.interface, "port": self.ch.port,
                "connected": self.connected, "since": round(self.since), "rx": self.rx, "tx": self.tx,
                "lost_rx": self.lost_rx, "lost_tx": self.lost_tx, "reconnects": self.reconnects,
                "last_rx_age": round(time.time() - self.last_rx, 1) if self.last_rx else None}

    def _drain_interface(self, seconds: float) -> None:
        # gateway unreachable: what programs write to the interface cannot go anywhere
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if select.select([self.cs], [], [], left)[0]:
                self.cs.recv(CAN_FRAME_SIZE)
                self.lost_tx += 1

    def _state(self, up: bool) -> None:
        self.connected, self.since = up, time.time()

    def run(self) -> None:
        ch, ts, deframer, backoff, last_err = self.ch, None, Deframer(), 1, None
        while True:
            if ts is None:
                try:
                    ts = tcp_connect(self.cfg.host, ch.port, self.cfg)
                    old = drop_backlog(ts, self.cfg.flush_seconds)
                except OSError as e:
                    if ts:
                        ts.close()
                        ts = None
                    if str(e) != last_err:
                        log.warning("%s: gateway %s:%d unreachable (%s), retrying",
                                    ch.name, self.cfg.host, ch.port, e)
                        last_err = str(e)
                    self._drain_interface(backoff)
                    backoff = min(backoff * 2, 8)
                    continue
                log.info("%s: connected %s:%d <-> %s (dropped %d old frames)",
                         ch.name, self.cfg.host, ch.port, ch.interface, old)
                deframer.reset()
                backoff, last_err = 1, None
                self._state(True)

            readable = select.select([ts, self.cs], [], [], 5)[0]
            try:
                if ts in readable:
                    data = ts.recv(4096)
                    if not data:
                        raise ConnectionError("closed by the gateway")
                    for f in deframer.feed(data):
                        try:
                            self.cs.send(to_can_frame(f))
                            self.rx += 1
                            self.last_rx = time.time()
                        except OSError:  # ENOBUFS: nobody reads the interface fast enough
                            self.lost_rx += 1
                if self.cs in readable:
                    f = from_can_frame(self.cs.recv(CAN_FRAME_SIZE))
                    if f is not None:
                        ts.sendall(encode(f))
                        self.tx += 1
            except OSError as e:
                log.warning("%s: connection lost (%s), reconnecting", ch.name, e)
                ts.close()
                ts = None
                self.reconnects += 1
                self._state(False)


def _write_status(path: Path, cfg: Config, links: list[Link]) -> None:
    doc = {"time": round(time.time()), "pid": os.getpid(), "host": cfg.host,
           "config": str(cfg.source or "defaults"), "channels": [lk.snapshot() for lk in links]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(path)


def status_path(cfg: Config) -> Path | None:
    if cfg.status_file:
        return Path(cfg.status_file).expanduser()
    if os.environ.get("RUNTIME_DIRECTORY"):  # set by systemd from RuntimeDirectory=
        return Path(os.environ["RUNTIME_DIRECTORY"].split(":")[0]) / "status.json"
    return None


def run(cfg: Config) -> int:
    channels = cfg.enabled_channels
    if not channels:
        log.error("no enabled channel in %s", cfg.source or "the defaults")
        return 2
    try:
        links = [Link(ch, cfg) for ch in channels]
    except OSError as e:
        log.error("cannot open the CAN interfaces (%s): create them with `sudo wscan vcan-up`", e)
        return 1

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    for lk in links:
        lk.start()
    log.info("bridge started (%s): %s", cfg.source or "default config",
             ", ".join(f"{c.name}:{c.port}->{c.interface}" for c in channels))
    spath = status_path(cfg)
    t_stats = time.monotonic()
    while not stop.wait(STATUS_EVERY):
        if spath:
            try:
                _write_status(spath, cfg, links)
            except OSError as e:
                log.warning("cannot write %s: %s", spath, e)
                spath = None
        if time.monotonic() - t_stats >= STATS_EVERY:
            t_stats = time.monotonic()
            log.info(" | ".join(
                f"{s['channel']}/{s['interface']} {'up' if s['connected'] else 'DOWN'} rx={s['rx']} tx={s['tx']}"
                f" lost_rx={s['lost_rx']} lost_tx={s['lost_tx']}" for s in (lk.snapshot() for lk in links)))
    log.info("stopping")
    return 0
