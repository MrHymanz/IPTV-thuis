"""Optional real browser regression: pip install playwright; playwright install chromium."""
from pathlib import Path
import tempfile
import threading
import struct
import zlib
import unittest

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

from mijntv.server import AdminServer, credentials
from mijntv.store import Store


@unittest.skipUnless(sync_playwright, 'Playwright en Chromium nodig voor browserproeven')
class BrowserDragTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.ids = [self.store.manual(f'Zender {i+1}',f'https://example.test/{i}') for i in range(8)]
        auth,_ = credentials(self.directory.name,'browser-test-password')
        self.server = AdminServer(('127.0.0.1',0),self.store,auth,Path(__file__).resolve().parents[1]/'mijntv/web')
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True,args=['--no-sandbox'])
        self.context = self.browser.new_context(viewport={'width':1440,'height':2000},has_touch=True,
                                               http_credentials={'username':'admin','password':'browser-test-password'})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror',lambda error: self.errors.append(str(error)))
        self.saves = []
        self.page.on('request',lambda request: self.saves.append(request) if request.url.endswith('/api/favorites/order') else None)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/admin')
        self.page.locator('.drag-handle').nth(7).wait_for()

    def tearDown(self):
        self.browser.close()
        self.playwright.stop()
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def boxes(self):
        first = self.page.locator('.drag-handle').first.bounding_box()
        last = self.page.locator('.favorite-row').last.bounding_box()
        return first['x']+first['width']/2, first['y']+first['height']/2, last['y']+last['height']*.85

    def check_save(self):
        expect(self.page.locator('#message')).to_have_text('Volgorde opgeslagen.',timeout=5000)
        self.page.wait_for_timeout(300)
        self.assertEqual([row['id'] for row in self.store.favorites()],self.ids[1:]+self.ids[:1])
        self.assertEqual(len(self.saves),1)
        self.assertEqual(self.errors,[])

    def test_mouse_hold_drag_across_multiple_rows(self):
        x,start,end = self.boxes()
        self.page.mouse.move(x,start)
        self.page.mouse.down()
        self.page.mouse.move(x,end,steps=30)
        self.assertEqual(len(self.saves),0, 'Niet opslaan voordat de muisknop wordt losgelaten')
        self.page.mouse.up()
        self.check_save()

    def test_touch_hold_drag_across_multiple_rows(self):
        x,start,end = self.boxes()
        session=self.context.new_cdp_session(self.page)
        session.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':start}]})
        for i in range(1,16):
            session.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':start+(end-start)*i/15}]})
        self.assertEqual(len(self.saves),0)
        session.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
        self.check_save()

    def test_escape_during_mouse_drag_cancels_without_save(self):
        x,start,end = self.boxes()
        self.page.mouse.move(x,start)
        self.page.mouse.down()
        self.page.mouse.move(x,end,steps=20)
        self.page.keyboard.press('Escape')
        self.page.mouse.up()
        self.assertEqual(len(self.saves),0)
        self.assertEqual([row['id'] for row in self.store.favorites()],self.ids)
        self.assertEqual(self.page.locator('.favorite-row').evaluate_all('(rows) => rows.map(r => r.dataset.id)'),self.ids)

    def test_real_images_load_in_admin_and_tv_preview(self):
        def chunk(kind,data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
        png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,6,0,0,0)) +
               chunk(b'IDAT',zlib.compress((b'\x00'+b'\xff\x00\x00\xff'*16)*16)) + chunk(b'IEND',b''))
        with self.store.connect() as db:
            db.execute('UPDATE channels SET logo=? WHERE id=?',('https://example.test/logo.png',self.ids[0]))
        self.server.logos.get=lambda url: (png,'image/png')
        self.page.reload()
        self.page.locator('.favorite-logo').first.scroll_into_view_if_needed()
        expect(self.page.locator('.favorite-logo.has-image img')).to_have_count(1)
        self.assertEqual(self.page.locator('.favorite-logo img').first.evaluate('(image) => image.naturalWidth'),16)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/tv')
        expect(self.page.locator('.channel .logo.has-image img')).to_have_count(1)
        self.page.locator('.channel').first.click()
        expect(self.page.locator('#player-logo.has-image img')).to_be_visible()
        self.assertEqual(self.errors,[])
