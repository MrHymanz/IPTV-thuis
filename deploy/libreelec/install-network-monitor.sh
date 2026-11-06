#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -m 700 "$SCRIPT_DIR/network-monitor.py" /storage/iptv-thuis/network-monitor.py
cp "$SCRIPT_DIR/iptv-network-monitor.service" /storage/.config/system.d/iptv-network-monitor.service
systemctl daemon-reload
systemctl enable --now iptv-network-monitor.service
