"""Native timeline behavior and Full HD bounds, exercised with Tk/Xvfb."""
import os
import queue
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import tkinter
    from mijntv.tv import TV
except (ImportError, AttributeError):
    TV = None
from mijntv.store import Store
from mijntv.recordings import Recordings


@unittest.skipUnless(TV and os.environ.get('DISPLAY'), 'Tk en X11 nodig')
class NativeGuideTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one.nl",NPO 1\nhttps://example.test/live/1.ts')
        self.channel = self.store.catalog()['items'][0]['id']
        self.store.add_favorite(self.channel)
        self.recordings = Recordings(self.store)
        self.now = int(time.time())
        self.items = [{'start':self.now+(index-72)*1800-900,'end':self.now+(index-72)*1800+900,
                       'title':f'Programma {index+1}', 'description':'Een echt opgehaalde omschrijving met achtergrondinformatie over deze uitzending. '*4,
                       'archive':index<72,'archive_days':3} for index in range(145)]
        with self.store.connect() as db:
            db.executemany('INSERT INTO epg_programmes VALUES (?,?,?,?)',
                           [('one.nl',item['start'],item['end'],item['title']) for item in self.items])
        self.guide_patch=patch.object(self.recordings.catchup,'guide',return_value={'programmes':self.items,'notice':'','days':3})
        self.guide_patch.start()
        self.addCleanup(self.guide_patch.stop)
        self.tv = TV(self.store,8080,fullscreen=True,recordings=self.recordings)
        self.addCleanup(self.cleanup)
        self.tv.open_panel('guide')
        self.pump(.4)

    def cleanup(self):
        self.tv.logo_requests.put(None)
        if self.tv.player:
            self.tv.player.close()
        for timer in self.tv.root.tk.call('after', 'info'):
            self.tv.root.after_cancel(timer)
        self.tv.root.destroy()

    def pump(self, duration=.1):
        end=time.monotonic()+duration
        while time.monotonic()<end:
            self.tv.root.update()
            time.sleep(.01)

    def key(self,name):
        self.tv.key(SimpleNamespace(keysym=name))
        self.pump(.03)

    def test_timeline_scrolls_and_plans_without_day_tabs(self):
        self.assertEqual(self.tv.panel_selected,72)
        self.key('Down')
        self.assertEqual(self.tv.panel_selected,73)
        self.key('Return')
        plans=self.recordings.list()
        self.assertEqual(len(plans),1)
        self.assertEqual(plans[0]['state'],'queued')
        self.key('Return')
        self.assertEqual(self.recordings.list()[0]['state'],'cancelled')
        for _ in range(8):self.key('Prior')
        self.assertLess(self.tv.panel_selected,30)
        self.assertEqual(self.tv.guide_view.selected,self.tv.panel_selected)

    def test_holding_ok_does_not_plan_and_cancel_repeatedly(self):
        self.key('Down')
        press=SimpleNamespace(keysym='Return',keycode=36)
        self.tv.key(press);self.tv.key(press)
        self.assertEqual(self.recordings.list()[0]['state'],'queued')
        self.tv.release_seek(SimpleNamespace(keysym='Return'));self.pump(.12)
        self.tv.key(press)
        self.assertEqual(self.recordings.list()[0]['state'],'cancelled')

    def test_current_programme_records_and_switches_with_one_ok(self):
        with patch.object(self.tv, 'choose') as choose:
            self.key('Return')
            choose.assert_called_once_with(0)
        self.assertIsNone(self.tv.programme_actions)
        self.assertEqual(self.recordings.list()[0]['state'],'queued')
        self.assertEqual(self.recordings.list()[0]['title'],self.items[72]['title'])
        self.assertNotIn('R:',self.tv.guide_view.hint.cget('text'))

    def test_current_programme_has_progress_and_fullhd_info_fits(self):
        view=self.tv.guide_view
        view.update_info();self.pump()
        self.assertTrue(view.live.winfo_ismapped())
        self.assertIn('50%',view.live_text.cget('text'))
        bottom=lambda widget:widget.winfo_rooty()+widget.winfo_height()
        self.assertLessEqual(bottom(view.record_hint),bottom(view.info))
        self.assertLessEqual(bottom(view.info),bottom(self.tv.content))
        self.assertGreater(view.canvas.winfo_height(),400)
        self.assertEqual(len(view.rows),145)
        self.key('Down')
        self.assertFalse(view.live.winfo_ismapped())
        self.key('Up');self.key('Up')
        self.assertFalse(view.live.winfo_ismapped())

    def test_description_geometry_settles_while_changing_programmes(self):
        view = self.tv.guide_view
        errors = []
        self.tv.root.report_callback_exception = lambda *error: errors.append(error)
        events = []
        view.info.bind('<Configure>', lambda event: events.append((event.width, event.height)), add='+')
        for index in (72, 73, 71, 72):
            view.items[index]['description'] = 'Lange programmaomschrijving. ' * 150
            view.items[index]['title'] = 'Een lange programmatitel ' * 8
            view.selected = self.tv.panel_selected = index
            view.highlight()
            self.pump(.15)
        self.pump(.2)
        count = len(events)
        self.pump(.3)
        self.assertEqual(len(events), count, 'Information panel keeps resizing after layout settled')
        self.assertLess(count, 20)
        self.assertFalse(errors)
        self.assertLessEqual(view.action.winfo_rooty() + view.action.winfo_height(),
                             view.info.winfo_rooty() + view.info.winfo_height())

    def test_cast_uses_native_player_without_cancelling_recordings(self):
        self.recordings.schedule(self.channel,self.items[73]['start'])
        with patch.object(self.tv,'start_stream') as start, patch.object(self.recordings,'cancel') as cancel:
            self.assertTrue(self.tv.cast_command({'command':'play','video':'BaW_jenozKc','position':15}))
            start.assert_called_once_with({'url':'https://www.youtube.com/watch?v=BaW_jenozKc'})
            cancel.assert_not_called()
        self.assertTrue(self.tv.playing_cast)
        self.assertEqual(self.recordings.list()[0]['state'],'queued')
        self.key('Escape')
        self.assertFalse(self.tv.playing_cast)
        self.assertIsNone(self.tv.panel_mode)
        self.assertEqual(self.recordings.list()[0]['state'],'queued')

    def test_cast_pairing_is_accessible_with_remote_in_menu(self):
        self.tv.go_home()
        self.tv.menu.destroy()
        self.tv.casting.process=Mock()
        self.tv.build_menu()
        self.key('Menu')
        for _ in range(3):self.key('Down')
        self.assertEqual(self.tv.menu_selected,3)
        self.assertEqual(len(self.tv.menu_buttons),4)
        with patch.object(self.tv,'show_cast_pairing') as pair:
            self.key('Return')
            pair.assert_called_once_with()
        self.assertFalse(self.tv.menu_open)
        self.tv.casting.process=None

    def test_cast_controls_cannot_stop_or_seek_live_tv(self):
        player=Mock();self.tv.player=player
        self.assertTrue(self.tv.cast_command({'command':'stop'}))
        self.assertFalse(self.tv.cast_command({'command':'seek','position':10}))
        player.command.assert_not_called()
        self.tv.playing_cast=True
        self.assertTrue(self.tv.cast_command({'command':'pause'}))
        player.command.assert_called_with('set_property','pause',True)
        self.assertTrue(self.tv.cast_command({'command':'seek','position':10}))
        player.command.assert_called_with('seek',10,'absolute')
        self.tv.cast_command({'command':'volume','volume':{'level':35,'muted':True}})
        self.assertEqual(self.tv.volume,35)
        self.assertTrue(self.tv.muted)

    def test_archive_returns_to_same_channel_and_row(self):
        self.key('Up')
        selected=self.tv.panel_selected
        player=Mock();player.events=queue.Queue();player.process.poll.return_value=None
        with patch('mijntv.tv.Player',return_value=player), patch.object(self.recordings.catchup,'playback_url',return_value='https://example.test/archive.ts'):
            self.key('Return');self.pump(.2)
        self.assertIsNotNone(self.tv.playing_catchup)
        self.assertEqual(self.tv.playing_recording,'catchup')
        self.key('Right');player.command.assert_called_with('seek',15,'relative')
        self.key('Escape');self.pump(.2)
        self.assertEqual(self.tv.panel_mode,'guide')
        self.assertEqual(self.tv.panel_selected,selected)
        self.assertIsNone(self.tv.playing_catchup)

    def test_primary_recording_blocks_archive_without_stopping_recording(self):
        self.key('Up')
        with patch.object(self.recordings,'required_channel',return_value={'id':self.channel,'label':'NPO 1'}), patch.object(self.recordings.catchup,'playback_url') as playback:
            self.key('Return')
        playback.assert_not_called()
        self.assertEqual(self.tv.panel_mode,'guide')
        self.assertIn('opname',self.tv.error.cget('text'))

    def test_closed_guide_ignores_late_background_response(self):
        generation=self.tv.guide_generation
        self.tv.go_home()
        future=Mock();future.done.return_value=True
        self.tv.poll_guide(future,generation)
        future.result.assert_not_called()


if __name__ == '__main__': unittest.main()
