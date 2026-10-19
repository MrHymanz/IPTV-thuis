"""The recorder must wait for mpv's stop acknowledgement, not just send a command."""
import os
import queue
import shutil
import subprocess
import tempfile
import time
import unittest
try:
    import tkinter as tk
except ImportError:
    tk=None
from mijntv.player import Player


@unittest.skipUnless(tk and os.environ.get('DISPLAY') and shutil.which('mpv') and shutil.which('ffmpeg'), 'Tk, X11, mpv en ffmpeg nodig')
class PlayerStopTests(unittest.TestCase):
    def test_stop_ack_keeps_player_alive_and_releases_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            filename=directory+'/video.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=steelblue:s=320x180:r=10',
                            '-t','10','-c:v','mpeg4',filename],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            root=tk.Tk();frame=tk.Frame(root,width=320,height=180);frame.pack();root.update()
            player=None
            try:
                player=Player(frame.winfo_id(),hwdec='no');player.play(filename)
                deadline=time.monotonic()+10;loaded=False
                while time.monotonic()<deadline:
                    root.update()
                    try:event=player.events.get(timeout=.05)
                    except queue.Empty:continue
                    if event.get('event')=='file-loaded':loaded=True;break
                self.assertTrue(loaded,'Testvideo moet laden')
                player.stop_and_wait()
                self.assertIsNone(player.process.poll(),'Stop moet via IPC worden bevestigd, zonder mpv te doden')
                self.assertFalse(player.stop_waiters)
                self.assertFalse(player.monitor_active)
            finally:
                if player:player.close()
                root.destroy()


if __name__=='__main__':unittest.main()
