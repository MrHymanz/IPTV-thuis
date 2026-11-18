import hashlib
import http.client
from http.cookies import SimpleCookie
import importlib.util
import pathlib
import re
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILE = ROOT/'portal/backend/browser_auth.py'
if not FILE.exists(): FILE = ROOT/'mijntv/browser_auth.py'
spec = importlib.util.spec_from_file_location('tested_browser_auth', FILE)
auth = importlib.util.module_from_spec(spec); spec.loader.exec_module(auth)


class BrowserAuthTests(unittest.TestCase):
    def setUp(self):
        self.sessions = auth.BrowserSessions('test_session')
        sessions = self.sessions
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if sessions.route(self, lambda u,p: u=='user' and p=='correct-password', 'Test'): return
                if sessions.require(self): auth.respond(self, 200, 'private')
            do_POST = do_GET
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)
        self.origin = 'https://127.0.0.1:'+str(self.server.server_port)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, method='GET', fields=None, cookie='', origin=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        h = {'X-Forwarded-Proto':'https', **(headers or {})}
        if cookie: h['Cookie'] = cookie
        if origin: h['Origin'] = origin
        body = urlencode(fields) if fields is not None else None
        if fields is not None: h['Content-Type'] = 'application/x-www-form-urlencoded'
        c.request(method, path, body, h); r=c.getresponse(); result=(r.status, r.getheaders(), r.read().decode()); c.close(); return result

    def form(self):
        status, headers, body = self.request('/auth/login?next=%2Fmobile')
        self.assertEqual(status, 200)
        cookie = next(v.split(';')[0] for k,v in headers if k.lower()=='set-cookie')
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
        return cookie, csrf

    def login(self, password='correct-password', next_path='/mobile'):
        cookie, csrf = self.form()
        return self.request('/auth/login','POST', {'username':'user','password':password,'csrf':csrf,'next':next_path}, cookie, self.origin)

    def test_form_is_standard_and_never_triggers_basic_popup(self):
        _, headers, body = self.request('/auth/login')
        for text in ['method="post"', 'action="/auth/login"', 'autocomplete="username"', 'autocomplete="current-password"', 'name="username"', 'name="password"', 'type="submit"']:
            self.assertIn(text, body)
        self.assertNotIn('WWW-Authenticate', dict(headers))
        self.assertEqual(self.request('/api/private')[0], 401)
        status, headers, _ = self.request('/mobile', headers={'Accept':'text/html'})
        self.assertEqual(status,303); self.assertIn('/auth/login?',dict(headers)['Location'])

    def test_session_cookie_is_opaque_secure_host_only_and_logout_revokes(self):
        status, headers, body = self.login()
        self.assertEqual(status,303); self.assertEqual(dict(headers)['Location'],'/mobile')
        session = next(v for k,v in headers if k.lower()=='set-cookie' and v.startswith('__Host-test_session='))
        for flag in ['Secure','HttpOnly','SameSite=Lax','Path=/']: self.assertIn(flag, session)
        self.assertNotIn('Domain=', session); self.assertNotIn('correct-password', session)
        cookie=session.split(';')[0]; token=cookie.split('=',1)[1]
        self.assertNotIn(token, repr(self.sessions.sessions))
        self.assertEqual(self.request('/api/private',cookie=cookie)[0],200)
        self.assertEqual(self.request('/auth/logout','POST',cookie=cookie,origin=self.origin)[0],303)
        self.assertEqual(self.request('/api/private',cookie=cookie)[0],401)

    def test_invalid_login_has_no_session_or_password_echo(self):
        status, headers, body=self.login('wrong-secret')
        self.assertEqual(status,401); self.assertNotIn('wrong-secret',body)
        self.assertFalse(self.sessions.sessions)
        self.assertNotIn('WWW-Authenticate',dict(headers))

    def test_csrf_cookie_origin_and_replay_are_checked(self):
        cookie,csrf=self.form()
        fields={'username':'user','password':'correct-password','csrf':csrf}
        self.assertEqual(self.request('/auth/login','POST',fields,cookie,'https://attacker.invalid')[0],403)
        self.assertEqual(self.request('/auth/login','POST',fields,'',self.origin)[0],403)
        self.assertEqual(self.request('/auth/login','POST',fields,cookie,self.origin)[0],303)
        self.assertEqual(self.request('/auth/login','POST',fields,cookie,self.origin)[0],403)

    def test_no_external_redirect_or_markup_in_next(self):
        for value in ['//evil.invalid','https://evil.invalid','/\\evil.invalid','/\r\nInjected: yes','/auth/logout']:
            self.assertEqual(auth.safe_next(value),'/')
        self.assertEqual(auth.safe_next('/mobile?q=1#films'),'/mobile?q=1#films')
        status,headers,_=self.login(next_path='//evil.invalid')
        self.assertEqual(dict(headers)['Location'],'/')

    def test_expired_sessions_and_rate_limit(self):
        _,headers,_=self.login()
        cookie=next(v.split(';')[0] for k,v in headers if k.lower()=='set-cookie' and v.startswith('__Host-test_session='))
        key=auth.BrowserSessions.digest(cookie.split('=',1)[1]); self.sessions.sessions[key]=time.monotonic()-1
        self.assertEqual(self.request('/api/private',cookie=cookie)[0],401)
        for _ in range(8): self.login(password='wrong')
        self.assertEqual(self.login(password='wrong')[0],429)


if __name__=='__main__': unittest.main()
