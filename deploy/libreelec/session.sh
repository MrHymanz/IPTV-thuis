#!/bin/sh
set -eu
xset s off
xset -dpms
openbox &
# Wait for the window manager before Tk requests a fullscreen window.
attempt=0
until xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q 'window id #'; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 50 ] || exit 1
    sleep .1
done
exec python3 -m mijntv --data-dir /data --port 8080
