#!/bin/sh
set -eu
DOCKER=/storage/.kodi/addons/service.system.docker/bin/docker
# Dedicated local display container. Privileged access is needed for DRM, VT and USB input.
"$DOCKER" create --name iptv-thuis-tv --network host --privileged \
    --mount type=bind,src=/storage/iptv-thuis/data,dst=/data \
    --mount type=bind,src=/run/udev,dst=/run/udev,readonly \
    --mount type=bind,src=/etc/resolv.conf,dst=/etc/resolv.conf,readonly \
    --mount type=bind,src=/etc/localtime,dst=/etc/localtime,readonly \
    --env TZ=:/etc/localtime \
    --tmpfs /tmp:exec,mode=1777 \
    --env "IPTV_AUDIO_DEVICE=${IPTV_AUDIO_DEVICE:-auto}" \
    iptv-thuis-tv:local
