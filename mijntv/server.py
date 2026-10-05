"""Authenticated LAN administration. No stream URL appears in API responses or logs."""
import base64
import hashlib
import hmac
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import MAX_PLAYLIST, xtream_playlist_url
from .logos import LogoCache
from .epg import EPG
from .display import Display


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200000).hex()


def credentials(directory, password=None):
    path = Path(directory) / 'admin.json'
    generated = None
    if password is not None or not path.exists():
        password = password or secrets.token_urlsafe(18)
        if len(password) < 12:
            raise ValueError('Gebruik een beheerwachtwoord met minimaal 12 tekens.')
        salt = secrets.token_hex(16)
        data = {'salt': salt, 'hash': hash_password(password, salt)}
        with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as file:
            json.dump(data, file)
        os.chmod(path, 0o600)
        generated = password
    with path.open() as file:
        return json.load(file), generated


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, auth, web_directory):
        self.store, self.auth, self.web_directory = store, auth, Path(web_directory)
        self.logos = LogoCache(Path(store.path).parent)
        self.epg = EPG(store)
        self.display = Display(store)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'MijnTV'

    def log_message(self, *args):
        pass  # URLs and playlist credentials must never enter request logs.

    def reply(self, status, data, content_type='application/json; charset=utf-8', extra=None):
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self):
        try:
            kind, value = self.headers.get('Authorization', '').split(' ', 1)
            user, password = base64.b64decode(value, validate=True).decode().split(':', 1)
            candidate = hash_password(password, self.server.auth['salt'])
            if kind.lower() == 'basic' and user == 'admin' and hmac.compare_digest(candidate, self.server.auth['hash']):
                return True
        except (ValueError, UnicodeError):
            pass
        self.reply(401, {'error': 'Log in als admin.'}, extra={'WWW-Authenticate': 'Basic realm="Mijn TV beheer", charset="UTF-8"'})
        return False

    def do_GET(self):
        if not self.authorized():
            return
        parts = urlsplit(self.path)
        params = parse_qs(parts.query)
        store = self.server.store
        try:
            if parts.path.startswith('/api/logos/'):
                url = store.channel_logo(parts.path[len('/api/logos/'):])
                if not url:
                    self.reply(404, {'error': 'Geen zenderlogo.'}); return
                try:
                    data, mime = self.server.logos.get(url)
                    self.reply(200, data, mime)
                except ValueError:
                    self.reply(404, {'error': 'Logo niet beschikbaar.'})
            elif parts.path == '/api/display':
                self.reply(200, self.server.display.status())
            elif parts.path == '/api/status':
                self.reply(200, {'channels': store.catalog(limit=0)['total'], 'favorites': len(store.favorites()),
                                 'has_source': bool(store.setting('source')),
                                 'has_epg_source': bool(self.server.epg.configuration()[0]),
                                 'epg_updated_at': float(store.setting('epg_refreshed_at', '0'))})
            elif parts.path == '/api/channels':
                offset = max(0, int(params.get('offset', ['0'])[0]))
                self.reply(200, store.catalog(params.get('q', [''])[0], params.get('group', [''])[0], offset))
            elif parts.path == '/api/groups':
                self.reply(200, store.groups())
            elif parts.path == '/api/favorites':
                self.reply(200, store.favorites())
            elif parts.path in ('/', '/admin', '/admin/', '/admin.js', '/admin.css', '/tv', '/tv/', '/tv.js', '/tv.css'):
                filename = {'/admin.js': 'admin.js', '/admin.css': 'admin.css', '/tv': 'tv.html', '/tv/': 'tv.html', '/tv.js': 'tv.js', '/tv.css': 'tv.css'}.get(parts.path, 'admin.html')
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[Path(filename).suffix]
                self.reply(200, (self.server.web_directory / filename).read_bytes(), mime + '; charset=utf-8')
            else:
                self.reply(404, {'error': 'Niet gevonden.'})
        except ValueError:
            self.reply(400, {'error': 'Ongeldige zoekopdracht.'})
        except Exception:
            self.reply(500, {'error': 'Er ging iets mis bij het lezen. Probeer opnieuw.'})

    def do_POST(self):
        if not self.authorized():
            return
        # A custom header and JSON force cross-origin browser requests to preflight;
        # no CORS permission is given. Origin check also protects cached Basic auth.
        origin = self.headers.get('Origin')
        path = urlsplit(self.path).path
        expected_type = 'application/octet-stream' if path == '/api/import/file' else 'application/json'
        if (self.headers.get('X-MijnTV') != '1' or
                self.headers.get('Content-Type', '').split(';')[0] != expected_type or
                (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
            self.reply(403, {'error': 'Open de beheerpagina op dit apparaat.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= (MAX_PLAYLIST if path == '/api/import/file' else MAX_PLAYLIST * 2):
                self.reply(413, {'error': 'Upload te groot (maximaal 512 MB M3U).'}); return
            if path == '/api/import/file':
                self.reply(200, {'ok': True, 'imported': self.server.store.import_file(self.rfile, length)})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Ongeldige aanvraag.')
            store = self.server.store
            result = {'ok': True}
            if path == '/api/import/xtream':
                source = xtream_playlist_url(data['server'], data['username'], data['password'])
                result['imported'] = store.import_url(source)
            elif path == '/api/import':
                if data.get('url'):
                    result['imported'] = store.import_url(data['url'])
                else:
                    text = data.get('text', '')
                    if not isinstance(text, str) or len(text.encode()) > MAX_PLAYLIST:
                        raise ValueError('Ongeldige of te grote M3U.')
                    result['imported'] = store.import_playlist(text)
            elif path == '/api/display':
                self.server.display.save(data['resolution'], data['margin'])
                result['native'] = self.server.display.native
            elif path == '/api/epg/refresh':
                result['programmes'] = self.server.epg.refresh()
            elif path == '/api/refresh':
                result['imported'] = store.refresh_source()
            elif path == '/api/favorites/add':
                store.add_favorite(data['id'], data.get('label', ''))
            elif path == '/api/favorites/rename':
                store.rename(data['id'], data['label'])
            elif path == '/api/favorites/remove':
                store.remove(data['id'])
            elif path == '/api/favorites/order':
                store.reorder(data['ids'])
            elif path == '/api/favorites/manual':
                store.manual(data['label'], data['url'])
            else:
                self.reply(404, {'error': 'Niet gevonden.'}); return
            self.reply(200, result)
        except (KeyError, TypeError, json.JSONDecodeError):
            self.reply(400, {'error': 'Ongeldige aanvraag.'})
        except ValueError as error:
            self.reply(400, {'error': str(error)})
        except Exception:
            self.reply(500, {'error': 'De wijziging kon niet worden opgeslagen. Probeer opnieuw.'})
