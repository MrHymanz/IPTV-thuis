#!/usr/bin/env bash
set -euo pipefail

# Isolate these requests from the runner's Azure mirror list and third-party
# sources. Ubuntu's archive signing key still verifies every mirror's index.
source_file="${RUNNER_TEMP:?GitHub runner temporary directory is required}/iptv-ubuntu.sources"
apt_options=(
  -o "Dir::Etc::sourcelist=$source_file"
  -o 'Dir::Etc::sourceparts=-'
  -o 'APT::Update::Error-Mode=any'
  -o 'Acquire::Retries=0'
  -o 'Acquire::http::Timeout=15'
  -o 'Acquire::https::Timeout=15'
)

for mirror in https://mirrors.edge.kernel.org/ubuntu https://archive.ubuntu.com/ubuntu; do
  cat > "$source_file" <<EOF
Types: deb
URIs: $mirror
Suites: noble noble-updates noble-backports noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
  echo "Installing test dependencies using $mirror"
  if sudo timeout --kill-after=10s 90s apt-get "${apt_options[@]}" update; then
    if sudo env DEBIAN_FRONTEND=noninteractive timeout --kill-after=10s 180s apt-get "${apt_options[@]}" install -y python3-tk python3-pil ffmpeg xvfb mpv; then
      exit 0
    fi
  fi
  echo "Mirror unavailable or package installation failed; trying the next mirror." >&2
done

echo 'Test dependencies could not be installed from either Ubuntu mirror.' >&2
exit 1
