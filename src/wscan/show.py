"""`wscan show`: everything at a glance, in plain words: config, live gateway settings, bridge."""
from __future__ import annotations

import time

from .bridge import read_status
from .config import Config
from .friendly import describe_channel
from .gateway import Gateway, GatewayError


def show(cfg: Config, say=print) -> int:
    gw_line, live, problems = "", {}, []
    try:
        gw = Gateway.from_config(cfg, prompt=False)
        st = gw.status()
        gw_line = f"{st.get('wbname', 'gateway')} firmware {st.get('revision', '?')}, up {st.get('runtime', '?')}"
        live = {ch.name: gw.read(ch.name) for ch in cfg.channels}
    except GatewayError as e:
        gw_line = f"settings not readable ({e})"

    _, doc = read_status(cfg)
    fresh = doc is not None and time.time() - doc.get("time", 0) <= 15
    state = {s["channel"]: s for s in doc["channels"]} if fresh else {}

    say(f"gateway   {cfg.host}   {gw_line}")
    if cfg.network.address:
        say(f"network   this device is {cfg.network.address} on {cfg.network.interface}")
    say(f"bridge    {'running' if fresh else 'not running'}")
    say("")
    for ch in cfg.channels:
        s = state.get(ch.name)
        link = "off" if not ch.enabled else ("UP" if s and s["connected"] else "DOWN")
        traffic = f"   {s['rx']} frames in, {s['tx']} out" if s else ""
        say(f"{ch.name.upper()}  ->  {ch.interface:<10} {link}{traffic}")
        if ch.name in live:
            d = describe_channel(live[ch.name])
            say(f"      bitrate {d['bitrate']}   mode {d['mode']}   filter {d['filter']}"
                f"   packets every {d['packets']}")
            if ch.enabled and (not d["tcp_server"] or d["port"] != str(ch.port)):
                problems.append(f"{ch.name}: the gateway is not set up for the bridge "
                                f"(port {d['port']}, wanted {ch.port}): run `wscan gw apply`")
    if cfg.unsaved:
        problems.append(f"{cfg.unsaved} change(s) tried with `wscan set` and not saved: wscan save / wscan revert")
    if problems:
        say("")
        for p in problems:
            say(f"note: {p}")
    return 0
