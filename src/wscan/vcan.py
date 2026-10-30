"""Creates the virtual CAN interfaces the bridge writes to (needs root)."""
from __future__ import annotations

import json
import subprocess


class VcanError(Exception):
    pass


def link_kind(name: str) -> str | None:
    """'vcan', 'can', ... or None if the interface does not exist."""
    r = subprocess.run(["ip", "-j", "-d", "link", "show", "dev", name], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    info = json.loads(r.stdout)[0].get("linkinfo", {})
    return info.get("info_kind", "")


def vcan_up(names: list[str], txqueuelen: int = 1000) -> list[str]:
    """Creates and raises each interface; returns what was done, one line each."""
    subprocess.run(["modprobe", "vcan"], check=False)
    done = []
    for name in names:
        kind = link_kind(name)
        if kind is None:
            subprocess.run(["ip", "link", "add", "dev", name, "type", "vcan"], check=True)
            done.append(f"{name}: created")
        elif kind != "vcan":
            # a real CAN controller already has this name: writing gateway frames into it
            # would put them on another bus
            raise VcanError(f"{name} exists and is a '{kind or 'non-virtual'}' interface, "
                            "not vcan: pick another interface name in the config")
        else:
            done.append(f"{name}: already there")
        subprocess.run(["ip", "link", "set", name, "txqueuelen", str(txqueuelen), "up"], check=True)
    return done
