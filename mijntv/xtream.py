"""Read only live TV through Xtream, independently of large VOD playlists."""
import io
import json
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_API_BYTES = 64 * 1024 * 1024


def is_xtream_source(source):
    p = urlsplit(source)
    q = parse_qs(p.query)
    return (p.scheme in ('http', 'https') and bool(p.netloc)
            and p.path.endswith('/get.php') and bool(q.get('username')) and bool(q.get('password')))


def live_playlist(source):
    parsed = urlsplit(source)
    query = parse_qs(parsed.query)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc
            or not parsed.path.endswith('/get.php')
            or not query.get('username') or not query.get('password')):
        raise ValueError('Deze provider weigert de M3U. Gebruik de Xtream-inloggegevens van de leverancier.')
    user, password = query['username'][0], query['password'][0]
    prefix = parsed.path.rsplit('/', 1)[0]

    def api(action=''):
        params = dict(username=user, password=password)
        if action:
            params['action'] = action
        address = urlunsplit((parsed.scheme, parsed.netloc, prefix + '/player_api.php', urlencode(params), ''))
        try:
            with urlopen(Request(address, headers={'User-Agent': 'IPTV-thuis/0.1',
                                                   'Accept-Encoding': 'identity'}), timeout=20) as response:
                if urlsplit(response.geturl()).scheme not in ('http', 'https'):
                    raise ValueError()
                raw = response.read(MAX_API_BYTES + 1)
                if len(raw) > MAX_API_BYTES:
                    raise ValueError()
                return json.loads(raw)
        except Exception:
            raise ValueError('De Xtream-zenderlijst kon niet worden opgehaald. Controleer server, inloggegevens en netwerk.') from None

    account = api()
    info = account.get('user_info', {}) if isinstance(account, dict) else {}
    if str(info.get('auth')) != '1' or str(info.get('status', '')).casefold() != 'active':
        raise ValueError('De Xtream-login is geweigerd of het account is niet actief. Controleer ook extra aanhalingstekens.')
    channels = api('get_live_streams')
    if not isinstance(channels, list):
        raise ValueError('De provider gaf geen geldige live-zenderlijst terug.')
    try:
        categories = api('get_live_categories')
    except ValueError:
        categories = []
    groups = {str(c['category_id']): str(c.get('category_name', ''))
              for c in categories if isinstance(c, dict) and 'category_id' in c} if isinstance(categories, list) else {}

    def clean(value):
        return str(value or '').replace('\r', ' ').replace('\n', ' ').replace('"', "'")[:300]

    base = urlunsplit((parsed.scheme, parsed.netloc, prefix, '', '')).rstrip('/')
    output = io.StringIO()
    output.write('#EXTM3U\n')
    count = 0
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        identifier = channel.get('stream_id')
        if isinstance(identifier, bool) or not str(identifier).isascii() or not str(identifier).isdigit():
            continue
        name = clean(channel.get('name'))
        if not name:
            continue
        group = clean(groups.get(str(channel.get('category_id')), ''))
        epg = clean(channel.get('epg_channel_id'))
        logo = clean(channel.get('stream_icon'))
        output.write(f'#EXTINF:-1 tvg-id="{epg}" tvg-logo="{logo}" group-title="{group}",{name}\n')
        output.write(base + '/live/' + quote(user, safe='') + '/' + quote(password, safe='') +
                     '/' + str(identifier) + '.ts\n')
        count += 1
    if not count:
        output.close()
        raise ValueError('Geen geldige live-zenders gevonden in het Xtream-account.')
    output.seek(0)
    return output
