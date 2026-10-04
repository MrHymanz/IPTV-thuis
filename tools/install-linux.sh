#!/bin/sh
set -eu

# Run as the desktop user, not root. Only OS package installation uses sudo.
if [ "$(id -u)" = 0 ]; then
    echo 'Voer dit script uit als de gebruiker die op de tv automatisch inlogt.' >&2
    exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
    echo 'Dit installatiescript is voor Debian/Ubuntu. Zie README.md voor handmatige installatie.' >&2
    exit 1
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
install_dir="$HOME/.local/share/iptv-thuis-app"
if [ -e /etc/os-release ] && grep -qi libreelec /etc/os-release; then
    echo 'Deze zelfstandige versie vereist een Linux X11-desktop; LibreELEC wordt niet ondersteund.' >&2
    exit 1
fi
sudo apt-get update
sudo apt-get install -y python3 python3-tk python3-pil python3-pil.imagetk mpv fonts-dejavu-core
mkdir -p "$install_dir" "$HOME/.local/bin" "$HOME/.config/autostart"
if [ "$project_dir" != "$install_dir" ]; then
    cp -R "$project_dir/mijntv" "$install_dir/"
fi
python3 - "$install_dir" "$HOME/.local/bin/iptv-thuis" "$HOME/.config/autostart/iptv-thuis.desktop" <<'PY'
import pathlib
import shlex
import sys
directory, launcher, desktop = sys.argv[1:]
path = pathlib.Path(launcher)
path.write_text('#!/bin/sh\ncd ' + shlex.quote(directory) + '\nexec /usr/bin/python3 -m mijntv "$@"\n')
path.chmod(0o755)
# Desktop Entry Exec quoting differs from shell quoting.
escaped = launcher.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
pathlib.Path(desktop).write_text('[Desktop Entry]\nType=Application\nName=IPTV thuis\nExec="' + escaped + '"\nTerminal=false\nX-GNOME-Autostart-enabled=true\n')
PY
echo 'Geïnstalleerd. Kies bij aanmelden een X11/Xorg-sessie en stel automatisch aanmelden in.'
echo "Start nu met: $HOME/.local/bin/iptv-thuis"
