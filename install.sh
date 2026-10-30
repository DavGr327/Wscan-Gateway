#!/usr/bin/env bash
# Installs wscan from the cloned repo:
#   ./install.sh               asks a few questions (Enter keeps the default), then sets up
#                              everything: config, network, gateway, `wscan` command, bridge service
#   ./install.sh --defaults    no questions: factory defaults, edit config.toml or use `wscan setup` later
#   ./install.sh --no-service  everything but the systemd service
# Run it as the user the bridge should run as; it asks for sudo where needed.
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SERVICE=1
ASK=1
RUN_USER="${SUDO_USER:-$(id -un)}"
while [ $# -gt 0 ]; do
    case "$1" in
        --no-service) SERVICE=0 ;;
        --defaults) ASK=0 ;;
        --user) RUN_USER="$2"; shift ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
[ -t 0 ] || ASK=0

[ "$(uname -s)" = Linux ] || { echo "SocketCAN needs Linux" >&2; exit 1; }
PY="$(command -v python3)" || { echo "python3 not found" >&2; exit 1; }
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "python3 is older than 3.11: creating .venv with tomli"
    "$PY" -m venv "$REPO/.venv"
    "$REPO/.venv/bin/pip" install -q tomli
fi
chmod +x "$REPO/wscan" "$REPO/uninstall.sh"
CONFIG="$REPO/config.toml"

if [ "$ASK" = 1 ]; then
    if [ -f "$CONFIG" ]; then
        read -rp "config.toml already exists: answer the setup questions again? [y/N] " again
        case "$again" in [yYsS]*) "$REPO/wscan" --config "$CONFIG" setup || true ;; esac
    else
        "$REPO/wscan" setup || echo "setup not completed: run 'wscan setup' when you are ready"
    fi
fi
if [ ! -f "$CONFIG" ]; then
    cp "$REPO/config.example.toml" "$CONFIG"
    echo "created config.toml with the factory defaults ('wscan setup' to answer the questions)"
fi
"$REPO/wscan" --config "$CONFIG" config >/dev/null

# the command for everyone, sudo included (/usr/local/bin is in sudo's path too)
ON_PATH=0
if sudo ln -sf "$REPO/wscan" /usr/local/bin/wscan; then
    ON_PATH=1
    [ "$(readlink -f "$HOME/.local/bin/wscan" 2>/dev/null)" = "$REPO/wscan" ] && rm -f "$HOME/.local/bin/wscan"
else
    mkdir -p "$HOME/.local/bin"
    ln -sf "$REPO/wscan" "$HOME/.local/bin/wscan"
fi

if [ "$SERVICE" = 1 ]; then
    echo "installing the bridge service ..."
    sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$RUN_USER|g" "$REPO/systemd/wscan-bridge.service.in" \
        | sudo tee /etc/systemd/system/wscan-bridge.service >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now wscan-bridge.service >/dev/null 2>&1
    sudo systemctl restart wscan-bridge.service  # picks up a config written by the setup
    sleep 3
    "$REPO/wscan" --config "$CONFIG" status || true
fi

echo
if [ "$ON_PATH" = 0 ]; then
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) echo "the \`wscan\` command works from your next login (or now: export PATH=\"\$HOME/.local/bin:\$PATH\")" ;;
    esac
fi
cat <<EOF
everyday commands:
  wscan show                         everything at a glance
  wscan set can1.bitrate=500000      change a setting (try it)
  wscan save | wscan revert          keep the change | undo it
  wscan status                       is it working?
  wscan setup                        the installation questions again
  wscan -h                           all the rest
EOF
