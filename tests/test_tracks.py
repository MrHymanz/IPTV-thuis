import unittest
from unittest.mock import Mock

from mijntv import tracks


class TrackTests(unittest.TestCase):
    def test_parser_exposes_only_input_languages_and_stops_before_output_tracks(self):
        session={'kind':'movie'}
        lines=['Input #0, concat, from private-credential-bearing-address:',
               '  Stream #0:0: Video: h264',
               '  Stream #0:1(eng): Audio: aac (default)',
               '  Stream #0:2(jpn): Audio: ac3 (original)',
               '  Stream #0:3(nld): Subtitle: subrip',
               '  Stream #0:4(nld): Subtitle: hdmv_pgs_subtitle',
               'Stream mapping:', '  Stream #0:5(ger): Audio: aac']
        for line in lines:tracks.observe(session,line.lower())
        result=tracks.options(session)
        self.assertTrue(result['ready'])
        self.assertEqual(len(result['audio']),2)
        self.assertTrue(result['audio'][1]['original'])
        self.assertTrue(result['subtitles'][0]['supported'])
        self.assertFalse(result['subtitles'][1]['supported'])
        self.assertNotIn('credential',str(result))

    def test_original_audio_precedes_default_and_dutch_text_precedes_bitmap(self):
        session={'kind':'episode','tracks_ready':True,'tracks':{
            'audio':[{'id':1,'default':True},{'id':2,'original':True,'default':False}],
            'subtitles':[{'id':3,'language':'nld','supported':False},{'id':4,'language':'nld','supported':True}]},
            'command':['ffmpeg','-re','-map','0:v:0?','-map','0:a:0?','-sn','-dn','-vf','scale=320:180','-hls_segment_filename','segment%06d.ts','index.m3u8']}
        mobile=Mock()
        tracks.configure(mobile,session,automatic=True,position=120)
        self.assertEqual(session['audio_id'],2);self.assertEqual(session['subtitle_id'],4)
        mobile.seekable.restart.assert_called_once_with(session,120)
        with self.assertRaises(ValueError):tracks.configure(mobile,session,audio=1,subtitle=3)
        with self.assertRaises(ValueError):tracks.configure(mobile,session,audio=True,subtitle=None)
        session['track_choice']={'audio_track':1,'subtitle_track':None}
        tracks.configure(mobile,session,automatic=True,position=121)
        self.assertEqual(session['audio_id'],1);self.assertIsNone(session['subtitle_id'])
