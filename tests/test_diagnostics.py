import tempfile
import unittest
from pathlib import Path
from mijntv.diagnostics import StreamWatchdog, safe_snapshot, configure_logging

class DiagnosticTests(unittest.TestCase):
    def test_progressing_stream_does_not_restart(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,180,2):
            self.assertFalse(w.stalled({'time-pos':t,'pause':False,'core-idle':False},t))

    def test_frozen_playback_and_loading_stall_report_once(self):
        for values in ({'time-pos':0}, {'core-idle':True,'seeking':True}):
            w=StreamWatchdog();w.reset(0)
            self.assertFalse(w.stalled(values,20))
            self.assertTrue(w.stalled(values,51))
            self.assertFalse(w.stalled(values,80))

    def test_missing_audio_detected_even_with_advancing_clock(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,50,2):
            self.assertFalse(w.stalled({'aid':1,'time-pos':t,'current-ao':None,'audio-out-params/samplerate':None},t))
        self.assertTrue(w.stalled({'aid':1,'time-pos':52,'current-ao':None,'audio-out-params/samplerate':None},52))

    def test_healthy_audio_output_is_not_mistaken_for_missing_audio(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,180,2):
            self.assertFalse(w.stalled({'aid':1,'time-pos':t,'current-ao':'alsa',
                                       'audio-out-params/samplerate':48000},t))

    def test_no_audio_track_does_not_count_as_failed_audio(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,100,2):
            self.assertFalse(w.stalled({'aid':False,'time-pos':t,'current-ao':None},t))

    def test_intentional_pause_exempt_and_new_channel_resets(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,120,2):
            self.assertFalse(w.stalled({'time-pos':1,'pause':True},t))
        w.reset(120)
        self.assertFalse(w.stalled({'time-pos':0},140))

    def test_black_picture_advancing_clock_reports_once(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,50,2):
            self.assertFalse(w.stalled({'time-pos':t,'video-black':True,'video-checked-at':t},t))
        self.assertTrue(w.stalled({'time-pos':50,'video-black':True,'video-checked-at':50},50))
        self.assertEqual(w.reason,'black_picture')
        self.assertFalse(w.stalled({'time-pos':70,'video-black':True,'video-checked-at':70},70))

    def test_short_black_intervals_stale_samples_and_pause_are_exempt(self):
        w=StreamWatchdog();w.reset(0)
        for t in range(0,160,2):
            black=t%40<20
            self.assertFalse(w.stalled({'time-pos':t,'video-black':black,'video-checked-at':t},t))
        w.reset(0)
        for t in range(0,100,2):
            self.assertFalse(w.stalled({'time-pos':t,'video-black':True,'video-checked-at':0},t))
        w.reset(0)
        for t in range(0,100,2):
            self.assertFalse(w.stalled({'time-pos':1,'pause':True,'video-black':True,'video-checked-at':t},t))

    def test_snapshot_excludes_credentials_and_arbitrary_strings(self):
        value=safe_snapshot({'path':'https://user:secret@provider/stream',
                             'time-pos':3,'current-ao':'https://secret',
                             'demuxer-cache-state':{'cache-duration':10,'secret':'password'}})
        self.assertEqual(value['time-pos'],3)
        self.assertNotIn('secret',str(value))
        self.assertNotIn('provider',str(value))
        self.assertEqual(value['demuxer-cache-state'],{'cache-duration':10})

    def test_ipc_diagnostics_never_forward_raw_errors_or_urls(self):
        import socket, queue, threading, json
        from mijntv.player import Player
        left, right = socket.socketpair()
        player = Player.__new__(Player)
        player.connection = left
        player.status_lock = threading.Lock()
        player.status = {}
        player.events = queue.Queue()
        player.last_warning = 0
        thread = threading.Thread(target=player._read)
        thread.start()
        messages = [
            {'event':'log-message','text':'HTTP error https://user:secret@provider/stream'},
            {'request_id':'health:time-pos','data':12},
            {'request_id':'health:path','data':'https://secret@provider'},
        ]
        right.sendall(('\n'.join(json.dumps(m) for m in messages)+'\n').encode())
        right.shutdown(socket.SHUT_WR)
        thread.join(timeout=2)
        try:
            self.assertFalse(thread.is_alive())
            self.assertEqual(player.snapshot()['time-pos'],12)
            events=[]
            while not player.events.empty(): events.append(player.events.get())
            self.assertNotIn('secret',str(events)+str(player.snapshot()))
            self.assertEqual(events[0]['category'],'network')
        finally:
            left.close();right.close()
