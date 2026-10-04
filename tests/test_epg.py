import gzip
import io
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from mijntv.epg import EPG, LimitedReader, epg_url, parse_xmltv, xmltv_time
from mijntv.store import Store

SOURCE = 'https://example.test/panel/get.php?username=test%26user&password=fake%2Bpassword&type=m3u_plus'
XML = b'''<tv>
<programme channel="one.nl" start="20261004200000 +0200" stop="20261004203000 +0200"><title lang="en">English</title><title lang="nl">Nieuws &amp; weer</title></programme>
<programme channel="other.nl" start="20261004200000 +0200" stop="20261004203000 +0200"><title>Andere zender</title></programme>
<programme channel="one.nl" start="20261004203000 +0200" stop="20261004210000 +0200"><title>Volgende uitzending</title></programme>
</tv>'''
NOW = xmltv_time('20261004201500 +0200')


class EPGTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one.nl",NPO 1\nhttps://example.test/live/1.ts\n', SOURCE)
        self.channel = self.store.catalog()['items'][0]['id']
        self.store.add_favorite(self.channel)
        self.epg = EPG(self.store)

    def test_xtream_endpoint_preserves_encoded_credentials(self):
        result = urlsplit(epg_url(SOURCE))
        self.assertEqual(result.path, '/panel/xmltv.php')
        self.assertEqual(parse_qs(result.query), {'username':['test&user'], 'password':['fake+password']})
        self.assertEqual(epg_url('https://example.test/playlist.m3u'), '')

    def test_timezone_offsets_and_utc_default(self):
        self.assertEqual(xmltv_time('20261004200000 +0200'), xmltv_time('20261004180000'))
        self.assertEqual(xmltv_time('202610042000 +0200'), xmltv_time('20261004180000 Z'))
        self.assertEqual(xmltv_time('20261025023000 +0200')+3600, xmltv_time('20261025023000 +0100'))
        with self.assertRaises(ValueError):
            xmltv_time('invalid')

    def test_favorite_id_matching_language_and_renaming(self):
        self.assertEqual(self.epg.refresh(io.BytesIO(XML), NOW), 2)
        guide = self.store.programmes(NOW)[self.channel]
        self.assertEqual(guide['now']['title'], 'Nieuws & weer')
        self.assertEqual(guide['next']['title'], 'Volgende uitzending')
        self.store.rename(self.channel, 'Nederland 1')
        self.assertEqual(self.store.programmes(NOW)[self.channel], guide)
        with self.store.connect() as db:
            self.assertEqual(db.execute('select count(*) from epg_programmes').fetchone()[0], 2)

    def test_transition_and_expiry_do_not_show_old_programmes(self):
        self.epg.refresh(io.BytesIO(XML), NOW)
        boundary = xmltv_time('20261004203000 +0200')
        guide = self.store.programmes(boundary)[self.channel]
        self.assertEqual(guide['now']['title'], 'Volgende uitzending')
        self.assertIsNone(guide['next'])
        self.assertEqual(self.store.programmes(boundary+1800)[self.channel], {'now': None, 'next': None})

    def test_failed_or_empty_refresh_preserves_cache_and_favorites(self):
        self.epg.refresh(io.BytesIO(XML), NOW)
        before = self.store.programmes(NOW)
        for bad in [b'<tv><programme', b'<html>Error</html>', b'<tv></tv>']:
            with self.assertRaises(ValueError):
                self.epg.refresh(io.BytesIO(bad), NOW)
            self.assertEqual(self.store.programmes(NOW), before)
            self.assertEqual(self.store.favorites()[0]['id'], self.channel)

    def test_optional_stop_and_invalid_programmes(self):
        xml = XML.replace(b' stop="20261004203000 +0200"', b'', 1)
        rows = parse_xmltv(io.BytesIO(xml), {'one.nl'}, NOW)
        self.assertEqual(rows[0][2], rows[1][1])
        self.assertEqual(len(rows), 2)
        invalid = XML.replace(b'20261004200000', b'invalid')
        self.assertEqual(len(parse_xmltv(io.BytesIO(invalid), {'one.nl'}, NOW)), 1)

    def test_gzip_download_and_credential_redaction(self):
        class Response(io.BytesIO):
            def geturl(self):
                return 'https://example.test/xmltv.php'
        with patch('mijntv.epg.urlopen', return_value=Response(gzip.compress(XML))):
            self.assertEqual(self.epg.refresh(now=NOW), 2)
        result = repr(self.store.favorites())
        self.assertNotIn('fake+password', result)
        self.assertNotIn('username=', result)

    def test_bounded_reader(self):
        reader = LimitedReader(io.BytesIO(b'12345'), limit=4)
        with self.assertRaises(ValueError):
            reader.read(8)

    def test_changed_configuration_does_not_publish_old_feed(self):
        self.epg.refresh(io.BytesIO(XML), NOW)
        original = self.epg.configuration()
        with patch.object(self.epg, 'configuration', side_effect=[original, (original[0],original[1],'changed')]):
            with self.assertRaises(ValueError):
                self.epg.refresh(io.BytesIO(XML), NOW)
        self.assertEqual(self.store.programmes(NOW)[self.channel]['now']['title'], 'Nieuws & weer')


if __name__ == '__main__':
    unittest.main()
