"""Native X11 display settings; no shell commands or arbitrary mode strings."""
import re
import subprocess

REFRESH_RATES = ('auto', '23.98', '24', '25', '29.97', '30', '50', '59.94', '60')
RESOLUTIONS = ('auto', '1280x720', '1920x1080', '3840x2160')


class Display:
    def __init__(self, store, native=False):
        self.store, self.native = store, native
        self.error = ''

    def settings(self):
        return {'resolution': self.store.setting('display_resolution', 'auto'),
                'margin': int(self.store.setting('display_margin', '0')),
                'decoder': self.store.setting('display_decoder', 'auto'),
                'show_fps': self.store.setting('display_show_fps', '1') == '1',
                'refresh_rate': self.store.setting('display_refresh_rate', 'auto')}

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
                    displays.append({'output': match[1], 'current': match[2], 'modes': [], 'rates': {}, 'current_rate': None})
                elif line and not line[0].isspace():
                    # Modes below disconnected outputs must not join the preceding display.
                    displays.append({'output': '', 'current': None, 'modes': [], 'rates': {}, 'current_rate': None})
                elif displays:
                    mode = re.match(r'^\s+(\d+x\d+)\s', line)
                    if mode:
                        displays[-1]['modes'].append(mode[1])
                        rates=[]
                        for token in line[mode.end():].split():
                            try: rate=f'{float(token.rstrip("*+")):g}'
                            except ValueError: continue
                            rates.append(rate)
                            if '*' in token: displays[-1]['current_rate']=rate
                        displays[-1]['rates'][mode[1]]=rates
            return next((d for d in displays if d['current']), next((d for d in displays if d['output']), None))
        except (OSError, subprocess.SubprocessError):
            return None

    def status(self):
        current = self.query()
        return {**self.settings(), 'native': self.native,
                'current': current['current'] if current else None,
                'current_rate': current['current_rate'] if current else None,
                'available_rates': {r: ['auto']+[hz for hz in REFRESH_RATES[1:]
                    if not self.native or (current and hz in current['rates'].get(r, []))]
                    for r in RESOLUTIONS if r != 'auto'},
                'available': [r for r in RESOLUTIONS if r == 'auto' or not self.native or
                              (current and r in current['modes'])], 'error': self.error}

    def save(self, resolution, margin, decoder=None, show_fps=None, refresh_rate=None):
        if resolution not in RESOLUTIONS or type(margin) is not int or not 0 <= margin <= 10:
            raise ValueError('Kies een geldige resolutie en een beeldmarge van 0 tot 10%.')
        current = self.settings()
        decoder = current['decoder'] if decoder is None else decoder
        show_fps = current['show_fps'] if show_fps is None else show_fps
        if decoder not in ('auto', 'software') or type(show_fps) is not bool:
            raise ValueError('Kies automatisch of software voor decodering en een geldige FPS-instelling.')
        refresh_rate = current['refresh_rate'] if refresh_rate is None else refresh_rate
        if refresh_rate not in REFRESH_RATES or (resolution == 'auto' and refresh_rate != 'auto'):
            raise ValueError('Kies een geldige verversingssnelheid en een vaste resolutie bij een vaste Hz-instelling.')
        if self.native and refresh_rate != 'auto' and refresh_rate not in self.status()['available_rates'].get(resolution, []):
            raise ValueError('Deze combinatie van resolutie en Hz wordt niet door het scherm aangeboden.')
        if self.native and resolution != 'auto' and resolution not in self.status()['available']:
            raise ValueError('Deze resolutie wordt niet door het aangesloten scherm aangeboden.')
        with self.store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           [('display_resolution', resolution), ('display_margin', str(margin)),
                            ('display_decoder', decoder), ('display_show_fps', '1' if show_fps else '0'),
                            ('display_refresh_rate', refresh_rate)])

        print(f'Beeldinstelling opgeslagen: resolutie={resolution}, marge={margin}%, tv-app={self.native}', flush=True)

    def apply(self):
        self.error = ''
        if not self.native:
            return
        current = self.query()
        settings = self.settings()
        resolution = settings['resolution']
        rate = settings['refresh_rate']
        if not current or (resolution != 'auto' and resolution not in current['modes']):
            self.error = 'De ingestelde resolutie is nu niet beschikbaar; het huidige beeld blijft behouden.'
            return
        if rate != 'auto' and rate not in current['rates'].get(resolution, []):
            self.error = 'De ingestelde Hz is nu niet beschikbaar; het huidige beeld blijft behouden.'
            return
        try:
            option = ['--auto'] if resolution == 'auto' else ['--mode', resolution]
            if rate != 'auto': option += ['--rate', rate]
            subprocess.run(['xrandr', '--output', current['output'], *option],
                           capture_output=True, timeout=10, check=True)
            print(f'Beeldresolutie toegepast: uitgang={current["output"]}, resolutie={resolution}', flush=True)
        except (OSError, subprocess.SubprocessError):
            print(f'Beeldresolutie toepassen mislukt: resolutie={resolution}', flush=True)
            self.error = 'De resolutie kon niet worden toegepast; het huidige beeld blijft behouden.'
