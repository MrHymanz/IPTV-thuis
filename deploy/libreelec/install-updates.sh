#!/bin/sh
set -eu
# Run once on each LibreELEC mediacenter after installing the TV app.
DOCKER=/storage/.kodi/addons/service.system.docker/bin/docker
"$DOCKER" exec iptv-thuis-tv python3 -c 'from mijntv.maintenance import UPDATE_LEASE_SUPPORTED; assert UPDATE_LEASE_SUPPORTED'

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p /storage/iptv-thuis
chmod 700 /storage/iptv-thuis
cp "$SCRIPT_DIR/update.py" /storage/iptv-thuis/update.py
chmod 700 /storage/iptv-thuis/update.py
if [ ! -f /storage/iptv-thuis/updates.json ]; then
    umask 077
    cat > /storage/iptv-thuis/updates.json <<'JSON'
{
  "repository": "MrHymanz/IPTV-thuis",
  "branch": "main",
  "workflow": ".github/workflows/validate.yml",
  "recording_guard_seconds": 600
}
JSON
fi
if [ ! -f /storage/iptv-thuis/update-state.json ]; then
    revision=$("$DOCKER" inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' iptv-thuis-tv)
    /usr/bin/python3 - "$revision" <<'PYTHON'
import json,re,sys
from pathlib import Path
revision=sys.argv[1]
if re.fullmatch(r'[0-9a-f]{40}',revision):
    path=Path('/storage/iptv-thuis/update-state.json')
    path.write_text(json.dumps({'revision':revision})+'\n')
    path.chmod(0o600)
PYTHON
fi
cp "$SCRIPT_DIR/iptv-update.service" /storage/.config/system.d/iptv-update.service
cp "$SCRIPT_DIR/iptv-update.timer" /storage/.config/system.d/iptv-update.timer
systemctl daemon-reload
systemctl enable --now iptv-update.timer
printf '%s\n' 'Automatische updates ingeschakeld. Eerste controle volgt na de opstartwachttijd.'
