"""Native X11 display settings; no shell commands or arbitrary mode strings."""
import re
import subprocess

RESOLUTIONS = ('auto', '1280x720', '1920x1080', '3840x2160')


class Display:
    def __init__(self, store, native=False):
        self.store, self.native = store, native
        self.error = ''

    def settings(self):
        return {'resolution': self.store.setting('display_resolution', 'auto'),
                'margin': int(self.store.setting('display_margin', '0'))}

    def query(self):
        if not self.native:
            return None
        try:
            output = subprocess.run(['xrandr', '--query'], capture_output=True, text=True,
                                    timeout=5, check=True).stdout
            displays = []
            for line in output.splitlines():
                match = re.match(r'^(\S+) connected(?: primary)?(?: (\d+x\d+)\+\d+\+\d+)?', line)
                if match:
                    displays.append({'output': match[1], 'current': match[2], 'modes': []})
                elif line and not line[0].isspace():
                    # Modes below disconnected outputs must not join the preceding display.
                    displays.append({'output': '', 'current': None, 'modes': []})
                elif displays:
                    mode = re.match(r'^\s+(\d+x\d+)\s', line)
                    if mode:
                        displays[-1]['modes'].append(mode[1])
            return next((d for d in displays if d['current']), next((d for d in displays if d['output']), None))
        except (OSError, subprocess.SubprocessError):
            return None

    def status(self):
        current = self.query()
        return {**self.settings(), 'native': self.native,
                'current': current['current'] if current else None,
                'available': [r for r in RESOLUTIONS if r == 'auto' or not self.native or
                              (current and r in current['modes'])], 'error': self.error}

    def save(self, resolution, margin):
        if resolution not in RESOLUTIONS or type(margin) is not int or not 0 <= margin <= 10:
            raise ValueError('Kies een geldige resolutie en een beeldmarge van 0 tot 10%.')
        if self.native and resolution != 'auto' and resolution not in self.status()['available']:
            raise ValueError('Deze resolutie wordt niet door het aangesloten scherm aangeboden.')
        with self.store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           [('display_resolution', resolution), ('display_margin', str(margin))])

    def apply(self):
        self.error = ''
        if not self.native:
            return
        current = self.query()
        resolution = self.settings()['resolution']
        if not current or (resolution != 'auto' and resolution not in current['modes']):
            self.error = 'De ingestelde resolutie is nu niet beschikbaar; het huidige beeld blijft behouden.'
            return
        try:
            option = ['--auto'] if resolution == 'auto' else ['--mode', resolution]
            subprocess.run(['xrandr', '--output', current['output'], *option],
                           capture_output=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            self.error = 'De resolutie kon niet worden toegepast; het huidige beeld blijft behouden.'
