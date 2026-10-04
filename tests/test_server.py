import base64
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

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
        for path in ('/admin', '/admin.js', '/api/favorites', '/api/status'):
            self.assertEqual(self.request('GET', path, auth=False)[0], 401)
        self.assertEqual(self.request('GET', '/admin')[0], 200)
        self.assertEqual(self.request('GET', '/api/channels?offset=no')[0], 400)

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

    def test_password_persisted_as_hash_and_not_plaintext(self):
        auth, generated = credentials(self.directory.name)
        self.assertIsNone(generated)
        self.assertEqual(auth, self.auth)
        self.assertNotIn('test-password', Path(self.directory.name, 'admin.json').read_text())


if __name__ == '__main__':
    unittest.main()
