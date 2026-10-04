FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-tk mpv fonts-dejavu-core xserver-xorg-core xserver-xorg-input-libinput xinit xauth x11-utils x11-xserver-utils openbox libgl1-mesa-dri mesa-utils pulseaudio-utils alsa-utils ca-certificates && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends xdotool python3-pil python3-pil.imagetk i965-va-driver && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY mijntv /app/mijntv
COPY deploy/libreelec/session.sh /usr/local/bin/iptv-session
COPY deploy/libreelec/entrypoint.sh /usr/local/bin/iptv-start
RUN chmod +x /usr/local/bin/iptv-session /usr/local/bin/iptv-start
ENV HOME=/tmp/iptv-home DISPLAY=:0 IPTV_AUDIO_DEVICE=alsa/hdmi:CARD=HDMI,DEV=2
HEALTHCHECK --interval=10s --start-period=90s --retries=3 CMD xdpyinfo >/dev/null && xdotool search --onlyvisible --name "^IPTV thuis$" >/dev/null && python3 -c "import urllib.request,urllib.error; exec('try:\n urllib.request.urlopen(\"http://127.0.0.1:8080/api/status\",timeout=3)\nexcept urllib.error.HTTPError as e:\n assert e.code==401')"
ENTRYPOINT ["/usr/local/bin/iptv-start"]
