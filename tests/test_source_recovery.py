import unittest
from mijntv.recovery import SourceRecovery,retry_interval


class SourceRecoveryTests(unittest.TestCase):
    def test_failure_survives_zapping_and_is_not_extended_by_repeated_watchdog_events(self):
        policy=SourceRecovery();sources=[{'id':'a'},{'id':'b'}]
        self.assertTrue(policy.failed('channel','a',1,100))
        self.assertFalse(policy.failed('channel','a',1,110))
        self.assertEqual([s['id'] for s in policy.available('channel',sources,150)],['b'])
        self.assertEqual([s['id'] for s in policy.available('other',sources,150)],['a','b'])
        self.assertEqual(policy.next_available('channel',[sources[0]],150),220)
        self.assertIn(sources[0],policy.available('channel',sources,220))

    def test_repeated_failures_back_off_but_verified_healthy_source_can_recover(self):
        policy=SourceRecovery()
        policy.failed('channel','a',1,100);policy.failed('channel','a',2,221)
        self.assertEqual(policy.next_available('channel',[{'id':'a'}],221),461)
        policy.healthy('channel','a')
        self.assertEqual(policy.available('channel',[{'id':'a'}],222),[{'id':'a'}])
        for serial in range(1000):policy.failed('channel',str(serial),serial,300)
        self.assertEqual(len(policy.failures),512)

    def test_only_confirmed_closed_inputs_get_fast_retries(self):
        self.assertEqual(retry_interval('eof'),5)
        self.assertEqual(retry_interval('error'),5)
        self.assertEqual(retry_interval('disconnected'),5)
        self.assertEqual(retry_interval('stalled'),60)
        self.assertEqual(retry_interval('black_picture'),60)
        self.assertEqual(retry_interval('stalled',{'core-idle':True,'demuxer-cache-state':{'eof':True}}),5)
