import importlib
import sys
import unittest
from collections import deque
from types import ModuleType
from unittest.mock import Mock, patch

try:
    from mijntv.tv import TV
except ModuleNotFoundError as error:
    if error.name != 'tkinter': raise
    with patch.dict(sys.modules, {'tkinter': ModuleType('tkinter')}):
        TV=importlib.import_module('mijntv.tv').TV


class ProgrammeNoticeTests(unittest.TestCase):
    def setUp(self):
        self.tv=TV.__new__(TV)
        self.tv.watching=True
        self.tv.playing_recording=None
        self.tv.menu_open=False
        self.tv.panel_mode=None
        self.tv.loading=False
        self.tv.selected=0
        self.tv.screen_width=1920
        self.tv.channels=[{'id':'one','label':'NPO 1','epg':{'next':{'start':1600,'title':'Het journaal'}}}]
        self.tv.epg_notice=Mock()
        self.tv.epg_notice_heading=Mock()
        self.tv.epg_notice_title=Mock()
        self.tv.epg_notice_seen=deque(maxlen=256)
        self.tv.epg_notice_key=None
        self.tv.epg_notice_until=0

    def update(self, now=1000, monotonic=100):
        with patch('mijntv.tv.time.time',return_value=now),patch('mijntv.tv.time.monotonic',return_value=monotonic):
            self.tv.update_epg_notice()

    def test_shows_ten_minutes_before_and_expires_without_repeating(self):
        self.update(now=999)
        self.tv.epg_notice.place.assert_not_called()
        self.update()
        self.tv.epg_notice.place.assert_called_once()
        self.assertIn('Over 10 min',self.tv.epg_notice_heading.configure.call_args.kwargs['text'])
        self.assertEqual(self.tv.epg_notice_title.configure.call_args.kwargs['text'],'Het journaal')
        self.tv.epg_notice.place_forget.reset_mock()
        self.update(now=1012,monotonic=112)
        self.tv.epg_notice.place_forget.assert_called_once()
        self.tv.epg_notice_key=None  # Returning to this channel must not repeat it.
        self.update(now=1020)
        self.assertEqual(self.tv.epg_notice.place.call_count,1)
        self.tv.channels[0]['epg']['next']['start']=1620
        self.update(now=1020)
        self.assertEqual(self.tv.epg_notice.place.call_count,2)

    def test_no_notice_without_usable_epg_or_after_start(self):
        for epg in ({},{'next':{'title':'No start'}},{'next':{'start':1600,'title':''}},
                    {'next':{'start':1000,'title':'Already started'}}):
            self.tv.channels[0]['epg']=epg
            self.update()
        self.tv.epg_notice.place.assert_not_called()

    def test_defers_while_loading_menu_recording_or_home(self):
        for name,value in [('loading',True),('menu_open',True),('playing_recording','recording'),
                           ('panel_mode','guide'),('watching',False)]:
            previous=getattr(self.tv,name)
            setattr(self.tv,name,value)
            self.update()
            setattr(self.tv,name,previous)
        self.tv.epg_notice.place.assert_not_called()
        self.assertFalse(self.tv.epg_notice_seen)
        self.update(now=1480)
        self.assertIn('Over 2 min',self.tv.epg_notice_heading.configure.call_args.kwargs['text'])
