import subprocess
import tempfile
import unittest
from unittest.mock import patch
from mijntv.display import Display
from mijntv.store import Store

XRANDR = '''Screen 0: current 1920 x 1080
HDMI-1 disconnected primary
DP-1 disconnected
HDMI-3 connected 1920x1080+0+0 (normal) 1600mm x 900mm
   1920x1080     60.00*+
   4096x2160     30.00
   3840x2160     30.00
   1920x1080i    50.00
   1280x720      60.00
'''

class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.display = Display(self.store, native=True)

    @patch('mijntv.display.subprocess.run')
    def test_modes_and_active_output(self, run):
        run.return_value.stdout = XRANDR
        status = self.display.status()
        self.assertEqual(status['current'], '1920x1080')
        self.assertEqual(status['available'], ['auto','1280x720','1920x1080','3840x2160'])
        self.display.save('3840x2160', 3)
        self.display.apply()
        self.assertEqual(run.call_args.args[0], ['xrandr','--output','HDMI-3','--mode','3840x2160'])
        self.assertEqual(Display(Store(self.directory.name)).settings(), {'resolution':'3840x2160','margin':3})

    @patch('mijntv.display.subprocess.run')
    def test_invalid_and_unavailable_do_not_replace_settings(self, run):
        run.return_value.stdout = XRANDR.replace('   3840x2160     30.00\n','')
        for resolution, margin in [('3840x2160',0), ('bad;command',0), ('auto',11), ('auto',True), ('auto','2')]:
            with self.assertRaises(ValueError):
                self.display.save(resolution,margin)
        self.assertEqual(self.display.settings(), {'resolution':'auto','margin':0})

    @patch('mijntv.display.subprocess.run')
    def test_unplugged_and_failed_mode_leave_current_display(self, run):
        self.display.native = False
        self.display.save('3840x2160',0)
        self.display.native = True
        run.return_value.stdout = 'HDMI-3 disconnected\n'
        self.display.apply()
        self.assertTrue(self.display.error)
        run.assert_called_once()
        run.reset_mock()
        run.side_effect = [subprocess.CompletedProcess([],0,XRANDR), subprocess.CalledProcessError(1, [])]
        self.display.apply()
        self.assertTrue(self.display.error)

    def test_admin_only_saves_without_changing_host_display(self):
        self.display.native=False
        with patch('mijntv.display.subprocess.run') as run:
            self.display.save('3840x2160',4)
            self.display.apply()
            self.assertFalse(self.display.status()['native'])
            run.assert_not_called()
