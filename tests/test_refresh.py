import unittest
from unittest.mock import Mock, patch
from mijntv.__main__ import refresh_loop, PLAYLIST_REFRESH_SECONDS

class RefreshTests(unittest.TestCase):
    def run_loop(self, ages, *, failure=False, sources=None, monotonic=None):
        store=Mock(); store.cleanup_imports.return_value=0
        source_values=iter(sources or ['https://example.test/list']*len(ages))
        age_values=iter(ages)
        def setting(key, default=''):
            return next(source_values) if key=='source' else str(100000-next(age_values))
        store.setting.side_effect=setting
        if failure: store.refresh_source.side_effect=ValueError('private-provider-details')
        stop=Mock(); stop.is_set.return_value=False
        stop.wait.side_effect=[False]*(len(ages)-1)+[True]
        with patch('mijntv.__main__.time.time',return_value=100000), patch('mijntv.__main__.time.monotonic',side_effect=monotonic or [1000]*len(ages)), patch('builtins.print') as output:
            refresh_loop(store,stop)
        for call in stop.wait.call_args_list: self.assertEqual(call.args,(60,))
        self.assertNotIn('private-provider-details',str(output.call_args_list))
        return store

    def test_refresh_when_six_hours_elapsed_after_recent_start(self):
        store=self.run_loop([PLAYLIST_REFRESH_SECONDS-30,PLAYLIST_REFRESH_SECONDS+30])
        store.refresh_source.assert_called_once()

    def test_recent_success_or_uploaded_file_does_not_trigger_download(self):
        self.run_loop([30,90]).refresh_source.assert_not_called()
        self.run_loop([99999],sources=['']).refresh_source.assert_not_called()

    def test_failure_backoff_and_retry_after_fifteen_minutes(self):
        store=self.run_loop([99999]*3,failure=True,monotonic=[1000,1000,1060,1900,1900])
        self.assertEqual(store.refresh_source.call_count,2)

    def test_changed_source_does_not_wait_for_old_backoff(self):
        store=self.run_loop([99999]*2,failure=True,sources=['https://example.test/old','https://example.test/new'],monotonic=[1000,1000,1060,1060])
        self.assertEqual(store.refresh_source.call_count,2)

if __name__=='__main__': unittest.main()
