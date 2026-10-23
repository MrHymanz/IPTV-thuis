import math
import queue
import time
import unittest
from unittest.mock import Mock
from mijntv.casting import YouTubeReceiver, number, validate


class CastValidationTests(unittest.TestCase):
    def test_only_video_ids_and_supported_commands_are_accepted(self):
        self.assertEqual(validate({'command':'play','video':'BaW_jenozKc','position':12}), 'play')
        for value in ('https://example.test/video', '../secret', 'x', 'BaW_jenozKc;ls'):
            with self.assertRaises(ValueError):
                validate({'command':'play','video':value})
        with self.assertRaises(ValueError):validate({'command':'loadfile','url':'file:///etc/passwd'})

    def test_positions_and_volumes_are_finite_and_bounded(self):
        for value in (math.nan, math.inf, '12', None, True):
            with self.assertRaises(ValueError):number(value)
        self.assertEqual(number(-10),0)
        self.assertEqual(number(150,100),100)
        with self.assertRaises(ValueError):validate({'command':'volume','volume':{'level':50,'muted':'false'}})

    def test_invalid_and_expired_commands_cannot_reach_tv(self):
        receiver=YouTubeReceiver.__new__(YouTubeReceiver)
        receiver.root=Mock();receiver.handler=Mock(return_value=True);receiver.notify=Mock()
        receiver.commands=queue.Queue();receiver.closed=False;receiver.process=Mock()
        receiver.process.poll.return_value=None;receiver.send=Mock()
        receiver.commands.put((time.monotonic(),{'id':1,'command':'play','video':'file:///secret'}))
        receiver.commands.put((time.monotonic()-20,{'id':2,'command':'stop'}))
        receiver.poll()
        receiver.handler.assert_not_called()
        self.assertEqual(receiver.send.call_count,2)
        receiver.send.assert_any_call({'id':1,'result':False})
        receiver.send.assert_any_call({'id':2,'result':False})

    def test_pairing_codes_are_not_interpreted_as_text_commands(self):
        receiver=YouTubeReceiver.__new__(YouTubeReceiver)
        receiver.root=Mock();receiver.handler=Mock();receiver.notify=Mock()
        receiver.commands=queue.Queue();receiver.closed=False;receiver.process=Mock()
        receiver.process.poll.return_value=None;receiver.code=None
        for code in ('https://evil.test','123 456 789'):
            receiver.commands.put((time.monotonic(),{'event':'pairing','code':code}))
        receiver.poll()
        self.assertEqual(receiver.code,'123 456 789')
        receiver.notify.assert_called_once_with('pairing','123 456 789')
