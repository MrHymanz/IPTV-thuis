"""Bounded, credential-free stream diagnostics and stall detection."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROPERTIES = ('aid', 'time-pos', 'pause', 'core-idle', 'seeking', 'paused-for-cache',
              'audio-params/samplerate', 'audio-out-params/samplerate', 'audio-pts', 'current-ao', 'hwdec-current',
              'decoder-frame-drop-count', 'frame-drop-count', 'demuxer-cache-state',
              'video-black', 'video-checked-at')
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
        self.black_since = None
        self.reason = 'stalled'
        self.reported = False

    def stalled(self, values, now):
        if not hasattr(self, 'started'):
            self.reset(now)
        if values.get('pause') is True:
            self.progress = now
            self.bad_since = None
            self.black_since = None
            return False
        position = values.get('time-pos')
        if isinstance(position, (int, float)) and position != self.position:
            self.position, self.progress = position, now
        bad = (values.get('core-idle') is True or values.get('seeking') is True
               or values.get('paused-for-cache') is True
               or (isinstance(values.get('aid'), int) and not isinstance(values.get('aid'), bool)
                   and not values.get('current-ao')
                   and not values.get('audio-out-params/samplerate')))
        if bad:
            if self.bad_since is None:
                self.bad_since = now
        else:
            self.bad_since = None
        checked = values.get('video-checked-at')
        picture_fresh = isinstance(checked, (int, float)) and 0 <= now-checked <= 20
        if picture_fresh and values.get('video-black') is True:
            if self.black_since is None:
                self.black_since = now
        else:
            self.black_since = None
        black = self.black_since is not None and now-self.black_since >= 45
        stuck = now-self.progress >= 20 or (self.bad_since is not None and now-self.bad_since >= 20)
        if now-self.started >= 50 and (stuck or black) and not self.reported:
            self.reason = 'black_picture' if black else 'stalled'
            self.reported = True
            return True
        return False
