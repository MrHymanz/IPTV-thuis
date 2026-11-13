import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from mijntv.backup import BackupAccount
from mijntv.mobile import MobilePlayback
from mijntv.recordings import Recordings
from mijntv.store import Store
from mijntv.streams import StreamHub, RecordingConflict


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args, self.code, self.signals = args, None, []
    def poll(self):
        return self.code
    def kill(self):
        self.code = -9
    def wait(self, timeout=None):
        return self.code
    def send_signal(self, value):
        self.signals.append(value)


class MobileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        playlist = '#EXTM3U\n#EXTINF:-1 tvg-id="npo1.nl" group-title="NL| TV",NL| NPO 1 HD\nhttps://provider.test/live/viewer/test-secret/1.ts\n'
        self.store.import_playlist(playlist,'https://provider.test/get.php?username=viewer&password=test-secret')
        self.store.add_favorite(self.store.catalog()['items'][0]['id'])
        with patch('mijntv.backup.live_playlist',return_value=io.StringIO(
                playlist.replace('provider.test','backup.test').replace('viewer/test-secret','alternate/backup-secret'))):
            BackupAccount(self.store).save('https://backup.test/get.php?username=alternate&password=backup-secret')
        self.store.set_setting('recording_source','https://recorder.test/get.php?username=recorder&password=record-secret')
        self.hub = StreamHub(self.store)
        self.addCleanup(self.hub.close)
        self.recordings = Recordings(self.store,self.hub)
        self.addCleanup(self.recordings.close)
        self.mobile = MobilePlayback(self.store,self.recordings,self.hub)
        self.addCleanup(self.mobile.close)
        self.channel = self.store.favorites(playback=True)[0]
        self.patch = patch('mijntv.mobile.subprocess.Popen',side_effect=FakeProcess)
        self.process = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.available = patch('mijntv.mobile.shutil.which',return_value='/usr/bin/ffmpeg')
        self.available.start()
        self.addCleanup(self.available.stop)

    def play(self, **kwargs):
        return self.mobile.start(dict(kind='live',id=self.channel['id'],**kwargs))

    def tv(self):
        source=next(s for s in self.channel['sources'] if s['account']=='primary')
        self.hub.view(self.channel,source)
        return self.hub.viewer

    def test_converter_diagnostics_keep_only_safe_failure_category(self):
        session = {'process': Mock(stderr=io.BytesIO(
            b'[http] HTTP error 403 Forbidden https://provider.test/movie/user/private-password/1.mp4\n')),
                   'failure': 'unknown'}
        self.mobile._diagnose(session)
        self.assertEqual(session['failure'], 'provider_denied')
        self.assertNotIn('private-password', session['failure'])

    def test_movie_number_is_not_mistaken_for_http_status(self):
        session = {'process': Mock(stderr=io.BytesIO(
            b'Invalid data found when processing input https://provider.test/movie/user/password/403.mp4\n')),
                   'failure': 'unknown'}
        self.mobile._diagnose(session)
        self.assertEqual(session['failure'], 'unknown')

    def test_tv_uses_primary_phone_uses_backup_without_touching_tv(self):
        feed=self.tv()
        result=self.play()
        self.assertEqual(self.hub.mobile['key'],self.hub.account_key('backup'))
        self.assertIs(self.hub.viewer,feed)
        self.assertFalse(feed.closed)
        self.assertNotIn('test-secret',json.dumps(result))
        self.assertNotIn('backup-secret',json.dumps(result))
        self.assertNotIn('backup.test',json.dumps(result))
        process=self.mobile.sessions[result['token']]['process']
        self.assertNotIn('backup-secret',' '.join(process.args))

    def test_tv_preempts_phone_and_phone_can_move_to_backup(self):
        first=self.play()
        process=self.mobile.sessions[first['token']]['process']
        feed=self.tv()
        self.assertEqual(self.mobile.status(first['token'])['state'],'preempted')
        self.assertIsNotNone(process.poll())
        second=self.play(session=first['token'],retry=True)
        self.assertEqual(self.hub.mobile['key'],self.hub.account_key('backup'))
        self.assertFalse(feed.closed)
        self.assertNotEqual(first['token'],second['token'])

    def test_recording_preempts_mobile_on_same_login(self):
        result=self.play()
        self.hub.acquire('primary',self.channel,self.channel['sources'][0],recording=True)
        self.assertEqual(self.mobile.status(result['token'])['state'],'preempted')
        self.assertTrue(self.hub.slots['primary'].recording)

    def test_recording_account_is_not_used_when_viewing_accounts_are_busy(self):
        feed=self.tv()
        self.hub.reserve_account('other-viewer','backup')
        with self.assertRaises(RecordingConflict):
            self.play()
        self.process.assert_not_called()
        self.assertFalse(feed.closed)

    def test_aliases_of_same_account_do_not_create_extra_connection(self):
        self.store.set_setting('backup_source','https://alias.test/get.php?username=viewer&password=test-secret')
        self.tv()
        with self.assertRaises(RecordingConflict):
            self.play()
        self.process.assert_not_called()

    def test_live_and_recording_alias_can_share_only_the_same_channel(self):
        self.store.set_setting('recording_source','https://alias.test/get.php?username=viewer&password=test-secret')
        tv=self.tv()
        source=dict(self.channel['sources'][0],account='recording')
        feed=self.hub.acquire('secondary',self.channel,source,recording=True)
        self.assertIs(feed,tv)
        self.assertTrue(feed.recording)
        with self.assertRaises(RecordingConflict):
            self.hub.acquire('secondary',dict(self.channel,id='other-channel'),source,recording=True)
        self.assertFalse(tv.closed)

    def test_account_reservation_survives_configuration_changes(self):
        old=self.hub.account_key('primary')
        self.tv()
        self.store.set_setting('source','https://changed.test/get.php?username=new-viewer&password=new-secret')
        self.assertIn(old,self.hub.occupied_accounts())

    def test_second_phone_cannot_replace_active_session(self):
        first=self.play()
        with self.assertRaises(RecordingConflict):
            self.play(session='wrong-token')
        self.assertEqual(self.mobile.current,first['token'])
        self.assertIsNone(self.mobile.sessions[first['token']]['process'].poll())

    def test_new_phone_can_replace_expired_session_without_monitor_tick(self):
        first = self.play()
        session = self.mobile.sessions[first['token']]
        session['seen'] = time.monotonic() - 46
        second = self.play()
        self.assertNotEqual(first['token'], second['token'])
        self.assertIsNotNone(session['process'].poll())
        self.assertNotIn(first['token'], self.mobile.sessions)

    def test_new_phone_can_replace_exited_transcoder(self):
        first = self.play()
        self.mobile.sessions[first['token']]['process'].code = 1
        second = self.play()
        self.assertNotEqual(first['token'], second['token'])
        self.assertNotIn(first['token'], self.mobile.sessions)

    def test_tv_archive_reserves_primary_and_preempts_phone(self):
        first=self.play()
        self.recordings.reserve_archive(lambda:None)
        self.assertEqual(self.mobile.status(first['token'])['state'],'preempted')
        second=self.play(session=first['token'])
        self.assertEqual(self.hub.mobile['key'],self.hub.account_key('backup'))
        self.recordings.release_archive()
        self.assertNotIn(self.hub.account_key('primary'),self.hub.external_accounts.values())

    def test_session_expires_and_releases_provider_connection(self):
        result=self.play()
        session=self.mobile.sessions[result['token']]
        session['seen']=time.monotonic()-46
        self.mobile.expire()
        self.assertIsNone(self.hub.mobile)
        self.assertIsNotNone(session['process'].poll())
        self.assertNotIn(result['token'],self.mobile.sessions)

    def test_assets_are_private_and_path_traversal_is_refused(self):
        result=self.play()
        root=Path(self.mobile.sessions[result['token']]['directory'].name)
        (root/'index.m3u8').write_text('#EXTM3U\nsegment000001.ts\n')
        self.assertEqual(self.mobile.asset(result['token'],'index.m3u8')[1],'application/vnd.apple.mpegurl')
        for path in ['../input.ffconcat','input.ffconcat','/etc/passwd','segment1.ts']:
            with self.assertRaises(ValueError):
                self.mobile.asset(result['token'],path)
        self.mobile.stop(result['token'])
        with self.assertRaises(ValueError):
            self.mobile.asset(result['token'],'index.m3u8')

    def test_recording_playback_and_pause_do_not_use_provider_accounts(self):
        directory=Path(self.temp.name)/'recordings'
        directory.mkdir(exist_ok=True)
        (directory/'recording.ts').write_bytes(b'fixture')
        with self.store.connect() as db:
            db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?)',
                       ('recording',self.channel['id'],'NPO 1','Test opname',0,100,'completed',str(directory),''))
        self.tv()
        self.hub.reserve_account('other','backup')
        with patch.object(self.mobile,'duration',return_value=100):
            result=self.mobile.start({'kind':'recording','id':'recording','position':15})
        session=self.mobile.sessions[result['token']]
        self.assertIsNone(self.hub.mobile)
        self.assertEqual(result['position'],15)
        self.mobile.pause(result['token'],True)
        self.mobile.pause(result['token'],False)
        self.assertEqual(session['process'].signals,[signal.SIGSTOP,signal.SIGCONT])
        self.assertEqual(session['kind'],'recording')

    def vod_fixture(self):
        def response(configuration, action, **params):
            return {'get_vod_categories': [{'category_id': '1', 'category_name': 'HBO'}],
                    'get_vod_streams': [{'stream_id': '77', 'name': 'Testfilm', 'category_id': '1', 'container_extension': 'mkv'}],
                    'get_vod_info': {'info': {'duration_secs': 3600}, 'movie_data': {'container_extension': 'mkv'}},
                    'get_series_categories': [{'category_id': '1', 'category_name': 'Netflix'}],
                    'get_series': [{'series_id': '77', 'name': 'Testserie', 'category_id': '1'}],
                    'get_series_info': {'info': {}, 'episodes': {'1': [{'id': '88', 'title': 'Aflevering', 'info': {'duration_secs': 1800}}]}}}[action]
        patcher = patch.object(self.mobile.vod.provider, 'request', side_effect=response)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.mobile.vod.catalog('movie')['signature']

    def test_movie_playback_keeps_credentials_in_private_manifest_and_resumes(self):
        signature = self.vod_fixture()
        result = self.mobile.start({'kind': 'movie', 'id': '77', 'signature': signature, 'position': 123})
        session = self.mobile.sessions[result['token']]
        self.assertEqual(result['title'], 'Testfilm')
        self.assertEqual(result['position'], 123)
        self.assertEqual(result['duration'], 3600)
        self.assertEqual(session['account'], self.hub.account_key('primary'))
        self.assertNotIn('test-secret', json.dumps(result))
        self.assertNotIn('test-secret', ' '.join(session['process'].args))
        manifest = Path(session['directory'].name)/'input.ffconcat'
        self.assertIn('/movie/viewer/test-secret/77.mkv', manifest.read_text())
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        self.mobile.pause(result['token'], True)
        self.assertEqual(session['process'].signals, [signal.SIGSTOP])
        self.tv()
        self.assertEqual(self.mobile.status(result['token'])['state'], 'preempted')

    def test_vod_never_uses_backup_ids_or_interrupts_an_active_tv(self):
        signature = self.vod_fixture()
        tv = self.tv()
        with self.assertRaises(RecordingConflict):
            self.mobile.start({'kind': 'movie', 'id': '77', 'signature': signature})
        self.assertFalse(tv.closed)
        self.process.assert_not_called()

    def test_free_accounts_in_error_cooldown_are_retried_instead_of_reported_busy(self):
        first = self.play()
        self.mobile._stop(self.mobile.sessions[first['token']], 'error')
        second = self.play(session=first['token'])
        self.assertEqual(self.mobile.sessions[second['token']]['account'], self.hub.account_key('backup'))
        self.mobile._stop(self.mobile.sessions[second['token']], 'error')
        with self.assertLogs('mijntv.stream', level='INFO') as captured:
            third = self.play(session=second['token'], retry=True)
        self.assertEqual(third['state'], 'loading')
        self.assertIn('recovery=True', ' '.join(captured.output))
        self.assertNotIn('secret', ' '.join(captured.output))

    def test_cooldown_retry_never_takes_a_tv_account(self):
        tv = self.tv()
        key = self.hub.account_key('backup')
        self.mobile.avoid[key] = time.monotonic()+45
        result = self.play()
        self.assertEqual(self.mobile.sessions[result['token']]['account'], key)
        self.assertFalse(tv.closed)

    def test_archive_restriction_explains_why_a_free_backup_cannot_be_used(self):
        self.tv()
        with self.assertRaisesRegex(RecordingConflict, 'backupaccount kan dit archief niet overnemen'):
            self.mobile._source(self.channel, archive=True)

    def test_episode_uses_selected_series_episode_and_actual_duration(self):
        signature = self.vod_fixture()
        self.mobile.vod.catalog('series')
        self.mobile.vod.detail('series', '77')
        result = self.mobile.start({'kind': 'episode', 'id': '88', 'signature': signature})
        self.assertEqual(result['title'], 'Aflevering')
        self.assertEqual(result['channel'], 'Testserie')
        self.assertEqual(result['duration'], 1800)
        session = self.mobile.sessions[result['token']]
        self.assertIn('/series/viewer/test-secret/88.mp4', (Path(session['directory'].name)/'input.ffconcat').read_text())


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg needed for real HLS playback')
class MobileHLSIntegration(unittest.TestCase):
    def test_movie_and_episode_have_short_playable_segments_including_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '10',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', '-c:a', 'aac',
                            '-movflags', '+faststart', str(root/'video.mp4')], check=True, capture_output=True)
            http = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietFiles, directory=directory))
            threading.Thread(target=http.serve_forever, daemon=True).start()
            store = Store(root/'data')
            hub = StreamHub(store)
            recordings = Recordings(store, hub)
            vod = Mock()
            vod.playback.return_value = {'url': 'http://127.0.0.1:'+str(http.server_port)+'/video.mp4',
                                         'configuration': ('fixture', {}, 'signature'), 'title': 'Film',
                                         'label': 'Film', 'duration': 10}
            mobile = MobilePlayback(store, recordings, hub, vod)
            try:
                for kind, offset in [('movie', 0), ('episode', 3)]:
                    with self.subTest(kind=kind):
                        with self.assertLogs('mijntv.stream', level='INFO') as captured:
                            player = mobile.start({'kind': kind, 'id': '7', 'signature': 'signature', 'position': offset})
                            deadline = time.monotonic()+12
                            while time.monotonic() < deadline:
                                state = mobile.status(player['token'])
                                if state['ready'] or state['state'] == 'error':
                                    break
                                time.sleep(.1)
                        self.assertTrue(state['ready'], state)
                        self.assertEqual(state['position'], offset)
                        playlist, _ = mobile.asset(player['token'], 'index.m3u8')
                        lengths = [float(line.split(':')[1].rstrip(',')) for line in playlist.decode().splitlines() if line.startswith('#EXTINF:')]
                        self.assertTrue(lengths)
                        self.assertLessEqual(max(lengths), 1.1)
                        segment = next(line for line in playlist.decode().splitlines() if line.endswith('.ts'))
                        data, _ = mobile.asset(player['token'], segment)
                        (root/'segment.ts').write_bytes(data)
                        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name',
                                                '-of', 'json', str(root/'segment.ts')], capture_output=True, check=True)
                        self.assertTrue({'h264', 'aac'} <= {stream['codec_name'] for stream in json.loads(probe.stdout)['streams']})
                        self.assertIn('mobile_ready', ' '.join(captured.output))
                        mobile.stop(player['token'])
            finally:
                mobile.close(); recordings.close(); hub.close(); http.shutdown(); http.server_close()

    def test_real_hls_is_h264_aac_and_releases_on_tv_preemption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=15',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                            '-t','12','-c:v','mpeg2video','-c:a','mp2','-f','mpegts',str(root/'video.ts')],
                           check=True,capture_output=True)
            http=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietFiles,directory=directory))
            threading.Thread(target=http.serve_forever,daemon=True).start()
            store=Store(root/'data')
            store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="test",Test\nhttp://127.0.0.1:'+str(http.server_port)+'/video.ts\n')
            store.add_favorite(store.catalog()['items'][0]['id'])
            hub=StreamHub(store);recordings=Recordings(store,hub);mobile=MobilePlayback(store,recordings,hub)
            try:
                channel=store.favorites(playback=True)[0]
                player=mobile.start({'id':channel['id']})
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    state=mobile.status(player['token'])
                    if state['ready'] or state['state']=='error':
                        break
                    time.sleep(.2)
                self.assertTrue(state['ready'],state)
                playlist,mime=mobile.asset(player['token'],'index.m3u8')
                self.assertIn(b'#EXTM3U',playlist)
                self.assertNotIn(b'http://',playlist)
                segment=next(s for s in playlist.decode().splitlines() if s.endswith('.ts'))
                data,_=mobile.asset(player['token'],segment)
                path=root/'segment.ts';path.write_bytes(data)
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name',
                                      '-of','json',str(path)],check=True,capture_output=True)
                codecs={s['codec_name'] for s in json.loads(probe.stdout)['streams']}
                self.assertTrue({'h264','aac'}<=codecs)
                process=mobile.sessions[player['token']]['process']
                hub.view(channel,channel['sources'][0])
                self.assertIsNotNone(process.poll())
                self.assertEqual(mobile.status(player['token'])['state'],'preempted')
            finally:
                mobile.close();recordings.close();hub.close();http.shutdown();http.server_close()
