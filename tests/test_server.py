import base64
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from mijntv.server import AdminServer, credentials
from mijntv.store import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.auth, _ = credentials(self.directory.name, 'test-password-123')
        self.server = AdminServer(('127.0.0.1', 0), self.store, self.auth, Path(__file__).resolve().parents[1]/'mijntv/web')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, body=None, auth=True, origin=None, custom=True):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=5)
        headers = {}
        if auth:
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'admin:test-password-123').decode()
        if body is not None:
            body = json.dumps(body)
            headers['Content-Type'] = 'application/json'
            if custom:
                headers['X-MijnTV'] = '1'
        if origin:
            headers['Origin'] = origin
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        status, result = response.status, response.read()
        connection.close()
        return status, result

    def test_admin_requires_auth_including_static_files(self):
        for path in ('/admin', '/admin.js', '/api/favorites', '/api/status', '/tv', '/tv.js', '/tv.css'):
            self.assertEqual(self.request('GET', path, auth=False)[0], 401)
        self.assertEqual(self.request('GET', '/admin')[0], 200)
        self.assertEqual(self.request('GET', '/api/channels?offset=no')[0], 400)

    def test_tv_preview_is_served_with_external_scripts(self):
        for path in ('/tv', '/tv/', '/tv.js', '/tv.css'):
            status, body = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
        html = self.request('GET', '/tv')[1]
        self.assertIn(b'src="/tv.js"', html)
        self.assertNotIn(b'<script>', html)
        self.assertIn(b'href="/admin"', html)

    def test_cross_origin_and_missing_header_are_rejected(self):
        data = {'label':'Test', 'url':'https://example.test/secret'}
        self.assertEqual(self.request('POST', '/api/favorites/manual', data, origin='https://evil.test')[0], 403)
        self.assertEqual(self.request('POST', '/api/favorites/manual', data, custom=False)[0], 403)
        self.assertEqual(self.store.favorites(), [])

    def test_import_search_add_rename_order_remove_and_secret_redaction(self):
        text = '#EXTM3U\n#EXTINF:-1,Test\nhttps://example.test/user/password/stream'
        self.assertEqual(self.request('POST', '/api/import', {'text':text})[0], 200)
        channels = json.loads(self.request('GET','/api/channels')[1])['items']
        channel_id = channels[0]['id']
        self.assertEqual(self.request('POST','/api/favorites/add',{'id':channel_id})[0], 200)
        self.assertEqual(self.request('POST','/api/favorites/rename',{'id':channel_id,'label':'Nieuwe naam'})[0], 200)
        self.assertEqual(self.request('POST','/api/favorites/order',{'ids':[channel_id]})[0], 200)
        for path in ('/api/channels', '/api/favorites', '/api/status'):
            self.assertNotIn(b'password', self.request('GET',path)[1])
        self.assertEqual(json.loads(self.request('GET','/api/favorites')[1])[0]['label'], 'Nieuwe naam')
        self.assertEqual(self.request('POST','/api/favorites/remove',{'id':channel_id})[0],200)
        self.assertEqual(self.store.favorites(), [])

    def test_invalid_payload_does_not_destroy_data(self):
        self.assertEqual(self.request('POST','/api/import',{'text':'invalid'})[0],400)
        self.assertEqual(self.request('POST','/api/favorites/manual',{'url':'https://example.test'})[0],400)
        self.assertEqual(self.request('POST','/api/not-found',{})[0],404)

    def test_raw_file_upload_and_invalid_replacement(self):
        def upload(body):
            connection = HTTPConnection('127.0.0.1',self.port,timeout=5)
            headers={'Authorization':'Basic '+base64.b64encode(b'admin:test-password-123').decode(),
                     'Content-Type':'application/octet-stream','X-MijnTV':'1'}
            connection.request('POST','/api/import/file',body,headers)
            response=connection.getresponse()
            result=(response.status,json.loads(response.read()))
            connection.close()
            return result
        body=b'#EXTM3U\n#EXTINF:-1,Test\nhttps://example.test/stream'
        self.assertEqual(upload(body),(200,{'ok':True,'imported':1}))
        self.assertEqual(upload(b'not a playlist')[0],400)
        self.assertEqual(self.store.catalog()['total'],1)

    def test_favorite_logo_is_proxied_cached_and_auth_protected(self):
        png=b'\x89PNG\r\n\x1a\n' + b'test-raster'
        requests=[]
        class Images(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200); self.send_header('Content-Length',str(len(png)))
                self.end_headers(); self.wfile.write(png)
        image_server=ThreadingHTTPServer(('127.0.0.1',0),Images)
        threading.Thread(target=image_server.serve_forever,daemon=True).start()
        try:
            logo=f'http://127.0.0.1:{image_server.server_port}/logo.png?secret=provider-password'
            self.store.import_playlist(f'#EXTM3U\n#EXTINF:-1 tvg-logo="{logo}",Test\nhttps://example.test/stream')
            channel_id=self.store.catalog()['items'][0]['id']; self.store.add_favorite(channel_id)
            favorite=json.loads(self.request('GET','/api/favorites')[1])[0]
            self.assertNotIn('provider-password',json.dumps(favorite))
            path=favorite['logo']
            self.assertTrue(path.startswith('/api/logos/'))
            self.assertEqual(self.request('GET',path,auth=False)[0],401)
            self.assertEqual(self.request('GET',path),(200,png))
            self.assertEqual(self.request('GET',path),(200,png))
            self.assertEqual(len(requests),1)
        finally:
            image_server.shutdown(); image_server.server_close()

    def test_password_persisted_as_hash_and_not_plaintext(self):
        auth, generated = credentials(self.directory.name)
        self.assertIsNone(generated)
        self.assertEqual(auth, self.auth)
        self.assertNotIn('test-password', Path(self.directory.name, 'admin.json').read_text())

    def test_xtream_import_and_refresh_through_real_provider_endpoint(self):
        requests = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append(self.path)
                body = ('#EXTM3U\n#EXTINF:-1 tvg-id="1",Test\nhttps://example.test/stream/' + str(len(requests))).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        provider = ThreadingHTTPServer(('127.0.0.1',0),Provider)
        thread = threading.Thread(target=provider.serve_forever,daemon=True)
        thread.start()
        try:
            body = {'server':f'http://127.0.0.1:{provider.server_port}', 'username':'name+&é', 'password':'secret?#&+'}
            status, result = self.request('POST','/api/import/xtream',body)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(result)['imported'],1)
            self.assertEqual(urlsplit(requests[0]).path,'/get.php')
            self.assertEqual(parse_qs(urlsplit(requests[0]).query)['password'],['secret?#&+'])
            channel_id = self.store.catalog()['items'][0]['id']
            self.store.add_favorite(channel_id)
            self.store.rename(channel_id,'Eigen naam')
            self.assertEqual(self.request('POST','/api/refresh',{})[0],200)
            self.assertEqual(len(requests),2)
            self.assertEqual(self.store.favorites(playback=True)[0]['url'],'https://example.test/stream/2')
            self.assertEqual(self.store.favorites()[0]['label'],'Eigen naam')
            for path in ('/api/status','/api/channels','/api/favorites'):
                self.assertNotIn(b'secret',self.request('GET',path)[1])
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join()

    def test_invalid_xtream_login_does_not_fetch_or_replace_existing_catalog(self):
        self.store.manual('Bestaand','https://example.test/stream')
        with patch('mijntv.store.fetch_playlist') as fetch:
            for body in ({'server':'file:///secret','username':'user','password':'secret'},
                         {'server':'https://example.test','username':'user'},
                         {'server':'https://example.test','username':'user','password':''}):
                self.assertEqual(self.request('POST','/api/import/xtream',body)[0],400)
            fetch.assert_not_called()
        self.assertEqual(self.store.catalog()['total'],1)

    def test_display_settings_require_login_and_validate_values(self):
        self.assertEqual(self.request('GET', '/api/display', auth=False)[0], 401)
        body = {'resolution':'3840x2160','margin':3}
        self.assertEqual(self.request('POST', '/api/display', body, custom=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/display', body)[0], 200)
        status, payload = self.request('GET', '/api/display')
        result = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual((result['resolution'], result['margin']), ('3840x2160', 3))
        self.assertFalse(result['native'])
        self.assertEqual(self.request('POST', '/api/display', {'resolution':'1920x1080','margin':-1})[0], 400)
        self.assertEqual(self.server.display.settings(), body)


    def test_recording_storage_and_epg_routes_are_authenticated_and_private(self):
        import time
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="test",Test\nhttps://example.test/stream?secret=provider-password')
        channel_id = self.store.catalog()['items'][0]['id']
        self.store.add_favorite(channel_id)
        now = int(time.time())
        with self.store.connect() as db:
            db.execute('INSERT INTO epg_programmes VALUES (?,?,?,?)', ('test',now+100,now+200,'Testprogramma'))
        for path in ('/api/storage','/api/recordings','/api/guide?id='+channel_id,'/api/recordings/file/test'):
            self.assertEqual(self.request('GET',path,auth=False)[0],401)
        directory = str(Path(self.directory.name)/'tv-recordings')
        self.assertEqual(self.request('POST','/api/storage',{'directory':directory},custom=False)[0],403)
        self.assertEqual(self.request('POST','/api/storage',{'directory':directory})[0],200)
        self.assertEqual(json.loads(self.request('GET','/api/storage')[1])['directory'],directory)
        self.assertEqual(json.loads(self.request('GET','/api/guide?id='+channel_id)[1])['programmes'][0]['title'],'Testprogramma')
        with patch('mijntv.recordings.shutil.which',return_value='/usr/bin/ffmpeg'):
            status,payload = self.request('POST','/api/recordings/schedule',{'channel_id':channel_id,'start':now+100})
        self.assertEqual(status,200)
        identifier = json.loads(payload)['id']
        self.assertNotIn(b'provider-password',self.request('GET','/api/recordings')[1])
        self.assertEqual(self.request('POST','/api/recordings/delete',{'id':identifier})[0],400)
        self.assertEqual(self.request('POST','/api/recordings/cancel',{'id':identifier})[0],200)
        Path(directory,identifier+'.ts').write_bytes(b'test-video')
        self.assertEqual(self.request('GET','/api/recordings/file/'+identifier),(200,b'test-video'))
        self.assertEqual(self.request('POST','/api/recordings/delete',{'id':identifier})[0],200)
        self.assertEqual(json.loads(self.request('GET','/api/recordings')[1]),[])

if __name__ == '__main__':
    unittest.main()
