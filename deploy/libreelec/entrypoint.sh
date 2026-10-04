#!/bin/sh
set -eu
mkdir -p "$HOME" /tmp/.X11-unix
exec xinit /usr/local/bin/iptv-session -- /usr/lib/xorg/Xorg :0 vt7 -nolisten tcp -noreset -ac -logfile /tmp/Xorg.log
