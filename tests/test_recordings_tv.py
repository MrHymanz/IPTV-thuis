import os
from pathlib import Path
import queue
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from mijntv.store import Store
from mijntv.recordings import Recordings
try:
    from mijntv.tv import TV
except ImportError:
    TV = None


@unittest.skipUnless(TV and os.environ.get('DISPLAY'), 'Tk en X11 nodig')
class RecordingTVTests(unittest.TestCase):
    def test_menu_layout_preserves_lower_row_and_page(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            for index in range(30):
                store.manual(f'Zender {index+1}', f'https://example.test/{index}')
            tv = TV(store, 8080, fullscreen=False)
            try:
                tv.root.update()
                tv.selected, tv.page = 17, 1
                tv.render()
                tv.key(SimpleNamespace(keysym='Menu'))
                tv.root.update()
                self.assertEqual(sum(button.winfo_ismapped() for button in tv.buttons), 6)
                self.assertGreater(tv.home.winfo_x(), 0)
                self.assertIn('18 · Zender 18', tv.buttons[5].cget('text'))
                self.assertEqual((tv.selected, tv.page), (17, 1))
                tv.key(SimpleNamespace(keysym='Escape'))
                tv.root.update()
                self.assertEqual(sum(button.winfo_ismapped() for button in tv.buttons), 10)
                self.assertEqual(tv.home.winfo_x(), 0)
                self.assertEqual((tv.selected, tv.page), (17, 1))
                self.assertEqual(tv.page_label.cget('text'), 'Pagina 2 van 3')
            finally:
                tv.logo_requests.put(None)
                tv.root.destroy()

    def test_remote_navigation_epg_record_and_playback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'IPTV_RECORDING_DIR':directory+'/recordings'}):
            store = Store(directory)
            store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="test",Test\nhttps://example.test/stream')
            channel = store.catalog()['items'][0]['id']; store.add_favorite(channel)
            now = int(time.time())
            with store.connect() as db:
                db.execute('INSERT INTO epg_programmes VALUES (?,?,?,?)', ('test',now+100,now+200,'Programma'))
            recordings = Recordings(store)
            tv = TV(store,8080,fullscreen=False,recordings=recordings)
            def key(name): tv.key(SimpleNamespace(keysym=name)); tv.root.update()
            try:
                tv.root.update()
                self.assertIn('GB vrij',tv.storage_label.cget('text'))
                key('Menu'); self.assertTrue(tv.menu_open)
                key('Down'); key('Down'); key('Return'); self.assertEqual(tv.panel_mode,'guide')
                self.assertEqual(tv.panel_items[0]['title'],'Programma')
                key('Return'); self.assertEqual(recordings.list()[0]['state'],'queued')
                identifier = recordings.list()[0]['id']
                recordings.cancel(identifier)
                recordings.update(identifier,'completed')
                recordings.directory().joinpath(identifier+'.ts').write_bytes(b'fixture')
                key('Escape'); key('Menu'); key('Down'); key('Return')
                self.assertEqual(tv.panel_mode,'recordings')
                player = Mock(); player.events = queue.Queue(); player.process.poll.return_value = None
                with patch('mijntv.tv.Player',return_value=player):
                    key('Return')
                self.assertEqual(tv.playing_recording,identifier)
                player.play.assert_called_with(str(recordings.playback(identifier)))
                key('Return'); player.command.assert_called_with('cycle','pause')
                key('Right'); player.command.assert_called_with('seek',30,'relative')
                key('Escape'); self.assertEqual(tv.panel_mode,'recordings')
                self.assertFalse(tv.watching)
                key('Escape'); self.assertIsNone(tv.panel_mode)
            finally:
                tv.logo_requests.put(None)
                if tv.player: tv.player.close()
                tv.root.destroy()

if __name__ == '__main__': unittest.main()
