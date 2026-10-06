import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from mijntv.recordings import MIN_FREE, Recordings
from mijntv.store import Store


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="test",Test\nhttps://example.test/private-stream?password=secret')
        self.channel = self.store.catalog()['items'][0]['id']
        self.store.add_favorite(self.channel)
        self.recorder = Recordings(self.store)
        self.addCleanup(self.recorder.close)
        self.now = int(time.time())
        with self.store.connect() as db:
            db.execute('INSERT INTO epg_programmes VALUES (?,?,?,?)', ('test', self.now-10, self.now+100, 'Nu'))
            db.execute('INSERT INTO epg_programmes VALUES (?,?,?,?)', ('test', self.now+100, self.now+200, 'Straks'))

    def schedule(self, start=None):
        with patch('mijntv.recordings.shutil.which', return_value='/usr/bin/ffmpeg'):
            return self.recorder.schedule(self.channel, start)

    def test_epg_schedule_conflicts_and_restart(self):
        identifier = self.schedule(self.now+100)
        fresh = Recordings(self.store)
        self.assertEqual(fresh.list()[0]['state'], 'queued')
        self.assertEqual(fresh.list()[0]['title'], 'Straks')
        with self.assertRaisesRegex(ValueError, 'tijdvak'):
            self.schedule(self.now+100)
        self.recorder.cancel(identifier)
        self.assertEqual(self.recorder.list()[0]['state'], 'cancelled')
        self.assertNotIn('secret', str(self.recorder.list()))
        self.assertNotIn('url', self.recorder.list()[0])
        self.assertEqual(len(self.recorder.guide(self.channel)['programmes']), 2)

    def test_storage_change_keeps_scheduled_location_and_free_space(self):
        identifier = self.schedule()
        old_directory = self.recorder.directory()
        new_directory = Path(self.directory.name)/'other'
        status = self.recorder.save_storage(str(new_directory))
        self.assertTrue(status['available'])
        self.assertEqual(status['free'], shutil.disk_usage(new_directory).free)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM recordings WHERE id=?', (identifier,)).fetchone()
        self.assertEqual(row['directory'], str(old_directory))
        for invalid in ('relative', '/var/media/disconnected/recordings', str(new_directory/'missing'/'leaf')):
            with self.assertRaises(ValueError):
                self.recorder.save_storage(invalid)
        self.assertEqual(self.recorder.directory(), new_directory)

    def test_missing_or_full_storage_preserves_partial_file(self):
        identifier = self.schedule()
        process = Mock(); process.poll.return_value = None
        with patch('mijntv.recordings.subprocess.Popen', return_value=process) as launch:
            self.recorder.step(self.now)
        args = launch.call_args.args[0]
        self.assertNotIn('secret', str(args))
        manifest = Path(self.recorder.manifest)
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        self.assertIn('password=secret', manifest.read_text())
        path = self.recorder.directory()/(identifier+'.ts'); path.write_bytes(b'partial')
        with patch('mijntv.recordings.shutil.disk_usage', return_value=Mock(free=MIN_FREE-1)):
            self.recorder.step(self.now+1)
        process.send_signal.assert_called_with(signal.SIGINT)
        process.poll.return_value = 255
        self.recorder.step(self.now+2)
        self.assertFalse(manifest.exists())
        self.assertEqual(self.recorder.list()[0]['state'], 'failed')
        self.assertTrue(self.recorder.list()[0]['playable'])
        self.recorder.delete(identifier)
        self.assertFalse(path.exists())

    def test_restart_marks_active_interrupted_and_missed_schedule_failed(self):
        identifier = self.schedule()
        self.recorder.update(identifier, 'recording')
        stop = threading.Event(); stop.set()
        self.recorder.run(stop)
        self.assertEqual(self.recorder.list()[0]['state'], 'interrupted')
        self.recorder.update(identifier, 'queued')
        self.recorder.step(self.now+300)
        self.assertEqual(self.recorder.list()[0]['state'], 'failed')

    def test_refuses_invalid_programme_and_low_space(self):
        for start in (True, 'text', self.now-9999):
            with self.assertRaises(ValueError):
                self.schedule(start)
        with patch('mijntv.recordings.shutil.disk_usage', return_value=Mock(free=MIN_FREE-1)):
            with self.assertRaisesRegex(ValueError, '512 MB'):
                self.schedule()

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg nodig voor echte opname')
    def test_real_http_stream_finishes_at_epg_end_and_contains_audio_video(self):
        clip = Path(self.directory.name)/'fixture.ts'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','2',
            '-c:v','mpeg2video','-c:a','mp2','-f','mpegts',str(clip)], check=True)
        data = clip.read_bytes()
        class LiveStream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Type','video/mp2t'); self.end_headers()
                try:
                    while True:
                        for offset in range(0,len(data),188*20):
                            self.wfile.write(data[offset:offset+188*20]); self.wfile.flush(); time.sleep(.01)
                except (BrokenPipeError,ConnectionResetError): pass
        http = ThreadingHTTPServer(('127.0.0.1',0), LiveStream)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        with self.store.connect() as db:
            db.execute('UPDATE channels SET url=? WHERE id=?', (f'http://127.0.0.1:{http.server_port}/live?password=test-secret',self.channel))
            db.execute('UPDATE epg_programmes SET end=? WHERE title=?', (int(time.time())+8,'Nu'))
        identifier = self.schedule()
        deadline = time.monotonic()+22
        while time.monotonic()<deadline:
            self.recorder.step()
            if self.recorder.list()[0]['state'] not in ('queued','recording'): break
            time.sleep(.1)
        self.assertEqual(self.recorder.list()[0]['state'],'completed',self.recorder.list())
        path = self.recorder.playback(identifier)
        self.assertGreater(path.stat().st_size,1000)
        result = subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type','-of','csv=p=0',str(path)], capture_output=True,check=True).stdout
        self.assertIn(b'video',result); self.assertIn(b'audio',result)
        self.assertIsNone(self.recorder.manifest)


if __name__ == '__main__': unittest.main()
