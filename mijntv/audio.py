"""Select the HDMI PCM actually associated with a connected monitor."""
import os
import re
import subprocess


def audio_device():
    configured = os.environ.get('IPTV_AUDIO_DEVICE', '')
    if configured != 'auto':
        return configured
    try:
        output = subprocess.run(['aplay', '-l'], capture_output=True, text=True,
                                timeout=5, check=True, env={**os.environ, 'LC_ALL':'C'}).stdout
        for line in output.splitlines():
            match = re.match(r'card \d+: ([\w-]+) \[.*?\], device \d+: HDMI (\d+) \[([^]]+)\]', line)
            if match and not re.fullmatch(r'HDMI\s*\d*', match[3]):
                # ALSA maps codec pins dynamically; ELD file suffixes are not PCM indices.
                return f'alsa/hdmi:CARD={match[1]},DEV={match[2]}'
    except (OSError, subprocess.SubprocessError):
        pass
    return ''
