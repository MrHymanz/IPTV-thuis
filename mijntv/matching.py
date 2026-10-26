"""Conservative recording-channel lookup across provider naming/quality variants."""
from .backup import channel_key


class RecordingCatalog:
    def __init__(self, rows):
        self.rows = rows
        self.epg = {}
        self.names = {}
        for row in rows:
            identifier = row.get('tvg_id', '').strip().casefold()
            if identifier:
                self.epg.setdefault(identifier, []).append(row)
            self.names.setdefault(channel_key(row['name'], row['group_name']), []).append(row)

    def match(self, channel):
        epg = channel.get('epg_id', '').strip().casefold()
        sources = channel.get('sources') or [channel]
        # Preserve an unambiguous exact variant/group when available.
        for source in sources:
            key = channel_key(source.get('name') or channel['label'], source.get('group_name') or '')
            exact = [r for r in self.names.get(key, [])
                     if r['group_name'] == source.get('group_name')
                     and r['name'] == source.get('name')
                     and (not epg or not r['tvg_id'] or r['tvg_id'].strip().casefold() == epg)]
            if len(exact) == 1:
                return exact[0]
        candidates = self.epg.get(epg, []) if epg else []
        if not candidates:
            for source in [channel] + sources:
                key = channel_key(source.get('name') or channel['label'], source.get('group_name') or '')
                candidates = self.names.get(key, [])
                if candidates:
                    # A name with different guide identities is not a certain match.
                    if len({r['tvg_id'].strip().casefold() for r in candidates if r['tvg_id'].strip()}) > 1:
                        candidates = []
                    else:
                        break
        if not candidates:
            return None
        region, name = channel_key(channel.get('name') or channel['label'], channel.get('group_name') or '')
        def rank(row):
            country, key = channel_key(row['name'], row['group_name'])
            return (bool(region) and country != region, key != name,
                    row['group_name'] != channel.get('group_name'),
                    'FHD' not in row['name'].upper(), 'HD' not in row['name'].upper(),
                    row['name'], row['id'])
        return min(candidates, key=rank)
