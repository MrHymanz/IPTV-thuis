#!/bin/sh
set -eu
DOCKER=/storage/.kodi/addons/service.system.docker/bin/docker
restore_kodi() {
    rm -f /storage/iptv-thuis/enabled
    systemctl start kodi.service
}
trap '"$DOCKER" stop -t 5 iptv-thuis-tv >/dev/null 2>&1 || true; exit 0' TERM INT
if ! "$DOCKER" start iptv-thuis-tv; then
    restore_kodi
    exit 1
fi
# Give Xorg and the app time to start before declaring this boot successful.
attempt=0
while [ "$attempt" -lt 60 ]; do
    health=$("$DOCKER" inspect --format '{{.State.Health.Status}}' iptv-thuis-tv 2>/dev/null || true)
    [ "$health" != healthy ] || break
    if [ "$("$DOCKER" inspect --format '{{.State.Running}}' iptv-thuis-tv)" != true ]; then break; fi
    attempt=$((attempt + 1))
    sleep 2
done
if [ "$health" != healthy ]; then
    "$DOCKER" stop -t 5 iptv-thuis-tv || true
    restore_kodi
    exit 1
fi
while [ "$("$DOCKER" inspect --format '{{.State.Running}}' iptv-thuis-tv)" = true ]; do
    health=$("$DOCKER" inspect --format '{{.State.Health.Status}}' iptv-thuis-tv)
    [ "$health" != unhealthy ] || break
    sleep 10
done
"$DOCKER" stop -t 5 iptv-thuis-tv || true
restore_kodi
exit 1
