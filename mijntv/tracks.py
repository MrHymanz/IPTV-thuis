"""Safe input-track metadata and HLS text subtitles, using the existing decoder."""
import math
import re

TEXT_SUBTITLES = {'subrip','srt','ass','ssa','webvtt','mov_text','text'}
LANGUAGES = {'nl':'Nederlands','nld':'Nederlands','dut':'Nederlands','en':'Engels','eng':'Engels',
             'de':'Duits','deu':'Duits','ger':'Duits','fr':'Frans','fra':'Frans','fre':'Frans',
             'es':'Spaans','spa':'Spaans','it':'Italiaans','ita':'Italiaans','ja':'Japans','jpn':'Japans'}


def observe(session, text):
    if session.get('tracks_ready') or session.get('kind') not in ('movie','episode'):
        return
    match = re.search(r'stream #0:(\d+)(?:\[[^\]]+\])?(?:\((\w+)\))?: (audio|subtitle): ([a-z0-9_]+)', text)
    if match:
        index, language, kind, codec = match.groups()
        group = 'audio' if kind == 'audio' else 'subtitles'
        language = language or 'und'
        tracks = session.setdefault('tracks', {'audio':[], 'subtitles':[]})[group]
        if not any(track['id']==int(index) for track in tracks):
            tracks.append({'id':int(index),'language':language,'label':LANGUAGES.get(language,language if language!='und' else 'Onbekende taal'),
                           'default':'(default)' in text,'original':'(original)' in text,'supported':kind=='audio' or codec in TEXT_SUBTITLES})
    if 'stream mapping:' in text or text.startswith('output #0'):
        session['tracks_ready'] = True


def options(session):
    return {'ready':session.get('tracks_ready',False),'audio':session.get('tracks',{}).get('audio',[]),
            'subtitles':session.get('tracks',{}).get('subtitles',[]),
            'audio_id':session.get('audio_id'), 'subtitle_id':session.get('subtitle_id')}


def configure(mobile, session, audio=None, subtitle=None, automatic=False, position=0):
    if not session.get('tracks_ready'):
        raise ValueError('De talen worden nog gelezen. Probeer zo opnieuw.')
    tracks=session.get('tracks',{'audio':[],'subtitles':[]})
    if automatic:
        audio=next((t['id'] for t in tracks['audio'] if t.get('original')),next((t['id'] for t in tracks['audio'] if t['default']),next((t['id'] for t in tracks['audio']),None)))
        subtitle=next((t['id'] for t in tracks['subtitles'] if t['language'] in ('nl','nld','dut') and t['supported']),None) if session.get('subtitle_language','nl')!='off' else None
        choice=session.get('track_choice',{})
        if 'audio_track' in choice and choice['audio_track'] in [t['id'] for t in tracks['audio']]:audio=choice['audio_track']
        if 'subtitle_track' in choice and choice['subtitle_track'] in [None]+[t['id'] for t in tracks['subtitles'] if t['supported']]:subtitle=choice['subtitle_track']
    else:
        if isinstance(audio,bool) or audio not in [t['id'] for t in tracks['audio']]+[None]:raise ValueError('Deze audiotaal is niet beschikbaar.')
        if isinstance(subtitle,bool) or subtitle not in [t['id'] for t in tracks['subtitles'] if t['supported']]+[None]:raise ValueError('Dit ondertitelingsformaat kan niet worden weergegeven.')
    original=next((t['id'] for t in tracks['audio']),None)
    changed=audio!=session.get('audio_id',original) or subtitle!=session.get('subtitle_id')
    session.update(audio_id=audio,subtitle_id=subtitle,tracks_configured=True)
    if not changed:return False
    command=list(session['command'])
    # Replace only output maps; never open another provider input.
    start=command.index('-map');end=command.index('-vf')
    command[start:end]=['-map','0:v:0?','-map',f'0:{audio}?' if audio is not None else '0:a:0?','-dn']
    for flag in ('-c:s','-var_stream_map','-master_pl_name'):
        if flag in command:
            index=command.index(flag);del command[index:index+2]
    if subtitle is not None:
        start=command.index('-vf')
        command[start:start]=['-map','0:'+str(subtitle),'-c:s','webvtt']
        command[-1:-1]=['-var_stream_map','v:0,a:0,s:0,sgroup:subs' if tracks['audio'] else 'v:0,s:0,sgroup:subs','-master_pl_name','unused-master.m3u8']
    else:
        command.insert(command.index('-vf'),'-sn')
    session['command']=command
    session['initial_position']=position
    session['initial_seek_prepared']=True
    if not mobile.seekable.enabled(session):session['position']=position
    mobile.seekable.restart(session,math.floor(position))
    return True


def master(session):
    language=next(t['language'] for t in session['tracks']['subtitles'] if t['id']==session['subtitle_id'])
    label=LANGUAGES.get(language,'Ondertiteling')
    return ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="'+label+'",LANGUAGE="'+language+'",DEFAULT=YES,AUTOSELECT=YES,URI="captions.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=2600000,SUBTITLES="subs"\nvideo.m3u8\n').encode(),'application/vnd.apple.mpegurl'
