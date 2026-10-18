"""Remote seeking logic without a display or media process."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from mijntv.tv import TV
except ImportError:
    TV = None


@unittest.skipUnless(TV, 'Tkinter nodig')
class RecordingSeekTests(unittest.TestCase):
    def setUp(self):
        self.tv = TV.__new__(TV)
        self.tv.root = Mock()
        self.tv.player = Mock()
        self.tv.seek_key = None
        self.tv.seek_release_timer = None
        self.tv.seek_started = self.tv.seek_last = 0
        self.tv.seek_step = 15
        self.tv.seek_visible_until = 0

    def seek(self, key, now):
        with patch('mijntv.tv.time.monotonic', return_value=now):
            self.tv.seek_recording(key)

    def test_hold_accelerates_and_direction_change_resets(self):
        for now, step in ((10, 15), (12, 30), (14, 30), (30, 30)):
            self.seek('Right', now)
            self.tv.player.command.assert_called_with('seek', step, 'relative')
        self.seek('Left', 30.1)
        self.tv.player.command.assert_called_with('seek', -15, 'relative')

    def test_repeat_is_throttled_and_release_resets(self):
        self.seek('Right', 10)
        self.seek('Right', 10.1)
        self.assertEqual(self.tv.player.command.call_count, 1)
        self.tv.release_seek(SimpleNamespace(keysym='Right'))
        self.tv.root.after.assert_called_with(80, self.tv.reset_seek)
        self.tv.reset_seek()
        self.seek('Right', 20)
        self.tv.player.command.assert_called_with('seek', 15, 'relative')

    def test_x11_repeat_cancels_pending_release(self):
        self.seek('Right', 10)
        self.tv.release_seek(SimpleNamespace(keysym='Right'))
        self.seek('Right', 12)
        self.tv.root.after_cancel.assert_called_once()
        self.tv.player.command.assert_called_with('seek', 30, 'relative')

    def test_progress_only_appears_after_seek_and_hides_after_three_seconds(self):
        self.tv.watching = self.tv.playing_recording = True
        self.tv.menu_open = False
        self.tv.recording_progress = Mock()
        self.tv.recording_position = Mock()
        self.tv.recording_bar = Mock()
        self.tv.recording_bar.winfo_width.return_value = 1000
        self.tv.player.snapshot.return_value = {'time-pos': 150, 'duration': 300}
        with patch('mijntv.tv.time.monotonic', return_value=10):
            self.tv.update_recording_progress()
        self.tv.recording_progress.place.assert_not_called()
        self.seek('Right', 10)
        self.tv.reset_seek()
        with patch('mijntv.tv.time.monotonic', return_value=12):
            self.tv.update_recording_progress()
        self.tv.recording_progress.place.assert_called_once()
        self.tv.recording_bar.create_rectangle.assert_called_with(
            0, 0, 500, 12, fill='#ffda83', outline='')
        self.tv.recording_progress.reset_mock()
        with patch('mijntv.tv.time.monotonic', return_value=13):
            self.tv.update_recording_progress()
        self.tv.recording_progress.place.assert_not_called()
        self.tv.recording_progress.place_forget.assert_called_once()

    def test_time_format(self):
        self.assertEqual(TV.recording_time(65), '1:05')
        self.assertEqual(TV.recording_time(3661), '1:01:01')
