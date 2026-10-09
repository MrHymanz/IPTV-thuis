import tempfile
import unittest
from pathlib import Path
from mijntv.video_health import black_frame
try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None


@unittest.skipIf(Image is None, 'Pillow required for video frame checks')
class VideoHealthTests(unittest.TestCase):
    def test_black_near_black_and_visible_picture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'frame.jpg'
            for color, expected in [(0, True), (2, True), (12, False), (90, False), (255, False)]:
                Image.new('RGB',(640,360),(color,color,color)).save(path)
                self.assertEqual(black_frame(path),expected)
            image=Image.new('RGB',(640,360),'black')
            ImageDraw.Draw(image).rectangle((500,20,620,60),fill='white')
            image.save(path)
            self.assertFalse(black_frame(path), 'A logo or credits must not trigger a blackout')

    def test_invalid_image_is_unknown_not_black(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'frame.jpg';path.write_bytes(b'invalid')
            with self.assertRaises(OSError):
                black_frame(path)


class PictureCaptureTests(unittest.TestCase):
    def test_capture_failure_and_channel_change_never_certify_old_picture(self):
        import socket, threading, json
        from unittest.mock import patch
        from mijntv.player import Player
        for mode in ('good','failed','channel-change'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                player=Player.__new__(Player)
                player.status_lock=threading.Lock();player.status={'core-idle':False,'video-black':False}
                player.generation=1;player.monitor_active=True
                player.directory=type('Directory',(),{'name':directory})()
                player.socket_path=str(Path(directory)/'mpv.sock')
                listener=socket.socket(socket.AF_UNIX);listener.bind(player.socket_path);listener.listen()
                def reply():
                    with listener.accept()[0] as connection:
                        request=json.loads(connection.makefile('rb').readline())
                        Path(request['command'][1]).write_bytes(b'frame')
                        connection.sendall((json.dumps({'request_id':'picture','error':
                            'failure' if mode=='failed' else 'success'})+'\n').encode())
                thread=threading.Thread(target=reply);thread.start()
                def classify(path):
                    if mode=='channel-change':
                        player.generation=2;player.status={'core-idle':False}
                    return False
                try:
                    with patch('mijntv.player.black_frame',side_effect=classify):
                        player.check_picture()
                    if mode=='good':
                        self.assertIs(player.status['video-black'],False)
                        self.assertIn('video-checked-at',player.status)
                    else:
                        self.assertNotIn('video-black',player.status)
                    self.assertFalse((Path(directory)/'frame.jpg').exists())
                finally:
                    thread.join(timeout=2);listener.close()
