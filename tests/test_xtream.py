import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from mijntv.xtream import live_playlist
from mijntv.store import parse_m3u


class Response(io.BytesIO):
    def geturl(self):
        return 'http://provider.test/player_api.php'


class XtreamTests(unittest.TestCase):
    source = 'http://provider.test/get.php?username=test%27user&password=a%26b%2Fc&type=m3u_plus'

    def responses(self, request, timeout):
        q = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(q['username'], ["test'user"])
        self.assertEqual(q['password'], ['a&b/c'])
        action = q.get('action', [''])[0]
        value = {'user_info': {'auth': 1, 'status': 'Active'}}
        if action == 'get_live_streams':
            value = [{'stream_id': 123, 'name': 'NL| NPO 1 HD', 'epg_channel_id': 'NPO1.nl', 'category_id': '7'},
                     {'stream_id': '../bad', 'name': 'invalid'}]
        if action == 'get_live_categories':
            value = [{'category_id': '7', 'category_name': 'NL| TV'}]
        return Response(json.dumps(value).encode())

    def test_catalog_preserves_identity_and_encodes_credentials(self):
        with patch('mijntv.xtream.urlopen', side_effect=self.responses):
            with live_playlist(self.source) as text:
                rows = parse_m3u(text.read())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:3], ('NL| NPO 1 HD', 'NL| TV'))
        self.assertEqual(rows[0][4], 'NPO1.nl')
        self.assertIn("/test%27user/a%26b%2Fc/123.ts", rows[0][3])

    def test_refused_login_and_network_errors_do_not_expose_secrets(self):
        for result in [Response(b'{"user_info":{"auth":0}}'), OSError(self.source)]:
            kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
            with patch('mijntv.xtream.urlopen', **kwargs):
                with self.assertRaises(ValueError) as error:
                    live_playlist(self.source)
                self.assertNotIn('test%27user', str(error.exception))
                self.assertNotIn('a%26b', str(error.exception))

    def test_non_xtream_playlist_does_not_guess_an_api(self):
        with patch('mijntv.xtream.urlopen') as request:
            with self.assertRaises(ValueError):
                live_playlist('http://provider.test/custom.m3u')
            request.assert_not_called()
