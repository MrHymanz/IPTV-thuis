"""Bounded, credential-free stream diagnostics and stall detection."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROPERTIES = ('aid', 'time-pos', 'pause', 'core-idle', 'seeking', 'paused-for-cache',
              'audio-params/samplerate', 'audio-out-params/samplerate', 'audio-pts', 'current-ao', 'hwdec-current',
              'decoder-frame-drop-count', 'frame-drop-count', 'demuxer-cache-state')
CACHE_FIELDS = ('cache-duration', 'reader-pts', 'cache-end', 'raw-input-rate',
                'total-bytes', 'underrun', 'eof')


def safe_snapshot(values):
    result = {}
    for key in PROPERTIES:
        value = values.get(key)
        if key == 'demuxer-cache-state' and isinstance(value, dict):
            result[key] = {k: v for k, v in value.items() if k in CACHE_FIELDS
                           and isinstance(v, (bool, int, float))}
        elif isinstance(value, (bool, int, float)) or value is None:
            result[key] = value
        elif key in ('audio-format', 'current-ao', 'hwdec-current'):
            # Only known engine identifiers, never raw error text or URLs.
            result[key] = value if value in ('no', 'alsa', 'pulse', 'sdl', 'vaapi',
                                             'vaapi-copy', 'vdpau', 'gpu', 's16',
                                             's16p', 's32', 's32p', 'float', 'floatp',
                                             'double', 'doublep') else 'other'
    return result


def configure_logging(directory):
    logger = logging.getLogger('mijntv.stream')
    if not logger.handlers:
        handler = RotatingFileHandler(Path(directory)/'stream-diagnostics.log',
                                      maxBytes=1_000_000, backupCount=2)
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


class StreamWatchdog:
    def reset(self, now):
        self.started = self.progress = now
        self.position = None
        self.bad_since = None
        self.reported = False

    def stalled(self, values, now):
        if not hasattr(self, 'started'):
            self.reset(now)
        if values.get('pause') is True:
            self.progress = now
            self.bad_since = None
            return False
        position = values.get('time-pos')
        if isinstance(position, (int, float)) and position != self.position:
            self.position, self.progress = position, now
        bad = (values.get('core-idle') is True or values.get('seeking') is True
               or values.get('paused-for-cache') is True
               or (isinstance(values.get('aid'), int) and not values.get('current-ao')
                   and not values.get('audio-out-params/samplerate')))
        if bad:
            if self.bad_since is None:
                self.bad_since = now
        else:
            self.bad_since = None
        stuck = now-self.progress >= 20 or (self.bad_since is not None and now-self.bad_since >= 20)
        if now-self.started >= 50 and stuck and not self.reported:
            self.reported = True
            return True
        return False
