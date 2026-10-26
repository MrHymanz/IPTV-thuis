FROM node:22-bookworm-slim AS youtube-receiver
WORKDIR /opt/iptv-cast
COPY deploy/libreelec/youtube/package*.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY deploy/libreelec/youtube/receiver.mjs ./

FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2 AS youtube-proof

FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-tk mpv fonts-dejavu-core xserver-xorg-core xserver-xorg-input-libinput xinit xauth x11-utils x11-xserver-utils openbox libgl1-mesa-dri mesa-utils pulseaudio-utils alsa-utils ca-certificates && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends xdotool python3-pil python3-pil.imagetk i965-va-driver && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends xserver-xorg-input-evdev && rm -rf /var/lib/apt/lists/*
COPY deploy/libreelec/99-iptv-remote.conf /usr/share/X11/xorg.conf.d/99-iptv-remote.conf
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/yt-dlp && /opt/yt-dlp/bin/pip install --no-cache-dir 'yt-dlp[default]==2026.10.21' 'bgutil-ytdlp-pot-provider==2.0.2'
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
COPY --from=youtube-proof /usr/local/bin/node /usr/local/bin/node
COPY --from=youtube-proof /app /opt/youtube-proof
COPY --from=youtube-receiver /opt/iptv-cast /opt/iptv-cast
RUN printf '%s\n' '--js-runtimes node' '--extractor-args youtube:player_client=mweb' '--check-formats' > /etc/yt-dlp.conf
RUN ln -s /opt/yt-dlp/bin/yt-dlp /usr/local/bin/yt-dlp
ENV IPTV_YOUTUBE_CAST=1
ARG VCS_REF=local
ENV IPTV_REVISION=${VCS_REF}
LABEL org.opencontainers.image.revision=${VCS_REF}
WORKDIR /app
COPY mijntv /app/mijntv
COPY deploy/libreelec/session.sh /usr/local/bin/iptv-session
COPY deploy/libreelec/entrypoint.sh /usr/local/bin/iptv-start
RUN chmod +x /usr/local/bin/iptv-session /usr/local/bin/iptv-start
ENV HOME=/tmp/iptv-home DISPLAY=:0 IPTV_AUDIO_DEVICE=auto
HEALTHCHECK --interval=10s --start-period=90s --retries=3 CMD xdpyinfo >/dev/null && xdotool search --onlyvisible --name "^IPTV thuis$" >/dev/null && python3 -c "import urllib.request,urllib.error; exec('try:\n urllib.request.urlopen(\"http://127.0.0.1:8080/api/status\",timeout=3)\nexcept urllib.error.HTTPError as e:\n assert e.code==401')"
ENTRYPOINT ["/usr/local/bin/iptv-start"]
