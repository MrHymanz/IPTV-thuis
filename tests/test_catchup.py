import base64
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from mijntv.catchup import Catchup, identity, text
from mijntv.recordings import Recordings
from mijntv.store import Store

SOURCE = 'https://provider.test/panel/get.php?username=test%26user&password=fake%2Bpass&type=m3u_plus'


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="two.nl" group-title="NL| ALGEMEEN",NL| NPO 2 FHD\nhttps://provider.test/live/a/b/1.ts', SOURCE)
        self.channel = self.store.catalog()['items'][0]['id']
        self.store.add_favorite(self.channel)
        self.catchup = Catchup(self.store)
        self.now = int(time.time())//60*60
        self.streams = [{'stream_id':1,'name':'NL| NPO 2 FHD','epg_channel_id':'two.nl','tv_archive':0},
                        {'stream_id':2,'name':'NL| NPO 2 HD','epg_channel_id':'two.nl','tv_archive':1,'tv_archive_duration':3}]
        self.entries = [self.programme(self.now-3600,self.now-1800,'Nieuws'),
                        self.programme(self.now+3600,self.now+5400,'Morgen')]
        self.calls = []
        self.patcher = patch.object(self.catchup, 'request', side_effect=self.request)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def programme(self, start, end, title):
        encode = lambda value: base64.b64encode(value.encode()).decode()
        return {'start_timestamp':str(start),'stop_timestamp':str(end),'title':encode(title),
                'description':encode('<p>Een echte &amp; duidelijke omschrijving.</p>'),'has_archive':0}

    def request(self, configuration, action=None, **params):
        self.calls.append(action)
        if action is None:
            return {'user_info':{'status':'Active'},'server_info':{'timezone':'Europe/Amsterdam'}}
        if action == 'get_live_streams':
            return self.streams
        return {'epg_listings':self.entries}

    def test_archive_alternative_preserves_primary_and_favorite_name(self):
        self.store.rename(self.channel, 'Mijn tweede zender')
        result = self.catchup.guide(self.channel)
        self.assertEqual(result['days'],3)
        self.assertTrue(result['programmes'][0]['archive'])
        self.assertFalse(result['programmes'][1]['archive'])
        self.assertEqual(result['programmes'][0]['stream_id'],'2')
        self.assertEqual(result['programmes'][0]['description'],'Een echte & duidelijke omschrijving.')
        self.assertEqual(self.store.favorites(playback=True)[0]['sources'][0]['name'],'NL| NPO 2 FHD')
        self.assertEqual(self.store.favorites()[0]['label'],'Mijn tweede zender')

    def test_cache_prevents_repeated_network_reads(self):
        self.catchup.guide(self.channel)
        self.catchup.guide(self.channel)
        self.assertEqual(self.calls,[None,'get_live_streams','get_simple_data_table'])

    def test_playback_url_uses_archive_variant_and_encoded_credentials(self):
        item=self.catchup.guide(self.channel)['programmes'][0]
        url=urlsplit(self.catchup.playback_url(self.channel,item['start']))
        self.assertTrue(url.path.startswith('/panel/timeshift/test%26user/fake%2Bpass/30/'))
        self.assertTrue(url.path.endswith('/2.ts'))
        self.assertEqual(url.query,'')
        with self.assertRaisesRegex(ValueError,'niet beschikbaar'):
            self.catchup.playback_url(self.channel,self.entries[1]['start_timestamp'])

    def test_expired_and_future_programmes_cannot_be_replayed(self):
        self.catchup.guide(self.channel)
        for offset in (-4*86400,3600):
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO catchup_programmes VALUES (?,?,?,?,?,?,?)',
                           (self.catchup.configuration()[2],self.channel,'2',self.now+offset,self.now+offset+900,'Titel',''))
            with self.assertRaises(ValueError):
                self.catchup.playback_url(self.channel,self.now+offset)

    def test_source_change_does_not_reuse_old_metadata_or_credentials(self):
        self.catchup.guide(self.channel)
        self.store.set_setting('source',SOURCE.replace('fake%2Bpass','different'))
        with self.assertRaises(ValueError):
            self.catchup.playback_url(self.channel,self.now-3600)
        self.assertIsNone(self.catchup.recording_programme(self.channel,self.now+3600))

    def test_provider_failure_keeps_local_guide_available(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO epg_programmes VALUES (?,?,?,?)',('two.nl',self.now,self.now+900,'Lokaal'))
        self.catchup.request.side_effect=ValueError('Provider tijdelijk niet bereikbaar.')
        result=self.catchup.guide(self.channel)
        self.assertEqual(result['programmes'][0]['title'],'Lokaal')
        self.assertFalse(result['programmes'][0]['archive'])
        self.assertIn('niet bereikbaar',result['notice'])

    def test_name_mapping_does_not_confuse_countries_or_extra_channels(self):
        self.assertEqual(identity('NL| NPO 2 FHD'),identity('NL| NPO 2 HD'))
        self.assertNotEqual(identity('NL| NPO 2 EXTRA HD'),identity('NL| NPO 2 HD'))
        self.streams[1]['epg_channel_id']=''
        self.streams[1]['name']='US| NPO 2 HD'
        result=self.catchup.guide(self.channel)
        self.assertEqual(result['days'],0)

    def test_verified_provider_programme_can_be_recorded_without_local_xmltv_row(self):
        self.catchup.guide(self.channel)
        recordings=Recordings(self.store)
        with patch('mijntv.recordings.shutil.which',return_value='/usr/bin/ffmpeg'):
            identifier=recordings.schedule(self.channel,self.now+3600)
        row=next(row for row in recordings.list() if row['id']==identifier)
        self.assertEqual(row['title'],'Morgen')
        self.assertEqual(row['end'],self.now+5400+300)
        with patch('mijntv.recordings.shutil.which',return_value='/usr/bin/ffmpeg'):
            with self.assertRaisesRegex(ValueError,'al een opname'):
                recordings.schedule(self.channel,self.now+3600)

    def test_plain_text_and_html_decoding(self):
        self.assertEqual(text('Een gewone titel'),'Een gewone titel')
        self.assertEqual(text(base64.b64encode(b'<b>Nieuws</b>').decode()),'Nieuws')


if __name__ == '__main__': unittest.main()
