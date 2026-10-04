"""Optional real display/mpv integration test; runs with X11 (including Xvfb)."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

try:
    from mijntv.tv import TV
except ImportError:
    TV = None
from mijntv.store import Store


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(TV and os.environ.get('DISPLAY') and shutil.which('mpv') and shutil.which('ffmpeg'),
                     'X11, tkinter, mpv en ffmpeg nodig voor de optionele videoproef')
class TVIntegration(unittest.TestCase):
    def test_real_video_zap_return_and_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory, 'test.mp4')
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=steelblue:s=640x360:r=10',
                            '-t','30','-c:v','mpeg4',str(clip)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            files = ThreadingHTTPServer(('127.0.0.1',0), partial(QuietFiles,directory=directory))
            threading.Thread(target=files.serve_forever, daemon=True).start()
            store = Store(str(Path(directory,'data')))
            names = ['NPO 1','NPO 2','NPO 3','RTL 4','RTL 5','SBS 6','RTL 7','RTL 8','Net5','Veronica']
            for i in range(30):
                store.manual(names[i] if i<10 else f'Zender {i+1}',f'http://127.0.0.1:{files.server_port}/test.mp4?channel={i}')
            tv = TV(store,8080,fullscreen=True)
            try:
                def pump(seconds=.3):
                    until = time.monotonic()+seconds
                    while time.monotonic()<until:
                        tv.root.update()
                        time.sleep(.01)
                def loaded():
                    deadline = time.monotonic()+12
                    while time.monotonic()<deadline:
                        pump(.1)
                        if tv.player and not getattr(tv,'loading',True) and not tv.pending_zap:
                            break
                    self.assertFalse(tv.loading, 'Video moet laden')
                    self.assertFalse(tv.error.winfo_ismapped(), 'Geen foutmelding bij testvideo')
                pump()
                self.assertEqual(tv.page_label.cget('text'),'Pagina 1 van 3')
                if os.environ.get('IPTV_SCREENSHOT'):
                    subprocess.run(['ffmpeg','-v','error','-f','x11grab','-video_size','1920x1080',
                                    '-i',os.environ['DISPLAY'],'-frames:v','1','-y',os.environ['IPTV_SCREENSHOT']],check=True)
                tv.key(SimpleNamespace(keysym='Next'))
                self.assertEqual(tv.selected,10)
                tv.key(SimpleNamespace(keysym='Return'))
                loaded()
                self.assertTrue(tv.watching)
                pump(.8)
                # The center pixel must be the generated blue video, not a black
                # placeholder: this verifies video is rendered inside our window.
                pixel = subprocess.run(['ffmpeg','-v','error','-f','x11grab','-video_size','1920x1080',
                                        '-i',os.environ['DISPLAY'],'-frames:v','1','-vf','crop=1:1:960:540',
                                        '-pix_fmt','rgb24','-f','rawvideo','pipe:1'],check=True,capture_output=True).stdout
                self.assertEqual(len(pixel),3)
                self.assertGreater(pixel[2],pixel[0]+40)
                process_id = tv.player.process.pid
                tv.key(SimpleNamespace(keysym='Right'))
                self.assertEqual(tv.selected,11)
                loaded()
                self.assertEqual(tv.player.process.pid,process_id, 'Zappen hergebruikt mpv')
                self.assertIs(tv.root.focus_get(), tv.root, 'Afstandsbediening blijft bij de app')
                tv.choose(29)
                loaded()
                tv.key(SimpleNamespace(keysym='Next'))
                self.assertEqual(tv.selected,0, 'Laatste favoriet zapt naar eerste')
                loaded()
                tv.key(SimpleNamespace(keysym='Escape'))
                pump()
                self.assertFalse(tv.watching)
                self.assertTrue(tv.home.winfo_ismapped())
                self.assertEqual(tv.selected,0)
                tv.choose(1)
                tv.go_home()
                pump()
                self.assertIsNone(tv.pending_zap, 'Terug annuleert een geplande stream')
            finally:
                if tv.player:
                    tv.player.close()
                tv.root.destroy()
                files.shutdown()
                files.server_close()


if __name__ == '__main__':
    unittest.main()
