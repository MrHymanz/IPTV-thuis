import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
try:
    import tkinter as tk
except ImportError:
    tk=None


@unittest.skipIf(tk is None, 'Tk required')
class ZapInterfaceTests(unittest.TestCase):
    def test_burst_stays_responsive_during_slow_player_startup(self):
        from mijntv.store import Store
        from mijntv.tv import TV
        try:
            root=tk.Tk();root.withdraw()
        except tk.TclError:
            self.skipTest('X display required')
        gate=threading.Event();started=threading.Event()
        player=Mock();player.events=queue.Queue();player.snapshot.return_value={}
        player.process.poll.return_value=None
        def slow_player(window, hwdec='auto-safe'):
            started.set();gate.wait(3);return player
        def pump(seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                root.update();time.sleep(.005)
        try:
            with tempfile.TemporaryDirectory() as directory, patch('mijntv.tv.tk.Tk',return_value=root), \
                    patch.object(TV,'present'), patch('mijntv.tv.Player',side_effect=slow_player):
                tv=TV(Store(directory),8080,fullscreen=False)
                tv.channels=[{'id':str(i),'available':True,'label':str(i),'sources':[
                    {'id':str(i),'url':'https://example.test/'+str(i)}]} for i in range(20)]
                tv.choose(0);pump(.45)
                self.assertTrue(started.is_set())
                before=time.monotonic()
                for _ in range(10):
                    tv.zap(1);root.update()
                self.assertLess(time.monotonic()-before,.5)
                self.assertEqual(tv.selected,10)
                player.play.assert_not_called()
                gate.set();pump(.7)
                player.play.assert_called_once_with('https://example.test/10')
                player.close()
        finally:
            gate.set();root.destroy()
