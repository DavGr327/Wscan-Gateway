"""Gateway settings over its web pages, the same forms the browser uses.

What the firmware does (seen on a 2-CH-CAN-TO-ETH, firmware V1.1.3):
- every page carries its current values as `var name = value` in a script;
- each form posts to a .cgi whose address has a section query, e.g.
  `can1set.cgi?ser2net2.@ser2net[0]`. Without it the gateway reboots and saves nothing;
- a correct post answers HTTP 200 and saves, but the CAN parameters only take effect
  after a restart (about 3 s).
The Network page is read-only here (one mistake there and the gateway is gone),
System Set is never touched (it holds the password), nor is the factory reset.
"""
from __future__ import annotations

import base64
import getpass
import http.client
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import Config, password_path

# socket A/B and registration: same field and var names on the CAN and serial pages
_SOCKET = [(n, n) for n in (
    "workmode", "remoteaddr", "remoteport", "localport", "checkport",
    "workmodeb", "remoteaddrb", "remoteportb", "localportb", "checkportb",
    "loginen", "logintype", "loginpkt", "loginwhen", "d2did", "d2dpwd")]


def _can_fields(prefix):
    return [
        ("baud", f"{prefix}baud"), ("paritybit", f"{prefix}mode"), ("packtlen", "pktl"),
        ("packttime", "pktt"), ("ID_FITER_TEPE", "fittype"), ("StdIDLow", "StdIDLow"),
        ("StdIDHigh", "StdIDHigh"), ("ExdIDLow", "ExdIDLow"), ("ExdIDHigh", "ExdIDHigh"),
    ] + _SOCKET + [
        ("hearten", "hearten"), ("heartpkt", "heartpkt"), ("hearttime", "hearttime"),
        ("reconnecttimeout", "timeout"),
    ]


_SERIAL = [
    ("baud", "u2band"), ("databit", "databit"), ("paritybit", "paritybit"), ("stopbit", "stopbit"),
    ("packtlen", "u2pktl"), ("packttime", "pckt"),
] + _SOCKET + [
    ("hearten", "hearten"), ("hearttype", "hearttype"), ("heartpkt", "heartpkt"),
    ("hearttime", "hearttime"), ("reconnecttimeout", "timeout"),
]


@dataclass(frozen=True)
class Page:
    url: str
    cgi: str | None  # fallback if the form's action can't be read; None = read-only
    fields: list | None  # (form field, page var); None = show the page vars as they are


PAGES = {
    "can1": Page("can0neten.html", "can0set.cgi", _can_fields("C0")),
    "can2": Page("can1neten.html", "can1set.cgi", _can_fields("C1")),
    "serial": Page("ser2net1en.html", "ser2set.cgi", _SERIAL),
    "func": Page("cantouarten.html", "can2uart.cgi", [("can2can", "c2c"), ("can2uart", "c2u")]),
    "net": Page("ipconfigen.html", None, None),
}
WRITABLE = [k for k, p in PAGES.items() if p.fields]
NET_SHOWN = ("proto", "ipaddr", "netmask", "gateway", "dns")

LEGEND = {
    "workmode": {"0": "None", "1": "TCP server", "2": "TCP client", "3": "UDP server", "4": "UDP client"},
    "workmodeb": {"0": "None", "2": "TCP client", "4": "UDP client"},
    "ID_FITER_TEPE": {"0": "off", "1": "whitelist std", "2": "blacklist std",
                      "3": "whitelist ext", "4": "blacklist ext"},
    "can2can": {"0": "off", "1": "on (CAN1 <-> CAN2 relay)"},
    "can2uart": {"0": "off", "1": "CAN1", "2": "CAN2"},
    "hearten": {"0": "off", "1": "bus side", "2": "network side"},
    "loginen": {"0": "off", "1": "custom", "2": "USR-Cloud", "3": "MAC"},
    "checkport": {"0": "off", "1": "on"},
    "checkportb": {"0": "off", "1": "on"},
}
_CAN_MODE = {"1": "loopback", "2": "normal"}
_SER_PARITY = {"1": "none", "2": "odd", "3": "even", "4": "mark", "5": "space"}

VAR_RE = re.compile(r"var\s+(\w+)\s*=\s*([^;\n]*)")
FORM_RE = re.compile(r"<form[^>]*\baction\s*=\s*[\"']([^\"']+)", re.I)
SSI_RE = re.compile(r"<!--#(\w+)-->([^<]*)")
_NO_ANSWER = (TimeoutError, ConnectionError, urllib.error.URLError, http.client.HTTPException)


class GatewayError(Exception):
    pass


def describe(page: str, field: str, value: str) -> str:
    if field == "paritybit":
        return (_CAN_MODE if page.startswith("can") else _SER_PARITY).get(value, "")
    return LEGEND.get(field, {}).get(value, "")


def parse_vars(html: str) -> dict:
    out = {}
    for name, raw in VAR_RE.findall(html):
        val = raw.strip()
        if val.startswith("document."):
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]  # inside quotes keep everything, trailing spaces included
        out.setdefault(name, val)
    return out


def resolve_password(cfg: Config) -> tuple[str, str]:
    """$WSCAN_PASSWORD, else password_file ("password" or "user:password"), else ask."""
    if os.environ.get("WSCAN_PASSWORD"):
        return cfg.user, os.environ["WSCAN_PASSWORD"]
    path = password_path(cfg)
    if path.is_file():
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"warning: {path} is readable by other users, run: chmod 600 {path}", file=sys.stderr)
        text = path.read_text().strip()
        line = text.splitlines()[0] if text else ""
        user, sep, pw = line.partition(":")
        return (user or cfg.user, pw) if sep else (cfg.user, line)
    if sys.stdin.isatty():
        return cfg.user, getpass.getpass(f"password for {cfg.user}@{cfg.host}: ")
    raise GatewayError(f"no password: set WSCAN_PASSWORD or create {path} (chmod 600)")


class Gateway:
    def __init__(self, host: str, user: str, password: str, *, http_port: int = 80,
                 timeout: float = 8, reboot_wait: float = 40):
        self.base = f"http://{host}:{http_port}/"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout, self.reboot_wait = timeout, reboot_wait

    @classmethod
    def from_config(cls, cfg: Config) -> Gateway:
        user, pw = resolve_password(cfg)
        return cls(cfg.host, user, pw, http_port=cfg.http_port)

    # --- HTTP -------------------------------------------------------------------------
    def _req(self, path: str, data: bytes | None = None) -> tuple[int, str]:
        # a page read that finds the connection dropped is tried once more (the embedded web
        # server does that now and then); a post never is: write() checks the result instead
        for attempt in (1, 2):
            req = urllib.request.Request(self.base + path, data=data, headers={"Authorization": self.auth})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise GatewayError("401: wrong user or password") from None
                raise GatewayError(f"HTTP {e.code} on {path}") from None
            except _NO_ANSWER as e:
                if data is not None or attempt == 2:
                    raise GatewayError(f"gateway not answering on {self.base} ({e})") from None
                time.sleep(0.3)

    def _post(self, action: str, fields: dict) -> int | None:
        """HTTP status, or None when the gateway dropped the connection (it reboots on bad posts)."""
        path = urllib.parse.quote(action, safe="/?.@[]~=&-_:")
        try:
            return self._req(path, urllib.parse.urlencode(fields).encode())[0]
        except GatewayError as e:
            if "401" in str(e):
                raise
            return None

    def wait_back(self) -> float | None:
        t0 = time.time()
        while time.time() - t0 < self.reboot_wait:
            time.sleep(1.5)
            try:
                self._req("statusen.html")
                return time.time() - t0
            except GatewayError:
                pass
        return None

    # --- read -------------------------------------------------------------------------
    def status(self) -> dict:
        """Model, firmware, uptime, MAC, IP: the status page uses server includes, not vars."""
        return {k: v.strip() for k, v in SSI_RE.findall(self._req("statusen.html")[1])}

    def form(self, page: str) -> tuple[dict, str]:
        """Current values of a page's form and the action to post them to."""
        p = PAGES[page]
        html = self._req(p.url)[1]
        values = parse_vars(html)
        if p.fields is None:
            return values, ""
        missing = [var for _, var in p.fields if var not in values]
        if missing:
            raise GatewayError(f"{page}: page has no {missing}: different firmware? nothing sent")
        m = FORM_RE.search(html)
        return {f: values[var] for f, var in p.fields}, (m.group(1) if m else p.cgi)

    def read(self, page: str) -> dict:
        values = self.form(page)[0]
        if PAGES[page].fields is None:
            return {k: values.get(k, "") for k in NET_SHOWN}
        return values

    def backup(self) -> dict:
        return {"status": self.status(), **{page: self.form(page)[0] for page in PAGES}}

    # --- write ------------------------------------------------------------------------
    def check_fields(self, page: str, changes: dict) -> None:
        if page not in WRITABLE:
            raise GatewayError(f"'{page}' is read-only here; writable pages: {', '.join(WRITABLE)}")
        known = [f for f, _ in PAGES[page].fields]
        bad = [f for f in changes if f not in known]
        if bad:
            raise GatewayError(f"{page}: unknown field(s) {bad}; known: {', '.join(known)}")

    def diff(self, page: str, changes: dict) -> dict:
        """{field: (current, wanted)} for the fields that would actually change."""
        self.check_fields(page, changes)
        current = self.form(page)[0]
        return {f: (current[f], str(v)) for f, v in changes.items() if current[f] != str(v)}

    def write(self, page: str, changes: dict) -> None:
        """Posts the whole form with `changes` applied and checks that they were saved."""
        self.check_fields(page, changes)
        values, action = self.form(page)
        values.update({k: str(v) for k, v in changes.items()})
        if self._post(action, values) is None and self.wait_back() is None:
            raise GatewayError(f"gateway did not come back within {self.reboot_wait:.0f} s")
        now = self.form(page)[0]
        lost = {f: now[f] for f, v in changes.items() if now[f] != str(v)}
        if lost:
            raise GatewayError(f"{page}: not saved: {lost}")

    def restart(self) -> float:
        html = self._req("rebooten.html")[1]
        m = FORM_RE.search(html)
        self._post(m.group(1) if m else "rebootset.cgi", {"restart": "Restart"})
        back = self.wait_back()
        if back is None:
            raise GatewayError(f"gateway did not come back within {self.reboot_wait:.0f} s after restart")
        return back

    def plan(self, wanted: dict) -> dict:
        """wanted {page: {field: value}} -> {page: {field: (current, wanted)}} for what differs."""
        out = {}
        for page, changes in wanted.items():
            d = self.diff(page, changes)
            if d:
                out[page] = d
        return out

    def apply(self, wanted: dict, restart: bool = True) -> dict:
        """Writes every page that differs, then one restart so it all takes effect."""
        todo = self.plan(wanted)
        for page, d in todo.items():
            self.write(page, {f: new for f, (_, new) in d.items()})
        if todo and restart:
            self.restart()
        return todo


def wanted_from_config(cfg: Config) -> dict:
    """What `wscan gw apply` enforces: [channel.gateway] fields, plus what the bridge relies on:
    each enabled channel in TCP server mode on the port written in the config."""
    wanted = {}
    for ch in cfg.enabled_channels:
        wanted[ch.name] = {"workmode": "1", "localport": str(ch.port), **ch.gateway}
    return wanted
