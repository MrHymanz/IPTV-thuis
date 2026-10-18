import io
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from mijntv.streams import Feed,StreamHub,RecordingConflict
from mijntv.store import Store
from mijntv.recordings import Recordings


class SharedStreamTests(unittest.TestCase):
    def setUp(self):
        self.requests=0
        self.payload=(b'\x47'+b'X'*187)*20
        owner=self
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                owner.requests+=1
                self.send_response(200); self.end_headers()
                try:
                    while True:
                        self.wfile.write(owner.payload);self.wfile.flush();time.sleep(.01)
                except OSError: pass
        self.provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        self.provider.daemon_threads=True
        threading.Thread(target=self.provider.serve_forever,daemon=True).start()
        self.hub=StreamHub()
        self.addCleanup(self.provider.server_close)
        self.addCleanup(self.provider.shutdown)
        self.addCleanup(self.hub.close)
        self.channel={'id':'one','label':'NPO 1'}
        self.source={'id':'primary','url':f'http://127.0.0.1:{self.provider.server_port}/live?password=private'}

    def test_small_network_fragments_do_not_exhaust_consumer_buffers(self):
        payload = (b'\x47' + b'X'*187)*1600
        class FragmentedResponse(io.BytesIO):
            def read1(self, size=-1):
                return self.read(min(size, 188))
        feed = Feed(self.channel, self.source)
        feed.started = True
        viewer = feed.subscribe()
        recorder = feed.subscribe()
        with patch('mijntv.streams.urlopen', return_value=FragmentedResponse(payload)):
            with self.assertLogs('mijntv.stream', level='INFO') as logs:
                feed.read()
        for consumer in (viewer, recorder):
            chunks = []
            while not consumer.empty():
                chunk = consumer.get_nowait()
                if chunk is not None:
                    chunks.append(chunk)
            self.assertEqual(b''.join(chunks), payload)
        self.assertFalse(any('stream_consumer_slow' in line for line in logs.output))

    def test_view_and_record_share_one_upstream_and_home_keeps_recording(self):
        first=self.hub.view(self.channel,self.source)
        view=urlopen(first,timeout=3);self.addCleanup(view.close)
        self.assertTrue(view.read(188))
        feed=self.hub.acquire('primary',self.channel,dict(self.source,id='other-working-preference'),recording=True)
        self.assertEqual(first,self.hub.url(feed))
        recorder=urlopen(self.hub.url(feed),timeout=3);self.addCleanup(recorder.close)
        self.assertTrue(recorder.read(188))
        self.assertEqual(self.requests,1)
        with self.assertRaises(RecordingConflict):
            self.hub.view({'id':'two','label':'NPO 2'},dict(self.source,id='two'))
        self.hub.stop_view();view.close()
        self.assertTrue(recorder.read(376))
        self.assertFalse(feed.closed)
        self.assertEqual(self.requests,1)
        self.hub.release_recording()
        self.assertTrue(feed.closed)
        self.assertIsNone(self.hub.required_channel())

    def test_second_account_keeps_recording_while_viewer_changes_channel(self):
        feed=self.hub.acquire('secondary',self.channel,self.source,recording=True)
        recorder=urlopen(self.hub.url(feed),timeout=3);self.addCleanup(recorder.close)
        self.assertTrue(recorder.read(188))
        other={'id':'two','label':'NPO 2'}
        view=urlopen(self.hub.view(other,dict(self.source,id='two')),timeout=3);self.addCleanup(view.close)
        self.assertTrue(view.read(188))
        self.assertIsNone(self.hub.required_channel())
        self.assertFalse(feed.closed)
        self.assertEqual(self.requests,2)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg integration')
    def test_real_recording_and_viewer_use_one_connection_and_produce_video(self):
        with tempfile.TemporaryDirectory() as directory:
            sample=Path(directory)/'sample.ts'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
                            '-t','2','-c:v','mpeg2video','-f','mpegts',str(sample)],check=True)
            self.payload=sample.read_bytes()
            store=Store(directory)
            output=Path(directory)/'recordings';output.mkdir()
            store.set_setting('recording_directory',str(output))
            store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one",NPO 1\n'+self.source['url'])
            identifier=store.catalog()['items'][0]['id'];store.add_favorite(identifier)
            channel=store.favorites(playback=True)[0]
            rec=Recordings(store,self.hub)
            view=urlopen(rec.viewing_url(channel,channel['sources'][0]),timeout=3)
            self.assertTrue(view.read(188))
            rid=rec.schedule(identifier);rec.step()
            try:
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    path=Path(rec.process.args[-1])
                    if path.exists() and path.stat().st_size>10000: break
                    time.sleep(.1)
                self.assertEqual(self.requests,1)
                rec.stop_view();view.close()
                before=rec.playback(rid).stat().st_size
                time.sleep(.3)
                self.assertGreaterEqual(rec.playback(rid).stat().st_size,before)
                rec.cancel(rid)
                rec.process.wait(timeout=5);rec.step()
                self.assertEqual(rec.list()[0]['state'],'cancelled')
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name','-of','csv=p=0',str(rec.playback(rid))],capture_output=True,text=True,check=True)
                self.assertIn('mpeg2video',probe.stdout)
                self.assertIsNone(self.hub.required_channel())
            finally:
                view.close();rec.close()


    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg integration')
    def test_real_recording_resumes_after_a_shared_feed_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            sample=Path(directory)/'sample.ts'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
                            '-t','2','-c:v','mpeg2video','-f','mpegts',str(sample)],check=True)
            self.payload=sample.read_bytes()
            output=Path(directory)/'recordings';output.mkdir()
            store=Store(directory);store.set_setting('recording_directory',str(output))
            store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one",NPO 1\n'+self.source['url'])
            identifier=store.catalog()['items'][0]['id'];store.add_favorite(identifier)
            rec=Recordings(store,self.hub)
            channel=store.favorites(playback=True)[0]
            view=urlopen(rec.viewing_url(channel,channel['sources'][0]),timeout=3)
            view.read(188)
            first_id=rec.schedule(identifier);rec.step()
            try:
                first_file=Path(rec.process.args[-1])
                deadline=time.monotonic()+8
                while time.monotonic()<deadline and (not first_file.exists() or first_file.stat().st_size<10000):
                    time.sleep(.1)
                self.assertTrue(first_file.exists())
                prefix=first_file.read_bytes()[:64]
                self.hub.slots['primary'].close('test_disconnect')
                view.close()
                second_id=None
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    rec.step()
                    if rec.active and rec.active!=first_id:
                        second_id=rec.active
                        second_file=Path(rec.process.args[-1])
                        if second_file.exists() and second_file.stat().st_size>10000:
                            break
                    time.sleep(.1)
                self.assertIsNotNone(second_id)
                self.assertGreater(second_file.stat().st_size,10000)
                self.assertEqual(self.requests,2,'One provider connection per sequential attempt')
                self.assertEqual(first_file.read_bytes()[:64],prefix)
                self.assertEqual(next(r for r in rec.list() if r['id']==first_id)['state'],'interrupted')
                rec.stop_for_zap();rec.process.wait(timeout=5);rec.step()
                for path in (first_file,second_file):
                    probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name','-of','csv=p=0',str(path)],capture_output=True,text=True,check=True)
                    self.assertIn('mpeg2video',probe.stdout)
            finally:
                view.close();rec.close()


class RecordingInputTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=Store(self.directory.name)
        self.primary='https://example.test/get.php?username=viewer&password=private'
        self.secondary='https://example.test/get.php?username=recorder&password=other-secret'
        self.playlist='#EXTM3U\n#EXTINF:-1 tvg-id="one" group-title="NL",NPO 1 HD\nhttps://example.test/live/one'
        self.store.import_playlist(self.playlist,self.primary)
        self.store.add_favorite(self.store.catalog()['items'][0]['id'])
        self.rec=Recordings(self.store);self.addCleanup(self.rec.close)

    def test_secondary_mapping_and_no_secrets_in_public_settings(self):
        with patch('mijntv.recordings.fetch_playlist',return_value=io.StringIO(self.playlist.replace('/one','/second-one'))):
            settings=self.rec.save_input(self.secondary)
        self.assertTrue(settings['configured'])
        self.assertNotIn('secret',str(settings))
        slot,source=self.rec.recording_source(self.store.favorites(playback=True)[0])
        self.assertEqual(slot,'secondary')
        self.assertTrue(source['url'].endswith('/second-one'))
        with self.store.connect() as db: db.execute('DELETE FROM recording_channels')
        with self.assertRaisesRegex(ValueError,'opnameaccount'):
            self.rec.recording_source(self.store.favorites(playback=True)[0])

    def test_same_account_rejected_and_invalid_import_preserves_previous(self):
        with self.assertRaisesRegex(ValueError,'tweede account'):
            self.rec.save_input(self.primary.replace('private','changed-password'))
        with patch('mijntv.recordings.fetch_playlist',return_value=io.StringIO(self.playlist)):
            self.rec.save_input(self.secondary)
        with patch('mijntv.recordings.fetch_playlist',return_value=io.StringIO('not a playlist')):
            with self.assertRaises(ValueError): self.rec.save_input('https://other.test/list')
        self.assertEqual(self.store.setting('recording_source'),self.secondary)
        self.assertEqual(self.rec.input_settings()['channels'],1)
        self.rec.save_input('')
        self.assertFalse(self.rec.input_settings()['configured'])
