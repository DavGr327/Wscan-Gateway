#!/usr/bin/env bash
# Sets up wscan from the cloned repo:
#   ./install.sh               config.toml (from the example), `wscan` on the PATH, bridge service
#   ./install.sh --no-service  everything but the systemd service
# Run it as the user the bridge should run as; it asks for sudo only for the service.
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SERVICE=1
RUN_USER="${SUDO_USER:-$(id -un)}"
while [ $# -gt 0 ]; do
    case "$1" in
        --no-service) SERVICE=0 ;;
        --user) RUN_USER="$2"; shift ;;
        -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

[ "$(uname -s)" = Linux ] || { echo "SocketCAN needs Linux" >&2; exit 1; }
PY="$(command -v python3)" || { echo "python3 not found" >&2; exit 1; }
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "python3 is older than 3.11: creating .venv with tomli"
    "$PY" -m venv "$REPO/.venv"
    "$REPO/.venv/bin/pip" install -q tomli
fi
chmod +x "$REPO/wscan" "$REPO/uninstall.sh"

if [ ! -f "$REPO/config.toml" ]; then
    cp "$REPO/config.example.toml" "$REPO/config.toml"
    echo "created config.toml from config.example.toml: edit it for your setup"
fi
"$REPO/wscan" --config "$REPO/config.toml" config

mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/wscan" "$HOME/.local/bin/wscan"
echo "wscan -> $HOME/.local/bin/wscan"

if [ "$SERVICE" = 1 ]; then
    UNIT=/etc/systemd/system/wscan-bridge.service
    sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$RUN_USER|g" "$REPO/systemd/wscan-bridge.service.in" \
        | sudo tee "$UNIT" >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now wscan-bridge.service
    sleep 2
    systemctl --no-pager --lines=5 status wscan-bridge.service || true
fi

cat <<EOF

next steps:
  wscan discover              find the gateway (sudo wscan discover --arp eth0 if it is on another subnet)
  wscan net check             can this host reach it?
  edit $REPO/config.toml, then:
  sudo systemctl restart wscan-bridge
  wscan status                bridge and channels
  candump vcan0               (can-utils) the frames
EOF
