"""A complete VOD timeline backed by one bounded, seekable transcoder."""
import math
from pathlib import Path
import shutil
import signal
import subprocess
import time


class SeekableVOD:
    def __init__(self, mobile):
        self.mobile = mobile

    @staticmethod
    def enabled(session):
        return session.get('native_vod', False) and session.get('duration_verified', False)

    @staticmethod
    def playlist(session, captions=False):
        duration = session['duration']
        lines = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:1', '#EXT-X-MEDIA-SEQUENCE:0',
                 '#EXT-X-PLAYLIST-TYPE:VOD', '#EXT-X-INDEPENDENT-SEGMENTS']
        initial = min(session['initial_position'], max(0, duration-.05))
        if initial:
            lines.append('#EXT-X-START:TIME-OFFSET='+str(initial)+',PRECISE=YES')
        for index in range(math.ceil(duration)):
            lines.extend(('#EXTINF:'+format(min(1, duration-index), '.6f')+',', ('caption' if captions else 'segment')+format(index, '06d')+('.vtt' if captions else '.ts')))
        lines.append('#EXT-X-ENDLIST')
        return ('\n'.join(lines)+'\n').encode(), 'application/vnd.apple.mpegurl'

    def restart(self, session, index):
        mobile = self.mobile
        # This method is called under the same account lock as TV/recording.
        if mobile.closed.is_set() or session['state'] in ('stopped', 'preempted', 'error'):
            raise ValueError('De mobiele speler is gestopt.')
        if session['account'] in mobile.streams.occupied_accounts():
            mobile._stop(session, 'preempted', 'De tv of opname heeft dit account nodig. Probeer later opnieuw.')
            raise ValueError(session['message'])
        process = session['process']
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        for reader in session.get('readers', []):
            reader.join(timeout=.25)
        previous = session['output']
        session['seek_generation'] += 1
        output = Path(session['directory'].name)/('run-'+str(session['seek_generation']))
        output.mkdir(mode=0o700)
        # concat needs an explicit duration for efficient random access. This
        # value came from its first decoded frame, not rounded provider metadata.
        manifest = Path(session['directory'].name)/'input.ffconcat'
        if manifest.exists() and session.get('duration_verified'):
            content = '\n'.join(line for line in manifest.read_text().splitlines() if not line.startswith('duration '))
            manifest.write_text(content+'\nduration '+str(session['duration'])+'\n')
        command = list(session['command'])
        if '-ss' in command:
            if index:command[command.index('-ss')+1] = str(index)
            else:
                position=command.index('-ss');del command[position:position+2]
        elif index:
            position = command.index('-re')
            command[position:position] = ['-ss', str(index)]
        if '-output_ts_offset' in command:command[command.index('-output_ts_offset')+1] = str(index)
        if '-start_number' in command:command[command.index('-start_number')+1] = str(index)
        elif session.get('subtitle_id') is not None:command[-1:-1]=['-start_number',str(index)]
        command[command.index('-hls_segment_filename')+1] = str(output/'segment%06d.ts')
        command[-1] = str(output/('index_%v.m3u8' if session.get('subtitle_id') is not None else 'index.m3u8'))
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            mobile._stop(session, 'error', 'Het gekozen videodeel kon niet worden geopend.')
            raise ValueError(session['message']) from None
        session.update(process=process, command=command, output=output, first_segment=index,
                       last_served=index-1, failure='unknown', decoded=False, state='loading',
                       transcoder_at=time.monotonic(), ready_logged=False)
        if session['account']:
            mobile.streams.mobile = {'owner':session['token'], 'key':session['account'], 'stop':session['preempt']}
        mobile._observe(session)
        shutil.rmtree(previous, ignore_errors=True)

    @staticmethod
    def caption_ready(session,name):
        # FFmpeg writes VTT directly; the atomic playlist publishes it only
        # after its cues have been flushed. An existing file may still be empty.
        try:return name in (session['output']/'index_0_vtt.m3u8').read_text().splitlines()
        except OSError:return False

    def segment(self, token, index, caption=False):
        mobile = self.mobile
        with mobile.streams.lock:
            session = mobile._session(token)
            if index < 0 or index >= math.ceil(session['duration']):
                raise ValueError('Dit videodeel valt buiten de film of aflevering.')
            if session['state'] in ('stopped', 'preempted', 'error') or mobile.closed.is_set():
                raise ValueError('De mobiele speler is gestopt.')
            name = 'index_0'+str(index)+'.vtt' if caption else 'segment'+format(index, '06d')+'.ts'
            path = session['output']/name
            if not path.exists() and (index < session['first_segment'] or (index <= session['last_served'] and (not caption or not (session['output']/('segment'+format(index,'06d')+'.ts')).exists())) or
                                      index > max(session['first_segment'], session['last_served'])+8):
                self.restart(session, index)
            generation = session['seek_generation']
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            with mobile.streams.lock:
                session = mobile._session(token)
                if session['seek_generation'] != generation:
                    raise ValueError('Er is een ander afspeelmoment gekozen.')
                if session['state'] in ('stopped', 'preempted', 'error') or mobile.closed.is_set():
                    raise ValueError('De mobiele speler is gestopt.')
                session['seen'] = time.monotonic()
                path = session['output']/name
                if path.is_file() and not path.is_symlink() and (not caption or self.caption_ready(session,name)):
                    data = path.read_bytes()
                    session['last_served'] = max(session['last_served'], index)
                    if session['paused'] and session['process'].poll() is None:
                        session['process'].send_signal(signal.SIGSTOP)
                    return (mobile.caption_data(data), 'text/vtt') if caption else (data, 'video/mp2t')
                code = session['process'].poll()
                if code is not None:
                    if code:
                        mobile._stop(session, 'error', mobile.failure_details(session['failure'])[0])
                    raise ValueError('Dit videodeel kon niet worden gelezen.')
                if session['paused']:
                    session['process'].send_signal(signal.SIGCONT)
            # Never hold the account lock while waiting for provider/video data.
            mobile.closed.wait(.1)
        raise ValueError('De aanbieder reageert te langzaam bij het spoelen.')
