"""Bounded, per-source cooldowns shared by zapping and automatic TV recovery."""
from collections import OrderedDict


class SourceRecovery:
    def __init__(self):
        self.failures=OrderedDict()
        self.last_failed_attempt=None

    def failed(self, channel, source, attempt, now):
        if not source:return False
        marker=(channel,source,attempt)
        if marker==self.last_failed_attempt:return False
        self.last_failed_attempt=marker
        key=(channel,source);previous=self.failures.get(key)
        count=previous[1]+1 if previous and now<previous[0]+1800 else 1
        self.failures[key]=(now+min(120*2**min(count-1,3),900),count)
        self.failures.move_to_end(key)
        while len(self.failures)>512:self.failures.popitem(last=False)
        return True

    def healthy(self, channel, source):
        self.failures.pop((channel,source),None)

    def available(self, channel, sources, now):
        result=[s for s in sources if self.failures.get((channel,s.get('id')),(0,0))[0]<=now]
        # Equal-health variants retain their configured preference order.
        return sorted(result,key=lambda s:self.failures.get((channel,s.get('id')),(0,0))[1])

    def next_available(self, channel, sources, now):
        return min((self.failures.get((channel,s.get('id')),(now,0))[0] for s in sources),default=now+60)


def retry_interval(reason, snapshot=None):
    snapshot=snapshot or {}
    ended=reason in ('eof','error','disconnected','recording_source_ended')
    cache=snapshot.get('demuxer-cache-state') or {}
    ended=ended or (snapshot.get('core-idle') is True and cache.get('eof') is True)
    return 5 if ended else 60
