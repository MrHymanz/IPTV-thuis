"""Browser login forms and opaque, host-only sessions; no saved passwords in cookies."""
import hashlib
import hmac
import html
from http.cookies import SimpleCookie, CookieError
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

CSS = '''*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;min-height:100svh;display:grid;place-items:center;padding:24px;background:radial-gradient(ellipse at top,#253b32,#101715 65%);color:#eef5ef;font:16px/1.5 system-ui,sans-serif}.card{width:min(100%,420px);padding:32px;border:1px solid #71877a55;border-radius:22px;background:#22312999;box-shadow:0 24px 80px #0005;backdrop-filter:blur(20px)}.mark{font-size:34px;color:#9cdbad}h1{margin:8px 0;font-size:28px}p{color:#bdccc0;margin:0 0 24px}label{display:block;margin:18px 0 6px;font-weight:600}input{width:100%;padding:12px 14px;border:1px solid #83968a88;border-radius:9px;background:#101b16;color:#f4fff6;font:inherit}input:focus{outline:2px solid #9cdbad;outline-offset:2px}button{width:100%;margin-top:26px;border:0;border-radius:9px;padding:13px;background:#a5dfb4;color:#15231a;font:600 16px system-ui;cursor:pointer}.error{padding:12px;border-radius:8px;background:#5a2828;color:#ffdbdb;margin-top:18px}.note{font-size:13px;margin:22px 0 0;color:#aabdb0}@media(max-width:420px){.card{padding:24px}body{padding:16px}}'''
JS = '''const next=document.querySelector('input[name="next"]');if(next&&location.hash&&!next.value.includes('#'))next.value+=location.hash;'''


def safe_next(value):
    if not isinstance(value, str) or len(value) > 2048 or not value.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c) < 32 for c in value):
        return '/'
    parsed = urlsplit(value)
    return '/' if parsed.scheme or parsed.netloc or parsed.path.startswith('/auth/') else value


def secure(handler):
    return handler.headers.get('X-Forwarded-Proto', '').split(',')[0].strip() == 'https'


def same_origin(handler):
    origin = handler.headers.get('Origin')
    if not origin:
        return False
    expected = ('https' if secure(handler) else 'http') + '://' + handler.headers.get('Host', '')
    return hmac.compare_digest(origin.rstrip('/').encode(), expected.rstrip('/').encode())


def respond(handler, code, body=b'', mime='text/html; charset=utf-8', headers=()):
    if isinstance(body, str): body = body.encode()
    handler.send_response(code)
    for k, v in [('Content-Type', mime), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
                 ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
                 ('Referrer-Policy', 'same-origin'), ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"), *headers]:
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


class BrowserSessions:
    def __init__(self, name, ttl=12*3600):
        self.name, self.ttl = name, ttl
        self.lock = threading.RLock()
        self.sessions, self.challenges, self.attempts = {}, {}, {}

    def cookie_name(self, handler, challenge=False):
        return ('__Host-' if secure(handler) else '') + self.name + ('_csrf' if challenge else '')

    def cookie(self, handler, challenge=False):
        try:
            cookies = SimpleCookie(); cookies.load(handler.headers.get('Cookie', ''))
            value = cookies.get(self.cookie_name(handler, challenge))
            return value.value if value else None
        except CookieError:
            return None

    def set_cookie(self, handler, value, challenge=False, max_age=None):
        result = self.cookie_name(handler, challenge) + '=' + value + '; Path=/; HttpOnly; SameSite=Lax'
        if secure(handler): result += '; Secure'
        if max_age is not None: result += '; Max-Age=' + str(max_age)
        return ('Set-Cookie', result)

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def prune(self):
        now = time.monotonic()
        for records in [self.sessions, self.challenges, self.attempts]:
            for key, expiry in list(records.items()):
                expires = expiry[0] if isinstance(expiry, tuple) else expiry
                if expires < now: records.pop(key, None)
            while len(records) >= 1024: records.pop(next(iter(records)))

    def authenticated(self, handler):
        token = self.cookie(handler)
        if not token or len(token) > 256: return False
        with self.lock:
            self.prune()
            return self.sessions.get(self.digest(token), 0) > time.monotonic()

    def login_page(self, handler, title, next_path='/', error='', status=200):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.prune(); self.challenges[self.digest(token)] = time.monotonic() + 600
        esc = html.escape
        body = f'''<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Inloggen · {esc(title)}</title><link rel="stylesheet" href="/auth/login.css"><script src="/auth/login.js" defer></script></head><body><main class="card"><span class="mark" aria-hidden="true">⏻</span><h1>{esc(title)}</h1><p>Log in om verder te gaan.</p>{'<div class="error" role="alert">'+esc(error)+'</div>' if error else ''}<form id="login-form" action="/auth/login" method="post" autocomplete="on"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="next" value="{esc(safe_next(next_path), quote=True)}"><label for="username">Gebruikersnaam</label><input id="username" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required><label for="password">Wachtwoord</label><input id="password" name="password" type="password" autocomplete="current-password" required><button type="submit">Inloggen</button></form><p class="note">Je bestaande inloggegevens blijven geldig.</p></main></body></html>'''
        respond(handler, status, body, headers=[self.set_cookie(handler, token, True, 600)])

    def route(self, handler, verify, title):
        parsed = urlsplit(handler.path)
        if parsed.path not in ['/auth/login', '/auth/logout', '/auth/login.css', '/auth/login.js']: return False
        if handler.command == 'GET':
            if parsed.path == '/auth/login.css': respond(handler, 200, CSS, 'text/css; charset=utf-8')
            elif parsed.path == '/auth/login.js': respond(handler, 200, JS, 'text/javascript; charset=utf-8')
            elif parsed.path == '/auth/login':
                target = safe_next(parse_qs(parsed.query).get('next', ['/'])[0])
                if self.authenticated(handler): respond(handler, 303, headers=[('Location', target)])
                else: self.login_page(handler, title, target)
            else: respond(handler, 405)
            return True
        if handler.command != 'POST' or parsed.path not in ['/auth/login', '/auth/logout']:
            respond(handler, 405); return True
        if not same_origin(handler): respond(handler, 403, 'Ongeldige aanvraag.'); return True
        if parsed.path == '/auth/logout':
            token = self.cookie(handler)
            with self.lock:
                if token: self.sessions.pop(self.digest(token), None)
            # Tombstone also prevents an old cached Basic credential from logging a browser back in.
            respond(handler, 303, headers=[('Location', '/auth/login'), self.set_cookie(handler, 'logged-out')]); return True
        handler.connection.settimeout(10)
        try:
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192 or handler.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded': raise ValueError()
            fields = parse_qs(handler.rfile.read(length).decode(), keep_blank_values=True, max_num_fields=8)
            if any(len(v) != 1 for v in fields.values()): raise ValueError()
            csrf, username, password = [fields.get(k, [''])[0] for k in ['csrf', 'username', 'password']]
            if len(username) > 256 or len(password) > 1024: raise ValueError()
        except (ValueError, UnicodeError): respond(handler, 400, 'Ongeldige aanvraag.'); return True
        target = safe_next(fields.get('next', ['/'])[0])
        cookie = self.cookie(handler, True)
        with self.lock:
            self.prune()
            valid = cookie and len(cookie) <= 256 and hmac.compare_digest(csrf.encode(), cookie.encode()) and self.challenges.pop(self.digest(cookie), 0) > time.monotonic()
            if not valid: respond(handler, 403, 'Inlogsessie verlopen. Open de inlogpagina opnieuw.'); return True
            address = handler.client_address
            client = handler.headers.get('X-Forwarded-For', '').split(',')[0] or (str(address[0]) if isinstance(address, tuple) else 'local')
            key = self.digest(client)
            expiry, count = self.attempts.get(key, (time.monotonic()+300, 0))
            _, total = self.attempts.get('global', (time.monotonic()+60, 0))
            if count >= 8 or total >= 40:
                self.login_page(handler, title, target, 'Te veel pogingen. Probeer het over enkele minuten opnieuw.', 429); return True
            self.attempts[key] = (expiry, count+1)
            global_expiry = self.attempts.get('global', (time.monotonic()+60, 0))[0]
            self.attempts['global'] = (global_expiry, total+1)
        if not verify(username, password):
            self.login_page(handler, title, target, 'Gebruikersnaam of wachtwoord klopt niet.', 401); return True
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.prune(); self.sessions[self.digest(token)] = time.monotonic()+self.ttl
            self.attempts.pop(key, None)
        respond(handler, 303, headers=[('Location', target), self.set_cookie(handler, token, max_age=self.ttl), self.set_cookie(handler, '', True, 0)])
        return True

    def require(self, handler):
        if self.authenticated(handler): return True
        path = urlsplit(handler.path).path
        if handler.command == 'GET' and not path.startswith(('/api/', '/stream/', '/mobile-hls/')) and 'text/html' in handler.headers.get('Accept', ''):
            respond(handler, 303, headers=[('Location', '/auth/login?next='+quote(safe_next(handler.path), safe=''))])
        else:
            respond(handler, 401, json.dumps({'error': 'authentication_required'}), 'application/json')
        return False
