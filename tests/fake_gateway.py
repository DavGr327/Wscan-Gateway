"""A fake 2-CH-CAN-TO-ETH for tests, with the quirks of the real firmware.

FakeWeb: web pages with `var name = value`, Basic auth, forms whose action carries the
section query; a post without it "reboots" (connection dropped, nothing saved); saved
values become active only after a restart.
FakeData: a channel's TCP data port; frames sent while nobody is connected pile up and
are delivered on connect, like the real gateway does.
"""
from __future__ import annotations

import base64
import copy
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from wscan.codec import Deframer, Frame, encode
from wscan.gateway import PAGES

USER, PASSWORD = "admin", "admin"
QUERY = {"can0set.cgi": "ser2net2.@ser2net[0]", "can1set.cgi": "ser2net2.@ser2net[0]",
         "ser2set.cgi": "ser2net1.@ser2net[0]", "can2uart.cgi": "ser2net2.@ser2net[0]"}

FACTORY = {
    "can1": {"C0baud": "1000000", "C0mode": "2", "pktl": "50", "pktt": "10", "fittype": "0",
             "StdIDLow": "0", "StdIDHigh": "2047", "ExdIDLow": "0", "ExdIDHigh": "536870911",
             "workmode": "1", "remoteaddr": "192.168.0.201", "remoteport": "20001", "localport": "20001",
             "checkport": "0", "workmodeb": "0", "remoteaddrb": "192.168.0.201", "remoteportb": "20002",
             "localportb": "20002", "checkportb": "0", "loginen": "0", "logintype": "1",
             "loginpkt": "1122334455", "loginwhen": "0", "d2did": "12345678901234567890",
             "d2dpwd": "12345678", "hearten": "0", "hearttype": "1",
             "heartpkt": "88 12 34 56 78 01 02 03 04 05 06 07 08 ", "hearttime": "30", "timeout": "86400"},
    "net": {"proto": "1", "ipaddr": "192.168.0.7", "netmask": "255.255.255.0",
            "gateway": "192.168.0.1", "dns": "208.67.222.222"},
    "func": {"c2c": "0", "c2u": "0"},
}
FACTORY["can2"] = {("C1" + k[2:] if k.startswith("C0") else k): v for k, v in FACTORY["can1"].items()}
FACTORY["can2"].update(remoteport="20005", localport="20005", remoteportb="20006", localportb="20006")
FACTORY["serial"] = {"u2band": "115200", "databit": "8", "paritybit": "1", "stopbit": "1", "u2pktl": "500",
                     "pckt": "10", **{k: v for k, v in FACTORY["can1"].items()
                                      if k not in ("C0baud", "C0mode", "pktl", "pktt", "fittype", "StdIDLow",
                                                   "StdIDHigh", "ExdIDLow", "ExdIDHigh")},
                     "remoteport": "20003", "localport": "20003", "heartpkt": "www.waveshare.com"}


class FakeWeb:
    def __init__(self):
        self.saved = copy.deepcopy(FACTORY)
        self.active = copy.deepcopy(FACTORY)
        self.posts: list[str] = []
        self.reboots = self.restarts = 0
        self.booted = time.time()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _page(self, key: str) -> str:
        page = PAGES[key]
        lines = "\n".join(f"var {k} = {v if v.isdigit() else repr(v)};" for k, v in self.saved[key].items())
        action = f"{page.cgi}?{QUERY[page.cgi]}" if page.cgi else "netset.cgi?network"
        return (f"<html><script>\n{lines}\nvar f = document.form1;\n</script>"
                f"<form name=\"form1\" action=\"{action}\" method=\"post\"></form></html>")

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _authorized(self):
                want = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
                if self.headers.get("Authorization") == want:
                    return True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="DSP_CLIENT_DEMO_AUTHENTICATE1"')
                self.end_headers()
                return False

            def _send(self, body: str):
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not self._authorized():
                    return
                path = self.path.lstrip("/")
                if path == "statusen.html":
                    up = int((time.time() - fake.booted) / 60)
                    self._send(f"<td><!--#wbname-->2-CH-CAN-TO-ETH</td><td><!--#revision-->V1.1.3</td>"
                               f"<td><!--#runtime-->0day: 0hour: {up}min</td>")
                elif path == "rebooten.html":
                    self._send('<form action="rebootset.cgi?~reboot -d 2 -f" method="post"></form>')
                else:
                    key = next((k for k, p in PAGES.items() if p.url == path), None)
                    if key is None:
                        self.send_error(404)
                    else:
                        self._send(fake._page(key))

            def do_POST(self):
                if not self._authorized():
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                cgi, _, query = urllib.parse.unquote(self.path.lstrip("/")).partition("?")
                fake.posts.append(f"{cgi}?{query}" if query else cgi)
                form = dict(urllib.parse.parse_qsl(body, keep_blank_values=True))
                if cgi == "rebootset.cgi" and "restart" in form:
                    fake.active = copy.deepcopy(fake.saved)
                    fake.restarts += 1
                    fake.booted = time.time()
                    self.close_connection = True  # it goes down without answering
                    return
                key = next((k for k, p in PAGES.items() if p.cgi == cgi), None)
                if key is None or query != QUERY.get(cgi):
                    fake.reboots += 1  # what the real firmware does with a post it does not like
                    self.close_connection = True
                    return
                for field, var in PAGES[key].fields:
                    if field in form:
                        fake.saved[key][var] = form[field]
                self._send("<html><title>ok</title></html>")

        return H


class FakeData:
    """One data port. send() frames from the 'bus'; .received holds what clients wrote."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.clients: list[socket.socket] = []
        self.backlog = b""
        self.received: list[Frame] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                if self.backlog:
                    c.sendall(self.backlog)
                    self.backlog = b""
                self.clients.append(c)
            threading.Thread(target=self._read, args=(c,), daemon=True).start()

    def _read(self, c):
        d = Deframer()
        while True:
            try:
                data = c.recv(4096)
            except OSError:
                data = b""
            if not data:  # client gone: what comes next piles up again
                with self.lock:
                    if c in self.clients:
                        self.clients.remove(c)
                return
            with self.lock:
                self.received += d.feed(data)

    def send(self, *frames: Frame):
        raw = b"".join(encode(f) for f in frames)
        with self.lock:
            if not self.clients:
                self.backlog += raw
            for c in list(self.clients):
                try:
                    c.sendall(raw)
                except OSError:
                    self.clients.remove(c)

    def drop_clients(self):
        with self.lock:
            for c in self.clients:
                # shutdown first: a plain close() while _read is blocked in recv() sends nothing on Linux
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                c.close()
            self.clients.clear()

    def wait_clients(self, n=1, timeout=5.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if len(self.clients) >= n:
                    return True
            time.sleep(0.02)
        return False

    def close(self):
        self.drop_clients()
        self.sock.close()
