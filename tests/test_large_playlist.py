from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from mijntv.store import Store, copy_playlist, PlaylistTooLarge


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class LargePlaylistTests(unittest.TestCase):
    def test_download_and_file_import_over_64mb_preserve_favorites(self):
        with tempfile.TemporaryDirectory() as directory:
            playlist = Path(directory, 'large.txt')
            # Fifty thousand channels with realistic long metadata URLs.
            with playlist.open('w') as file:
                file.write('#EXTM3U\n')
                for i in range(50000):
                    file.write(f'#EXTINF:-1 tvg-id="{i}" tvg-logo="https://example.test/' + 'x'*1400 +
                               f'" group-title="NL",Zender {i:05d}\nhttps://example.test/new/{i}\n')
            self.assertGreater(playlist.stat().st_size, 64*1024*1024)
            store = Store(str(Path(directory, 'data')))
            store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="0" group-title="NL",Zender 00000\nhttps://example.test/old/0')
            channel_id = store.catalog()['items'][0]['id']
            store.add_favorite(channel_id)
            store.rename(channel_id, 'Mijn favoriet')
            server = ThreadingHTTPServer(('127.0.0.1',0), partial(QuietFiles,directory=directory))
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            started = time.monotonic()
            try:
                self.assertEqual(store.import_url(f'http://127.0.0.1:{server.server_port}/large.txt'),50000)
                favorites = store.favorites(playback=True)
                self.assertEqual(favorites[0]['label'],'Mijn favoriet')
                self.assertEqual(favorites[0]['url'],'https://example.test/new/0')
                with playlist.open('rb') as file:
                    self.assertEqual(store.import_file(file,playlist.stat().st_size),50000)
                self.assertEqual(store.catalog()['total'],50000)
                self.assertEqual(len(list(Path(directory,'data').iterdir())),1, 'Tijdelijke downloads zijn opgeruimd')
                print(f'\n{playlist.stat().st_size/1024/1024:.1f} MB: download én bestandsimport geslaagd in {time.monotonic()-started:.2f}s')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_failed_streaming_parse_rolls_back_partial_inserts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            original = '#EXTM3U\n#EXTINF:-1,Bestaand\nhttps://example.test/old'
            store.import_playlist(original,'https://example.test/original')
            def broken():
                yield '#EXTINF:-1,Nieuw\n'
                yield 'https://example.test/new\n'
                raise OSError('Leesfout halverwege')
            with self.assertRaises(OSError):
                store.import_playlist(broken(),'https://example.test/new')
            self.assertEqual(store.catalog()['items'][0]['name'],'Bestaand')
            self.assertEqual(store.setting('source'),'https://example.test/original')

    def test_copy_bounds_and_truncated_upload(self):
        class BoundedInput(io.BytesIO):
            def read(self, size=-1):
                self.assert_size = size
                if not 0 <= size <= 1024*1024:
                    raise AssertionError('Onbegrensde read')
                return super().read(size)
        with patch('mijntv.store.MAX_PLAYLIST',8):
            with self.assertRaises(PlaylistTooLarge):
                copy_playlist(BoundedInput(b'123456789'),io.BytesIO())
            with self.assertRaises(ValueError):
                copy_playlist(BoundedInput(b'123'),io.BytesIO(),length=8)
            output=io.BytesIO()
            self.assertEqual(copy_playlist(BoundedInput(b'12345678'),output,length=8),8)
            self.assertEqual(output.read(),b'12345678')
