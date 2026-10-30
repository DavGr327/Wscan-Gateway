#!/usr/bin/env bash
# Removes the bridge service and the `wscan` command; leaves the repo, config.toml and the network as they are.
set -uo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
UNIT=/etc/systemd/system/wscan-bridge.service
if [ -f "$UNIT" ]; then
    sudo systemctl disable --now wscan-bridge.service
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    echo "service removed"
fi
for link in /usr/local/bin/wscan "$HOME/.local/bin/wscan"; do
    if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$REPO/wscan" ]; then
        if [ -w "$(dirname "$link")" ]; then rm -f "$link"; else sudo rm -f "$link"; fi
        echo "removed $link"
    fi
done
echo "the vcan interfaces stay until reboot (sudo ip link delete vcan0 to remove one now)"
