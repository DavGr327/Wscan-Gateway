#!/usr/bin/env bash
# Removes the bridge service and the `wscan` link; leaves the repo, config.toml and the network as they are.
set -uo pipefail
UNIT=/etc/systemd/system/wscan-bridge.service
if [ -f "$UNIT" ]; then
    sudo systemctl disable --now wscan-bridge.service
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    echo "service removed"
fi
if [ -L "$HOME/.local/bin/wscan" ]; then
    rm -f "$HOME/.local/bin/wscan"
    echo "removed $HOME/.local/bin/wscan"
fi
echo "the vcan interfaces stay until reboot (sudo ip link delete vcan0 to remove one now)"
