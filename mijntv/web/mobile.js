'use strict';
const $ = id => document.getElementById(id);
const video = $('video');
let channels = [], recordings = [], filter = 'Alles', recordingFilter = 'saved', guide = [], guideDay = 0, guideGeneration = 0;
let player = null, hls = null, request = null, playerGeneration = 0, playerTimer, heartbeat, retries = 0, restarting = false, attaching = false;
let playbackQueue = Promise.resolve();
function playbackMutation(path, body) {
  const pending = playbackQueue.then(()=>api(path, body, true));
  playbackQueue = pending.catch(()=>{});
  return pending;
}
let homeContext={signature:''}, homeResumeGeneration=0, heroPreviewKey='';
let nativeCountdownExit=false,nextWasFullscreen=false,nextRequestFullscreen=false;
let libraryEpoch=0, historyMutationQueue=Promise.resolve();
let sharedLibrary=null, libraryRequest=null, progressQueue=Promise.resolve(), nextCandidate=null, nextTimer=null, nextDeadline=0;
let activeProfile='home';
let playerExpanded=false, nativePlayerFullscreen=false, nativeVideoFullscreen=false;
let origin = null, preview = null, toastTimer, lastPause = null, lastSaved = 0;
function stored(storage,key) {try{return storage.getItem(key);}catch(_){return null;}}
function store(storage,key,value) {try{if(value===null)storage.removeItem(key);else storage.setItem(key,value);}catch(_){}}
function resume(id) {if(sharedLibrary)return sharedLibrary.positions[id]||null;try{return JSON.parse(stored(localStorage,'iptv-position-'+id)||'null');}catch(_){return null;}}
const clock = value => new Date(value * 1000).toLocaleTimeString('nl-NL', {hour:'2-digit', minute:'2-digit'});
const date = value => new Date(value * 1000).toLocaleDateString('nl-NL', {day:'numeric', month:'short'});
const time = value => { const s = Math.max(0, Math.floor(value || 0)); return (s >= 3600 ? Math.floor(s / 3600) + ':' : '') + (s >= 3600 ? String(Math.floor(s % 3600 / 60)).padStart(2,'0') : Math.floor(s / 60)) + ':' + String(s % 60).padStart(2,'0'); };
function element(tag, className, text) { const e = document.createElement(tag); if (className) e.className = className; if (text !== undefined) e.textContent = text; return e; }
function action(text, callback, className='text-button') { const e = element('button',className,text); e.type='button'; e.onclick=callback; return e; }
async function api(path, body, keepalive=false) {
  const response = await fetch(path, body === undefined ? {credentials:'same-origin',cache:'no-store'} : {method:'POST',credentials:'same-origin',keepalive,headers:{'Content-Type':'application/json','X-MijnTV':'1'},body:JSON.stringify(body)});
  if(response.status===401){location.assign('/auth/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));throw new Error('Log opnieuw in.');}
  const result = await response.json();
  if (!response.ok) throw Error(response.status === 401 ? 'Log opnieuw in op je mediacenter.' : result.error || 'De verbinding is onderbroken.');
  return result;
}
function toast(message) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true,5000); }
function logo(channel) {
  const wrap=element('span','channel-logo');
  if (channel.logo) {
    const img=document.createElement('img'); img.src=channel.logo; img.alt=''; img.loading='lazy';
    img.onerror=()=>wrap.replaceChildren(document.createTextNode(channel.label.slice(0,2).toUpperCase())); wrap.append(img);
  } else wrap.textContent=channel.label.slice(0,2).toUpperCase();
  return wrap;
}
function progress(programme) { return programme ? Math.max(0,Math.min(100,100*(Date.now()/1000-programme.start)/(programme.end-programme.start))) : 0; }
function track(programme) { const e=element('span','channel-track'), fill=element('span'); fill.style.width=progress(programme)+'%'; e.append(fill); return e; }
function channelRow(channel) {
  const programme=channel.epg?.now, row=action('',()=>openPlayer({kind:'live',id:channel.id}),'channel-row');
  const copy=element('span','channel-info'); copy.append(element('strong','',programme?.title || channel.label));
  const meta=element('span','channel-meta'); meta.append(element('span','',channel.label),element('span','',programme ? clock(programme.start)+' – '+clock(programme.end) : 'Live'));
  copy.append(meta,track(programme)); row.append(logo(channel),copy,element('span','row-play','▶'));
  row.disabled=!channel.available; return row;
}
function category(channel) {
  const name=(channel.label+' '+(channel.group_name || '')).toLowerCase();
  if (/sport|espn|ziggo|eurosport|viaplay/.test(name)) return 'Sport';
  if (/radio|muziek|music/.test(name)) return 'Muziek';
  if (/film|hbo|videoland|prime/.test(name)) return 'Film';
  if (/nieuws|cnn|politiek|news|rtl z/.test(name)) return 'Nieuws';
  return '';
}
function renderChannels() {
  const query=$('channel-search').value.toLocaleLowerCase('nl-NL').trim();
  const shown=channels.filter(c=>(filter==='Alles'||category(c)===filter) && (c.label+' '+(c.epg?.now?.title||'')).toLocaleLowerCase('nl-NL').includes(query));
  $('all-channels').replaceChildren(...shown.map(channelRow)); $('channel-count').textContent=shown.length+' zenders'; $('empty-search').hidden=shown.length>0;
}
function recordingRow(item, compact=false) {
  const playable=item.playable&&!['queued','recording'].includes(item.state), row=element('div','recording-card');
  const main=element(playable?'button':'div','recording-main'); if(playable) main.onclick=()=>openPlayer({kind:'recording',id:item.id});
  const saved=resume(item.id);
  const copy=element('span','recording-copy'); copy.append(element('span','small-muted',item.channel+' · '+date(item.start)),element('strong','',item.title));
  copy.append(element('p','',clock(item.start)+' – '+clock(item.end)),element('span','tag',playable&&saved?.position>10?'VERDER KIJKEN':item.status)); if(item.error) copy.append(element('p','',item.error));
  const art=element('span','recording-art',playable?'▶':'◷');if(playable){const img=element('img');img.alt='';img.loading='lazy';img.src='/api/mobile/recording-cover?id='+encodeURIComponent(item.id);let attempts=0;img.onerror=()=>{img.hidden=true;if(attempts++<2)setTimeout(()=>{if(img.isConnected)img.src='/api/mobile/recording-cover?id='+encodeURIComponent(item.id)+'&retry='+attempts;},5000);};img.onload=()=>img.hidden=false;art.append(img);}main.append(art,copy); row.append(main);
  if(!compact) { const actions=element('span','recording-actions');
    if(['queued','recording'].includes(item.state)) actions.append(action(item.state==='recording'?'Stoppen':'Annuleren',async()=>{ if(confirm('Deze opname '+(item.state==='recording'?'stoppen':'annuleren')+'?')) await mutate('/api/recordings/cancel',{id:item.id},'Opname geannuleerd.'); }));
    else actions.append(action('Verwijderen',async()=>{if(confirm('Opname “'+item.title+'” verwijderen?')) await mutate('/api/recordings/delete',{id:item.id},'Opname verwijderd.');}));
    row.append(actions); }
  return row;
}
function renderRecordings() {
  const planned=recordingFilter==='planned';
  const shown=recordings.filter(r=>planned?['queued','recording'].includes(r.state):!['queued','recording','cancelled'].includes(r.state)).sort((a,b)=>planned?a.start-b.start:b.start-a.start);
  $('recordings-heading').textContent=planned?'Gepland & bezig':'Opgenomen'; $('recording-count').textContent=shown.length+' programma’s';
  $('recordings-list').replaceChildren(...(shown.length?shown.map(r=>recordingRow(r)):[element('p','empty',planned?'Nog geen opnames gepland. Kies een programma in de gids.':'Nog geen bewaarde opnames. Plan iets leuks voor later.')]));
  const saved=recordings.filter(r=>r.playable&&!['queued','recording'].includes(r.state)).slice(0,3);
  $('home-recordings').replaceChildren(...(saved.length?saved.map(r=>recordingRow(r,true)):[element('p','empty','Nog niets opgenomen. In de gids vind je iets voor later.')]));
}
function renderHome() {
  const last=stored(localStorage,'iptv-last-live-'+homeContext.signature);
  const available=channels.filter(c=>c.available);
  const npo1=c=>/\bnpo\s*1(?!\d)/i.test(c.label);
  const featured=available.find(c=>c.id===last)||available.find(c=>npo1(c)&&!/extra/i.test(c.label))||available.find(npo1)||available[0];
  $('home-channels').replaceChildren(...(channels.length?channels.slice(0,4).map(channelRow):[element('p','empty','Voeg eerst favorieten toe in het beheer van je mediacenter.')]));
  $('featured').disabled=!featured;
  updateHeroPreview(featured);
  if(featured) {
    const p=featured.epg?.now; $('hero-channel').textContent=featured.label; $('hero-title').textContent=p?.title || 'Zet je favoriete zender aan.';
    $('hero-time').textContent=p ? clock(p.start)+' – '+clock(p.end)+' · nog '+Math.max(1,Math.ceil((p.end-Date.now()/1000)/60))+' min' : 'Je programma speelt direct op je telefoon.';
    $('hero-progress').style.width=progress(p)+'%'; $('hero-logo').replaceChildren(logo(featured)); $('featured').onclick=()=>openPlayer({kind:'live',id:featured.id});
  }
}
function updateHeroPreview(channel) {
  const image=$('hero-live-image'),label=$('hero-preview-label');
  const key=channel?channel.id+':'+Math.floor(Date.now()/30000):'';
  if(key===heroPreviewKey)return;heroPreviewKey=key;
  image.hidden=true;label.hidden=true;image.removeAttribute('src');
  if(!channel||document.hidden)return;
  let attempt=0;
  const address='/api/mobile/live-preview?id='+encodeURIComponent(channel.id)+'&v='+Math.floor(Date.now()/30000);
  image.onload=()=>{if(heroPreviewKey===key){image.hidden=false;label.hidden=false;}};
  image.onerror=()=>{image.hidden=true;label.hidden=true;if(attempt++<2)setTimeout(()=>{if(heroPreviewKey===key&&!document.hidden)image.src=address+'&attempt='+attempt;},4000);};
  image.src=address;
}
function localJson(key,fallback=[]){try{return JSON.parse(stored(localStorage,key)||'null')||fallback;}catch(_){return fallback;}}
function applySharedLibrary(state){
  sharedLibrary=state;
  $('watch-profile-name').textContent=state.profile.name;
  $('autoplay-next').checked=state.profile.preferences.autoplay_next;
  $('default-subtitles').value=state.profile.preferences.subtitle_language;
  $('library-sync-status').textContent='Kijklijst en voortgang zijn gesynchroniseerd.';
  if(typeof renderRecordings==='function')renderRecordings();
}
function localLibraryImport(signature){
  const storedSaved=localJson('iptv-vod-saved-'+signature),storedHistory=localJson('iptv-vod-history-'+signature);
  const saved=(Array.isArray(storedSaved)?storedSaved:[]).filter(item=>item&&['movie','series'].includes(item.kind)).map(item=>({kind:item.kind,id:item.id}));
  const progress=new Map();
  for(const item of (Array.isArray(storedHistory)?storedHistory:[])){
    if(!item)continue;const kind=item.kind==='series'?'episode':'movie',id=item.episode?.id||item.id;
    if(id&&item.position>0)progress.set(kind+':'+id,{kind,id,position:item.position,duration:item.duration||0});
  }
  try{for(let index=0;index<localStorage.length&&progress.size<1000;index++){
    const key=localStorage.key(index),prefix='iptv-position-'+signature+'-';let kind,id;
    if(key.startsWith(prefix)){const match=key.slice(prefix.length).match(/^(movie|episode)-([0-9]+)$/);if(!match)continue;kind=match[1];id=match[2];}
    else if(/^iptv-position-[a-f0-9]{32}$/.test(key)){kind='recording';id=key.slice('iptv-position-'.length);}
    else continue;
    const entry=localJson(key,null);if(entry&&entry.position>0&&!progress.has(kind+':'+id))progress.set(kind+':'+id,{kind,id,position:entry.position,duration:entry.duration||0});
  }}catch(_){}
  let client=stored(localStorage,'iptv-library-client');if(!client){client=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);store(localStorage,'iptv-library-client',client);}
  return {profile:activeProfile,signature,client,saved:saved.slice(0,300),progress:[...progress.values()]};
}
async function refreshSharedLibrary(){
  if(libraryRequest)return libraryRequest;
  libraryRequest=(async()=>{
    await progressQueue;
    const epoch=libraryEpoch;
    let state=await api('/api/mobile/library?profile='+encodeURIComponent(activeProfile));
    const marker='iptv-library-imported-'+activeProfile+'-'+state.signature;
    if(!stored(localStorage,marker)){
      state=await api('/api/mobile/library/import',localLibraryImport(state.signature));store(localStorage,marker,'1');
    }
    if(epoch!==libraryEpoch)return sharedLibrary||state;
    applySharedLibrary(state);return state;
  })();
  try{return await libraryRequest;}finally{libraryRequest=null;}
}
function syncPlaybackProgress(ended=false){
  if(!player||!request||!['movie','episode','recording'].includes(player.kind))return Promise.resolve();
  const key=playbackKey(request),current=position(),duration=player.duration||0;
  const completed=ended||!!(duration&&current>=duration-10);
  if(sharedLibrary)sharedLibrary.positions[key]={position:completed?0:current,duration,completed,watched:completed||!!sharedLibrary.positions[key]?.watched};
  const data={token:player.token,sequence:(player.progress_sequence=(player.progress_sequence||0)+1),position:current,duration,ended};
  progressQueue=progressQueue.catch(()=>{}).then(()=>api('/api/mobile/progress',data,true)).then(()=>{
    $('library-sync-status').textContent='Kijklijst en voortgang zijn gesynchroniseerd.';
  }).catch(()=>{$('library-sync-status').textContent='Voortgang kon nog niet worden gesynchroniseerd. Controleer je verbinding.';});
  return progressQueue;
}
function cancelNext(clear=false){
  clearInterval(nextTimer);nextTimer=null;nextDeadline=0;
  if(clear){nextCandidate=null;nextWasFullscreen=false;$('up-next').hidden=true;}
  else if(nextCandidate){$('up-next-status').textContent='Automatisch starten geannuleerd.';$('up-next-cancel').hidden=true;}
}
function startNext(){
  const next=nextCandidate;if(!next||!$('player').open)return;
  const returnFullscreen=nextWasFullscreen;cancelNext(true);nextRequestFullscreen=returnFullscreen;playVod(next.library,next.episode,true);
}
function scheduleNext(){
  cancelNext(true);if(!player||player.kind!=='episode'||!player.next_episode)return;
  nextCandidate=player.next_episode;$('up-next').hidden=false;
  if(nativeVideoFullscreen&&video.webkitExitFullscreen){nextWasFullscreen=true;nativeCountdownExit=true;try{video.webkitExitFullscreen();}catch(_){nativeCountdownExit=false;}}
  $('up-next-title').textContent='Hierna: seizoen '+nextCandidate.episode.season+', aflevering '+nextCandidate.episode.number+' · '+nextCandidate.episode.title;
  if(sharedLibrary?.profile.preferences.autoplay_next===false||document.hidden){$('up-next-status').textContent='Klaar voor de volgende aflevering.';$('up-next-cancel').hidden=true;return;}
  nextDeadline=Date.now()+10000;$('up-next-cancel').hidden=false;
  const tick=()=>{
    const remaining=Math.max(0,Math.ceil((nextDeadline-Date.now())/1000));
    $('up-next-status').textContent='Begint automatisch over '+remaining+' seconden.';
    if(!remaining){clearInterval(nextTimer);nextTimer=null;if(document.hidden)cancelNext();else startNext();}
  };
  tick();nextTimer=setInterval(tick,250);
}
$('up-next-play').onclick=startNext;$('up-next-cancel').onclick=()=>cancelNext();
async function saveWatchPreferences(){
  try{const result=await api('/api/mobile/preferences',{profile:activeProfile,preferences:{autoplay_next:$('autoplay-next').checked,subtitle_language:$('default-subtitles').value}});if(sharedLibrary)sharedLibrary.profile=result.profile;if(!result.profile.preferences.autoplay_next)cancelNext();$('library-sync-status').textContent='Kijkvoorkeuren opgeslagen.';}
  catch(error){toast(error.message);}
}
$('autoplay-next').onchange=saveWatchPreferences;$('default-subtitles').onchange=saveWatchPreferences;
async function refreshHomeContinue() {
  const generation=++homeResumeGeneration, signature=homeContext.signature;
  try{
    const state=await refreshSharedLibrary();
    if(generation!==homeResumeGeneration||homeContext.signature!==signature)return;
    renderHomeContinue(state.history);
  }catch(_){if(generation===homeResumeGeneration)$('home-continue').hidden=true;}
}
function renderHomeContinue(items){
    const rows=(items||[]).slice(0,8).map(item=>{
      const button=action('',()=>playVod(item,item.episode||null),'resume-card');
      const progress=item.duration?Math.round(item.position/item.duration*100):0;
      button.append(vodPoster(item,true),element('strong','',item.title));
      if(item.episode)button.append(element('span','small-muted','Seizoen '+item.episode.season+' · aflevering '+item.episode.number));
      button.append(element('span','resume-action',item.next_episode?'▶ Volgende aflevering':'▶ Verder vanaf '+time(item.position)));
      const track=element('span','vod-progress'),fill=element('span');fill.style.width=Math.min(100,progress)+'%';track.append(fill);button.append(track);
      return continueCard(item,button,'resume-card-wrap');
    });
    $('home-continue-list').replaceChildren(...rows);$('home-continue').hidden=!rows.length;
}
function continueCard(item,card,className='vod-card-wrap'){
  const wrapper=element('article',className);
  const remove=action('×',()=>removeContinue(item,remove),'continue-remove');
  remove.setAttribute('aria-label','Verwijder '+item.title+' uit Verder kijken');
  remove.title='Verwijder uit Verder kijken';wrapper.append(card,remove);return wrapper;
}
function removeContinue(item,button){
  button.disabled=true;++libraryEpoch;
  const data={profile:activeProfile,signature:item.signature||vodSignature||homeContext.signature,kind:item.kind,id:item.id};
  historyMutationQueue=historyMutationQueue.catch(()=>{}).then(async()=>{
    try{
      const state=await api('/api/mobile/history/remove',data);++libraryEpoch;
      applySharedLibrary(state);
      const previous=localJson('iptv-vod-history-'+state.signature,[]);
      const history=(Array.isArray(previous)?previous:[]).filter(row=>row&&vodKey(row)!==vodKey(item));
      store(localStorage,'iptv-vod-history-'+state.signature,JSON.stringify(history));
      renderHomeContinue(state.history);if(!$('vod-view').hidden)renderVod();
      toast('Verwijderd uit Verder kijken. Je kijkpositie blijft bewaard.');
    }catch(error){++libraryEpoch;button.disabled=false;toast(error.message);}
  });
}
async function load() {
  try {
    const [favorites,items,storage,status,context]=await Promise.all([api('/api/favorites'),api('/api/recordings'),api('/api/storage'),api('/api/status'),api('/api/mobile/home')]);
    homeContext=context;
    channels=favorites; recordings=items; $('connection-message').hidden=true;document.querySelector('.connection-card').classList.remove('offline');document.querySelector('.connection-card strong').textContent='Verbonden met je mediacenter';
    const selected=$('guide-channel').value;
    $('guide-channel').replaceChildren(...channels.map(c=>new Option(c.label,c.id)));
    if(channels.some(c=>c.id===selected)) $('guide-channel').value=selected;
    $('version').textContent='Versie '+(status.revision||'local').slice(0,8);
    $('storage-text').textContent=storage.available?(storage.free/1e9).toFixed(1)+' GB vrij van '+(storage.total/1e9).toFixed(0)+' GB':storage.error||'Opslag niet beschikbaar';
    $('storage-progress').style.width=storage.available?Math.max(0,100*(storage.total-storage.free)/storage.total)+'%':'0%';
    renderHome(); renderChannels(); renderRecordings();refreshHomeContinue();
  } catch(error) { document.querySelector('.connection-card').classList.add('offline');document.querySelector('.connection-card strong').textContent='Verbinding onderbroken';$('connection-message').textContent=error.message+' Controleer je wifi of WireGuard-VPN.'; $('connection-message').hidden=false; }
}
async function mutate(path,body,message) { try { await api(path,body); toast(message); await load(); if(location.hash==='#guide')renderGuide(); } catch(error){toast(error.message);} }
async function loadGuide() {
  const id=$('guide-channel').value, generation=++guideGeneration;
  if(!id) { $('guide-list').replaceChildren(element('p','empty','Je hebt nog geen favoriete zenders.')); return; }
  $('guide-list').replaceChildren(element('p','empty','De programmagids wordt opgehaald…'));
  try {
    const data=await api('/api/mobile/guide?id='+encodeURIComponent(id)); if(generation!==guideGeneration)return;
    guide=data.programmes||[]; $('guide-notice').textContent=data.notice||''; renderDays(data.days||0);renderGuide();
  } catch(error){if(generation===guideGeneration)$('guide-list').replaceChildren(element('p','empty',error.message));}
}
function dayLabel(offset) {
  if(offset===0)return 'Vandaag';if(offset===-1)return 'Gisteren';if(offset===1)return 'Morgen';
  const d=new Date();d.setDate(d.getDate()+offset);
  return d.toLocaleDateString('nl-NL',{weekday:'short',day:'numeric',month:'short'});
}
function renderDays(archiveDays) {
  const today=new Date();today.setHours(0,0,0,0);
  const future=Math.min(7,Math.max(1,...guide.map(p=>Math.floor((p.start*1000-today.getTime())/86400000))));
  const past=Math.min(7,Math.max(1,archiveDays));
  if(guideDay < -past||guideDay>future)guideDay=0;
  const buttons=[];
  for(let offset=-past;offset<=future;offset++){
    const button=element('button','chip'+(offset===guideDay?' active':''),dayLabel(offset));
    button.dataset.day=String(offset);button.setAttribute('aria-pressed',String(offset===guideDay));buttons.push(button);
  }
  $('guide-days').replaceChildren(...buttons);
  $('guide-days').querySelector('.active')?.scrollIntoView({inline:'center',block:'nearest'});
}
function programmeRecording(p) { return recordings.find(r=>r.channel_id===$('guide-channel').value && r.title===p.title && ['queued','recording'].includes(r.state) && r.start<p.end && r.end>p.start); }
function renderGuide() {
  const target=new Date(); target.setDate(target.getDate()+guideDay); const key=target.toLocaleDateString('nl-NL');
  $('guide-heading').textContent=dayLabel(guideDay);
  const shown=guide.filter(p=>new Date(p.start*1000).toLocaleDateString('nl-NL')===key);
  $('guide-list').replaceChildren(...(shown.length?shown.map(p=>{
    const row=action('',()=>openProgramme(p),'guide-programme'), copy=element('span','guide-copy'), now=Date.now()/1000;
    copy.append(element('strong','',p.title),element('p','',clock(p.start)+' – '+clock(p.end)));
    const current=p.start<=now&&p.end>now, recording=programmeRecording(p);
    if(current)copy.append(track(p));
    row.append(element('time','guide-time',clock(p.start)),copy);
    if(recording)row.append(element('span','tag',recording.state==='recording'?'NEEMT OP':'GEPLAND'));
    else if(current)row.append(element('span','tag','NU'));
    else if(p.archive)row.append(element('span','tag','KIJK TERUG'));
    return row;
  }):[element('p','empty','Geen programma’s voor deze dag beschikbaar.')]));
}
function openProgramme(p) {
  const id=$('guide-channel').value, channel=channels.find(c=>c.id===id), now=Date.now()/1000;
  $('programme-title').textContent=p.title; $('programme-meta').textContent=channel?.label||'Programma';
  $('programme-time').textContent=date(p.start)+' · '+clock(p.start)+' – '+clock(p.end); $('programme-description').textContent=p.description||'';
  const actions=$('programme-actions'); actions.replaceChildren(); const recording=programmeRecording(p);
  if(p.end<=now&&p.archive)actions.append(action('Terugkijken',()=>{$('programme').close();openPlayer({kind:'archive',id,start:p.start});},'primary-button'));
  if(p.start<=now&&p.end>now)actions.append(action('Live kijken',()=>{$('programme').close();openPlayer({kind:'live',id});},'primary-button'));
  if(recording)actions.append(action(recording.state==='recording'?'Opname stoppen':'Planning annuleren',async()=>{if(confirm('Deze opname annuleren?')){await mutate('/api/recordings/cancel',{id:recording.id},'Opname geannuleerd.');$('programme').close();}},'primary-button secondary-action'));
  else if(p.end>now)actions.append(action(p.start<=now?'Resterend deel opnemen':'Opname plannen',async()=>{await mutate('/api/recordings/schedule',{channel_id:id,start:p.start},'Opname ingepland.');$('programme').close();},'primary-button secondary-action'));
  else if(!p.archive)actions.append(element('p','small-muted','Voor deze uitzending is geen terugkijkarchief beschikbaar.'));
  $('programme').showModal();
}
// Films and series use separate server catalogues and category IDs.
let vodKind='movie', vodItems=[], vodTotal=0, vodSignature='', vodGeneration=0, vodDetailGeneration=0;
let vodVariants=[];
let vodFilter='all', vodSelected=null, vodSearchTimer, vodLoading=false, vodRequiresCategory=false, vodAllowed=null, vodPollTimer;
let vodFeatured=[], featuredIndex=0, featuredKey='', featuredFrame, featuredDay='';
function initialVodState(kind){let options={};try{options=JSON.parse(stored(localStorage,'iptv-vod-filters-'+kind)||'{}')||{};}catch(_){}return {category:'',query:'',sort:stored(localStorage,'iptv-vod-sort-'+kind)||'title',genre:options.genre||'',year:options.year||'',min_rating:options.min_rating||'',length:options.length||''};}
const vodState={movie:initialVodState('movie'),series:initialVodState('series')};
function vodSearchText(value){return String(value||'').replace(/^\s*(?:NL|EN|UK|US|FR|DE|BE|ES|IT|PT|TR|AR)\s*[|:\-]\s*/i,'').replace(/\b(?:4K|8K|UHD|FHD|HD|SD|HDR10|HDR|HEVC|H\.?26[45]|X26[45]|2160P|1080[PI]|720P|480P)\b/gi,' ').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();}
function hasVodFilters(){return ['genre','year','min_rating','length'].some(key=>vodState[vodKind][key]);}
function renderVodFilterOptions(options={}){
  const state=vodState[vodKind];
  for(const [id,key,label,values] of [['vod-genre','genre','Alle genres',options.genres||[]],['vod-year','year','Alle jaren',options.years||[]]]){
    const choices=[new Option(label,''),...values.map(value=>new Option(value,value))];
    if(state[key]&&!values.includes(state[key]))choices.push(new Option(state[key],state[key]));
    $(id).replaceChildren(...choices);$(id).value=state[key];
  }
  $('vod-rating').value=state.min_rating;$('vod-length').value=state.length;
  const count=['genre','year','min_rating','length'].filter(key=>state[key]).length;
  $('vod-filter-summary').textContent=count?'Filters · '+count+' actief':'Filters';
}
function matchesLocalVod(item){
  const state=vodState[vodKind],words=vodSearchText(state.query).split(' ').filter(Boolean),text=vodSearchText(item.title+' '+(item.year||''));
  if(!words.every(word=>text.includes(word)))return false;
  if(state.year&&item.year!==state.year)return false;
  if(state.genre&&!String(item.genre||'').split(/[,;/|]/).some(value=>vodSearchText(value)===vodSearchText(state.genre)))return false;
  if(state.min_rating&&Number(String(item.rating||'0').replace(',','.'))<Number(state.min_rating))return false;
  const duration=item.duration||0,short=vodKind==='series'?1800:5400,medium=vodKind==='series'?3600:7200;
  if(state.length&&(!duration||(state.length==='short'&&duration>=short)||(state.length==='medium'&&(duration<short||duration>medium))||(state.length==='long'&&duration<=medium)))return false;
  return true;
}
function vodLocal(name) {if(sharedLibrary&&sharedLibrary.signature===vodSignature)return name==='saved'?sharedLibrary.saved:sharedLibrary.history;try {const value=JSON.parse(stored(localStorage,'iptv-vod-'+name+'-'+vodSignature)||'[]');return Array.isArray(value)?value:[];}catch(_){return [];}}
function vodWrite(name,value) {store(localStorage,'iptv-vod-'+name+'-'+vodSignature,JSON.stringify(value.slice(0,300)));}
function vodKey(item) {return item.kind+':'+item.id;}
function playbackKey(data) {return ['movie','episode'].includes(data.kind)?data.signature+'-'+data.kind+'-'+data.id:data.id;}
function vodPoster(item, landscape=false) {
  const wrap=element('span','vod-poster');wrap.append(element('span','',item.title));
  if(item.cover||(landscape&&item.backdrop)){const img=element('img');let usedPoster=!landscape||!item.backdrop;img.src=usedPoster?item.cover:item.backdrop;img.alt='';img.loading='lazy';img.onerror=()=>{if(!usedPoster&&item.cover){usedPoster=true;img.src=item.cover;}else img.remove();};wrap.append(img);}
  return wrap;
}
function vodCard(item) {
  const row=action('',()=>openVodDetail(item),'vod-card');const poster=vodPoster(item);
  if(item.imdb_rank)poster.append(element('b','vod-badge','#'+item.imdb_rank+' IMDb'));else if(item.rating)poster.append(element('b','vod-badge','★ '+item.rating));
  row.append(poster,element('strong','',item.display_title||item.title),element('span','small-muted',[item.year,item.kind==='series'?'Serie':'Film'].filter(Boolean).join(' · ')));
  if(item.variants_count>1)row.append(element('span','small-muted',item.variants_count+' versies'));
  if(item.position>10){const track=element('span','vod-progress'),fill=element('span');fill.style.width=(item.duration>0?Math.min(100,item.position/item.duration*100):0)+'%';track.append(fill);row.append(track);}
  return row;
}
function renderVod() {
  renderBottomNav();
  const local=vodFilter==='saved'?vodLocal('saved'):vodLocal('history');
  const q=vodState[vodKind].query.toLocaleLowerCase('nl-NL').trim();
  const shown=vodFilter==='all'?vodItems:local.filter(i=>i.kind===vodKind&&(vodAllowed===null||vodAllowed.includes(i.category))&&(!vodState[vodKind].category||i.category===vodState[vodKind].category)&&matchesLocalVod(i));
  $('vod-list').replaceChildren(...shown.map(item=>vodFilter==='continue'?continueCard(item,vodCard(item)):vodCard(item)));
  if(!shown.length)$('vod-list').append(element('p','empty',vodFilter==='saved'?'Je kijklijst is nog leeg voor deze selectie. Bewaar een titel via de details.':vodFilter==='continue'?'Nog niets om verder te kijken voor deze selectie.':vodRequiresCategory?'Kies hierboven een bron of categorie. We halen het aanbod per categorie op.':vodState[vodKind].sort==='imdb250'?'Geen gekoppelde IMDb Top 250-films in deze selectie. Controleer de ranglijst in het beheer.':'Geen titels gevonden. Kies een andere categorie of zoekterm.'));
  $('vod-count').textContent=vodFilter==='all'&&vodRequiresCategory?'':(vodFilter==='all'?vodTotal:shown.length)+' titels';
  $('vod-list-heading').textContent=vodFilter==='saved'?'Mijn kijklijst':vodFilter==='continue'?'Verder kijken':vodKind==='movie'?'Ontdek iets moois':'Jouw volgende serie';
  $('vod-more').hidden=vodFilter!=='all'||vodItems.length>=vodTotal;
  renderVodFeatured(!!q||!!vodState[vodKind].category||vodFilter!=='all'||hasVodFilters());
  for(const [id,filter] of [['vod-all','all'],['vod-saved','saved'],['vod-continue','continue']]){const active=filter===vodFilter;$(id).classList.toggle('active',active);$(id).setAttribute('aria-pressed',String(active));}
}
function updateFeaturedIndex(index) {
  featuredIndex=Math.max(0,Math.min(index,vodFeatured.length-1));
  $('vod-featured-count').textContent=vodFeatured.length?(featuredIndex+1)+' / '+vodFeatured.length:'';
  $('vod-featured-prev').disabled=featuredIndex===0;$('vod-featured-next').disabled=featuredIndex>=vodFeatured.length-1;
  [...$('vod-featured-dots').children].forEach((dot,i)=>dot.setAttribute('aria-current',String(i===featuredIndex)));
}
function showFeatured(index) {
  updateFeaturedIndex(index);const slide=$('vod-featured-track').children[featuredIndex];if(!slide)return;
  $('vod-featured-track').scrollTo({left:slide.offsetLeft,behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
}
function renderVodFeatured(hide) {
  $('vod-featured').hidden=hide||!vodFeatured.length;if($('vod-featured').hidden)return;
  const key=vodKind+vodSignature+JSON.stringify(vodFeatured.map(i=>[i.id,i.cover,i.backdrop,i.title,i.rating]));
  if(key===featuredKey)return;featuredKey=key;
  const selectedId=$('vod-featured-track').children[featuredIndex]?.dataset.id;
  const slides=vodFeatured.map((item,index)=>{
    const slide=action('',()=>openVodDetail(item),'vod-featured');slide.dataset.id=item.id;slide.setAttribute('aria-label',item.title+' · '+(index+1)+' van '+vodFeatured.length);
    const art=element('span','vod-featured-art');art.append(vodPoster(item,true));const img=art.querySelector('img');if(img)img.loading=index?'lazy':'eager';
    const copy=element('span','vod-featured-copy');copy.append(element('span','tag light',vodKind==='movie'?'UITGELICHT · FILM':'UITGELICHT · SERIE'),element('strong','',item.title),element('span','',[item.year,item.rating?'★ '+item.rating:''].filter(Boolean).join(' · ')),element('span','watch-pill','▶  Bekijk details'));
    slide.append(art,copy);return slide;
  });
  $('vod-featured-track').replaceChildren(...slides);
  $('vod-featured-dots').replaceChildren(...vodFeatured.map((item,index)=>{const dot=action('',()=>showFeatured(index),'featured-dot');dot.setAttribute('aria-label','Toon '+item.title);return dot;}));
  $('vod-featured-controls').hidden=vodFeatured.length<2;$('vod-featured-hint').hidden=vodFeatured.length<2;
  updateFeaturedIndex(Math.max(0,vodFeatured.findIndex(i=>i.id===selectedId)));
  requestAnimationFrame(()=>{const slide=$('vod-featured-track').children[featuredIndex];if(slide)$('vod-featured-track').scrollLeft=slide.offsetLeft;});
}
$('vod-featured-prev').onclick=()=>showFeatured(featuredIndex-1);$('vod-featured-next').onclick=()=>showFeatured(featuredIndex+1);
$('vod-featured-track').addEventListener('scroll',()=>{cancelAnimationFrame(featuredFrame);featuredFrame=requestAnimationFrame(()=>{const track=$('vod-featured-track');let nearest=0;[...track.children].forEach((slide,index)=>{if(Math.abs(slide.offsetLeft-track.scrollLeft)<Math.abs(track.children[nearest].offsetLeft-track.scrollLeft))nearest=index;});updateFeaturedIndex(nearest);});},{passive:true});
$('vod-featured-track').addEventListener('keydown',event=>{if(event.key==='ArrowRight'||event.key==='ArrowLeft'){event.preventDefault();showFeatured(featuredIndex+(event.key==='ArrowRight'?1:-1));$('vod-featured-track').children[featuredIndex]?.focus({preventScroll:true});}});
async function loadVod(append=false,quiet=false) {
  clearTimeout(vodPollTimer);
  const generation=++vodGeneration,kind=vodKind,state={...vodState[kind]};
  vodLoading=true;$('vod-more').disabled=true;$('vod-retry').hidden=true;$('vod-notice').textContent='';
  if(!append&&!quiet){vodItems=[];$('vod-featured').hidden=true;$('vod-more').hidden=true;$('vod-count').textContent='';$('vod-list').replaceChildren(element('p','empty','Het aanbod van je provider wordt opgehaald…'));}
  const params=new URLSearchParams({kind,category:state.category,q:state.query,sort:state.sort,offset:String(append?vodItems.length:0)});
  for(const key of ['genre','year','min_rating','length'])if(state[key])params.set(key,state[key]);
  try {
    const result=await api('/api/mobile/vod?'+params);if(generation!==vodGeneration)return;
    if(append&&vodSignature&&vodSignature!==result.signature){vodItems=[];vodTotal=0;throw Error('Je provideraccount is gewijzigd. Open het aanbod opnieuw.');}
    renderVodFilterOptions(result.filter_options||{});$('vod-filter-notice').textContent=result.metadata_notice||'Genre en speelduur zijn niet bij iedere titel bekend.';
    featuredDay=result.featured_day||'';vodFeatured=result.featured||[];vodAllowed=result.allowed_categories??null;vodRequiresCategory=!!result.requires_category;vodSignature=result.signature;vodItems=append?[...new Map(vodItems.concat(result.items).map(i=>[i.id,i])).values()]:quiet&&vodItems.length>60?vodItems:result.items;vodTotal=result.total;
    const categories=[new Option(result.requires_category?'Kies een categorie':'Alle categorieën',''),...result.categories.map(c=>new Option(c.name,c.id))];
    if(state.category&&!result.categories.some(c=>c.id===state.category))categories.push(new Option('Eerdere categorie',state.category));
    $('vod-category').replaceChildren(...categories);$('vod-category').value=state.category;$('vod-notice').textContent=result.notice||'';renderVod();if(result.loading)vodPollTimer=setTimeout(()=>{if(['#films','#series'].includes(location.hash)&&!document.hidden)loadVod(false,true);},5000);
  }catch(error){if(generation!==vodGeneration)return;$('vod-notice').textContent=error.message;$('vod-retry').hidden=false;if(!append)$('vod-list').replaceChildren(element('p','empty',error.message));}
  finally{if(generation===vodGeneration){vodLoading=false;$('vod-more').disabled=false;}}
}
function enterVod(kind) {
  vodKind=kind;const series=kind==='series';$('vod-heading').textContent=window.pickMobileCopy(series?'seriesHead':'filmsHead');$('vod-subtitle').textContent=window.pickMobileCopy(series?'seriesSub':'filmsSub');
  $('vod-sort').replaceChildren(new Option('Alfabetisch A–Z','title'),new Option('Beste beoordeling (provider)','rating'),...(series?[]:[new Option('IMDb Top 250','imdb250')]));if(series&&vodState[kind].sort==='imdb250')vodState[kind].sort='title';$('vod-sort').value=vodState[kind].sort;
  $('vod-search').placeholder=series?'Zoek een serie':'Zoek een film';$('vod-search').setAttribute('aria-label',$('vod-search').placeholder);$('vod-search').value=vodState[kind].query;
  $('vod-length-label').textContent=series?'Afleveringsduur':'Speelduur';
  $('vod-length').replaceChildren(new Option('Alle lengtes',''),new Option(series?'Korter dan 30 minuten':'Korter dan 90 minuten','short'),new Option(series?'30–60 minuten':'90–120 minuten','medium'),new Option(series?'Langer dan 60 minuten':'Langer dan 120 minuten','long'));
  renderVodFilterOptions();
  $('vod-category').replaceChildren(new Option('Alle categorieën',''));vodFilter='all';renderBottomNav();loadVod();
}
async function saveVod(item) {
  const has=vodLocal('saved').some(x=>vodKey(x)===vodKey(item));
  try{const state=await api('/api/mobile/library',{profile:activeProfile,signature:vodSignature,kind:item.kind,id:item.id,saved:!has});applySharedLibrary(state);toast(has?'Verwijderd uit je kijklijst.':'Toegevoegd aan je kijklijst.');renderVodActions();if(vodFilter==='saved')renderVod();}
  catch(error){toast(error.message);}
}
function vodLibrary(item) {return {id:item.id,kind:item.kind,title:item.title,cover:item.cover,year:item.year,rating:item.rating,category:item.category||'',display_title:item.display_title,genre:item.genre,duration:item.duration};}
function playVod(item,episode=null,fromStart=false) {
  const kind=episode?'episode':'movie', id=episode?episode.id:item.id;
  const data={kind,id,signature:item.signature,library:vodLibrary(item),...(episode?{episode:{id:episode.id,title:episode.title,season:episode.season,number:episode.number}}:{})};
  const saved=resume(playbackKey(data));data.position=fromStart?0:saved?.position||item.position||0;
  if(item.duration&&data.position>=item.duration-5&&!episode)data.position=0;
  $('vod-detail').close();openPlayer(data);
}
function renderVodActions() {
  const item=vodSelected;if(!item)return;const actions=$('vod-detail-actions');actions.replaceChildren();
  if(item.kind==='movie'){
    const saved=resume(playbackKey({kind:'movie',id:item.id,signature:item.signature}));const continuing=saved?.position>10&&(!item.duration||saved.position<item.duration-5);
    actions.append(action(continuing?'▶ Verder kijken · '+time(saved.position):'▶ Film kijken',()=>playVod(item),'primary-button'));
    if(continuing)actions.append(action('Vanaf het begin',()=>playVod(item,null,true),'primary-button secondary-action'));
  }
  const has=vodLocal('saved').some(x=>vodKey(x)===vodKey(item));actions.append(action(has?'✓ Op je kijklijst':'＋ Op mijn kijklijst',()=>saveVod(vodLibrary(item)),'primary-button secondary-action'));
}
function renderVodEpisodes() {
  const item=vodSelected;if(!item)return;const season=Number($('vod-season').value),episodes=(item.episodes||[]).filter(e=>e.season===season);
  $('vod-episodes').replaceChildren(...episodes.map(ep=>{const row=action('',()=>playVod(item,ep),'vod-episode'),copy=element('div');copy.append(element('strong','',ep.title));const saved=resume(playbackKey({kind:'episode',id:ep.id,signature:item.signature}));
    copy.append(element('small','',[ep.duration?Math.round(ep.duration/60)+' min':'',saved?.watched?'✓ Bekeken':saved?.position>10?'Verder vanaf '+time(saved.position):''].filter(Boolean).join(' · ')));
    if(ep.description)copy.append(element('small','',ep.description.slice(0,180)));row.append(element('span','',String(ep.number).padStart(2,'0')),copy,element('span','','▷'));return row;}));
  if(!episodes.length)$('vod-episodes').append(element('p','empty','Geen afleveringen beschikbaar voor dit seizoen.'));
}
async function openVodDetail(item) {
  const generation=++vodDetailGeneration;vodSelected=null;vodVariants=[];$('vod-variant-wrap').hidden=true;$('vod-detail-title').textContent=item.title;$('vod-detail-kind').textContent=item.kind==='series'?'SERIE':'FILM';$('vod-detail-cover').replaceChildren(vodPoster(item));
  for(const id of ['vod-detail-meta','vod-detail-cast','vod-detail-description'])$(id).textContent='';$('vod-detail-notice').textContent='Details ophalen…';$('vod-detail-actions').replaceChildren();$('vod-seasons').hidden=true;
  if(!$('vod-detail').open)$('vod-detail').showModal();
  try {
    const detail=await api('/api/mobile/vod/detail?'+new URLSearchParams({kind:item.kind,id:item.id,category:vodState[item.kind]?.category||''}));if(generation!==vodDetailGeneration||!$('vod-detail').open)return;
    if(detail.signature!==vodSignature)throw Error('Het provideraccount is gewijzigd. Open het aanbod opnieuw.');
    vodSelected={...detail,display_title:item.display_title||detail.title,category:item.category||''};
    vodVariants=detail.variants||[];$('vod-variant-wrap').hidden=vodVariants.length<2;
    $('vod-variant').replaceChildren(...vodVariants.map(variant=>new Option([variant.title,variant.source_name].filter(Boolean).join(' · '),variant.id)));$('vod-variant').value=detail.id;
    $('vod-detail-title').textContent=detail.title;$('vod-detail-cover').replaceChildren(vodPoster(detail));
    $('vod-detail-meta').textContent=[detail.year,detail.genre,detail.duration?Math.round(detail.duration/60)+' min':'',detail.rating?'★ '+detail.rating:''].filter(Boolean).join(' · ');
    $('vod-detail-description').textContent=detail.description||'Geen beschrijving beschikbaar.';$('vod-detail-cast').textContent=[detail.director?'Regie: '+detail.director:'',detail.cast?'Met: '+detail.cast:''].filter(Boolean).join('\n');$('vod-detail-notice').textContent=detail.notice||'';renderVodActions();
    if(detail.kind==='series'){
      const seasons=[...new Set((detail.episodes||[]).map(e=>e.season))].sort((a,b)=>a-b);$('vod-season').replaceChildren(...seasons.map(n=>new Option(n===0?'Specials':'Seizoen '+n,String(n))));
      const history=vodLocal('history').find(x=>x.kind==='series'&&x.id===item.id);if(seasons.includes(history?.episode?.season))$('vod-season').value=String(history.episode.season);
      $('vod-seasons').hidden=false;renderVodEpisodes();
    }
  }catch(error){if(generation===vodDetailGeneration&&$('vod-detail').open){$('vod-detail-notice').textContent=error.message;$('vod-detail-actions').replaceChildren(action('Opnieuw proberen',()=>openVodDetail(item),'primary-button'));}}
}
function saveVodProgress(ended=false) {
  syncPlaybackProgress(ended);
  if(!request?.library||!player)return;const signature=request.signature,kind=request.kind;
  if(!['movie','episode'].includes(kind))return;
  const key='iptv-vod-history-'+signature;let list;try{list=JSON.parse(stored(localStorage,key)||'[]');if(!Array.isArray(list))list=[];}catch(_){list=[];}
  const library=request.library;list=list.filter(x=>vodKey(x)!==vodKey(library));
  if(!ended&&position()>10)list.unshift({...library,episode:request.episode,position:position(),duration:player.duration});
  store(localStorage,key,JSON.stringify(list.slice(0,100)));
}
$('vod-search').oninput=()=>{vodState[vodKind].query=$('vod-search').value;clearTimeout(vodSearchTimer);vodGeneration++;vodLoading=false;vodSearchTimer=setTimeout(()=>loadVod(),300);};
$('vod-category').onchange=()=>{vodState[vodKind].category=$('vod-category').value;loadVod();};
for(const [id,filter] of [['vod-all','all'],['vod-saved','saved'],['vod-continue','continue']])$(id).onclick=()=>{vodFilter=filter;if(!vodLoading&&vodSignature)renderVod();};
$('vod-sort').onchange=()=>{vodState[vodKind].sort=$('vod-sort').value;store(localStorage,'iptv-vod-sort-'+vodKind,$('vod-sort').value);loadVod();};
for(const [id,key] of [['vod-genre','genre'],['vod-year','year'],['vod-rating','min_rating'],['vod-length','length']])$(id).onchange=()=>{
  vodState[vodKind][key]=$(id).value;
  const state=vodState[vodKind];store(localStorage,'iptv-vod-filters-'+vodKind,JSON.stringify(Object.fromEntries(['genre','year','min_rating','length'].map(key=>[key,state[key]]))));
  loadVod();
};
$('vod-filter-reset').onclick=()=>{for(const key of ['genre','year','min_rating','length'])vodState[vodKind][key]='';store(localStorage,'iptv-vod-filters-'+vodKind,null);renderVodFilterOptions();loadVod();};
$('vod-variant').onchange=()=>{const item=vodVariants.find(item=>item.id===$('vod-variant').value);if(item)openVodDetail(item);};
$('vod-more').onclick=()=>loadVod(true);$('vod-retry').onclick=()=>loadVod();$('vod-season').onchange=renderVodEpisodes;
$('vod-close').onclick=()=>{++vodDetailGeneration;$('vod-detail').close();};$('vod-detail').addEventListener('cancel',()=>{++vodDetailGeneration;});

function renderBottomNav(view=location.hash.slice(1)||'home'){
  const vod=['films','series'].includes(view),nav=document.querySelector('.bottom-nav');
  const definitions=vod?[['all','◇','Ontdekken'],['saved','♡','Mijn kijklijst'],['continue','▶','Verder kijken']]:[['home','⌂','Home'],['channels','▦','Zenders'],['guide','◷','Terugkijken'],['recordings','◉','Opnames']];
  nav.setAttribute('aria-label',vod?(view==='films'?'Films':'Series')+' navigatie':'Live-tv navigatie');
  // Keep focused buttons stable while data refreshes or a filter changes.
  if(nav.dataset.context!==(vod?'vod':'live')){
    nav.replaceChildren(...definitions.map(([key,icon,label])=>{
      const button=action('',()=>{},'nav-button');button.append(element('span','',icon),document.createTextNode(label));button.firstChild.setAttribute('aria-hidden','true');
      if(vod){button.dataset.vodFilter=key;button.onclick=()=>{vodFilter=key;renderBottomNav();if(!vodLoading&&vodSignature)renderVod();};}
      else button.dataset.go=key;
      return button;
    }));nav.dataset.context=vod?'vod':'live';
  }
  for(const button of nav.children){const active=vod?button.dataset.vodFilter===vodFilter:button.dataset.go===view;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
}
function showView() {
  const requested=location.hash.slice(1)||'home', view=['home','channels','recordings','guide','films','series'].includes(requested)?requested:'home';
  const vod=['films','series'].includes(view);clearTimeout(vodSearchTimer);clearTimeout(vodPollTimer);if(!vod)++vodGeneration;
  document.querySelectorAll('.view').forEach(e=>e.hidden=e.id!==(vod?'vod':view)+'-view');
  renderBottomNav(view);
  document.querySelectorAll('.media-tabs button').forEach(e=>{const active=e.dataset.go===(vod?view:'home');e.classList.toggle('active',active);if(active)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current');});
  window.scrollTo(0,0); if(view==='guide'&&channels.length)loadGuide();if(vod){refreshSharedLibrary().then(()=>{if(vodFilter!=='all')renderVod();}).catch(()=>{});enterVod(view==='films'?'movie':'series');}if(view==='home')refreshHomeContinue();
}
function clearVideo() {
  attaching=true; clearTimeout(playerTimer); clearInterval(heartbeat); if(hls){hls.destroy();hls=null;}
  video.pause(); video.removeAttribute('src'); video.load(); origin=null;preview=null;lastPause=null;attaching=false;
}
function setPlayerStage(phase, message='') {
  $('player-surface').dataset.phase=phase;
  const stages=['connecting','preparing','buffering'];
  const current=stages.indexOf(phase);
  document.querySelectorAll('[data-player-step]').forEach((step,index)=>{
    step.classList.toggle('complete',phase==='playing'||(current>=0&&index<current));
    step.classList.toggle('current',phase!=='playing'&&index===current);
    if(phase!=='playing'&&index===current)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');
  });
  $('player-stage-note').textContent=message||({connecting:'Verbinding maken met de aanbieder…',preparing:'Video ontvangen. Klaarmaken voor je telefoon…',buffering:'Video klaar. De speler vult de buffer…',playing:'Je kijkt nu.',error:'Afspelen is gestopt.'}[phase]||'');
}
function setLoading(message,phase='connecting') { $('player-loading').hidden=false; $('player-status').textContent=message; $('retry-player').hidden=true;setPlayerStage(phase,message); }
function failPlayer(message) { for(const id of ['timeline','rewind','forward'])$(id).disabled=true;if($('player-title').textContent==='Verbinden…')$('player-title').textContent='Even niet beschikbaar';$('player-loading').hidden=true;$('player-message').textContent=message;$('retry-player').hidden=false;$('pause').disabled=true;setPlayerStage('error',message); }
function stopFailedPlayer(message) {
  const token=player?.token,current=position();
  if(player&&['movie','episode'].includes(player.kind)){store(localStorage,'iptv-position-'+playbackKey(request),JSON.stringify({position:current,duration:player.duration}));saveVodProgress();}
  clearVideo();if(player){player.position=current;if(player.native_vod)player.initial_position=current;}
  if(request&&request.kind!=='live')request={...request,position:current};
  failPlayer(message);
  if(token)playbackMutation('/api/mobile/stop',{token}).then(()=>{if(stored(sessionStorage,'iptv-mobile-session')===token)store(sessionStorage,'iptv-mobile-session',null);}).catch(()=>{});
}
function handlePlayerState(state) {
  if(!['preempted','error','stopped'].includes(state.state))return false;
  if(state.state==='error'&&state.retryable===false)stopFailedPlayer(state.message);
  else restartPlayer(state.message);
  return true;
}
async function openPlayer(data, automatic=false) {
  cancelNext(true);
  if(!automatic) retries=0;
  data={...data,profile:activeProfile,native_vod:/Apple/.test(navigator.vendor)&&typeof video.webkitEnterFullscreen==='function'&&['movie','episode'].includes(data.kind)};
  video.controls=!['movie','episode'].includes(data.kind);$('fullscreen').disabled=true;
  if(['recording','movie','episode'].includes(data.kind)&&data.position===undefined)data={...data,position:resume(playbackKey(data))?.position||0};
  const generation=++playerGeneration, previous=player?.token || stored(sessionStorage,'iptv-mobile-session');
  request={...data}; restarting=false;clearVideo();player=null;
  for(const id of ['timeline','rewind','forward','pause'])$(id).disabled=true;
  $('timeline').value=0;$('elapsed').textContent='0:00';$('total').textContent=data.kind==='live'?'Live':'…';
  setLoading(data.kind==='recording'?'Je opname openen…':'Een vrij kijkaccount zoeken…');
  $('player-message').textContent='De tv en opnames houden voorrang.';$('player-title').textContent='Verbinden…';$('player-channel').textContent='';
  if(!$('player').open)$('player').showModal();
  try {
    const pending=playbackQueue.then(async()=>{
      if(generation!==playerGeneration)return null;
      const session=stored(sessionStorage,'iptv-mobile-session')||previous;
      const result=await api('/api/mobile/play',{...data,...(session?{session}:{}),...(automatic?{retry:true}:{})});
      if(generation!==playerGeneration){await api('/api/mobile/stop',{token:result.player.token},true);return null;}
      store(sessionStorage,'iptv-mobile-session',result.player.token);
      return result;
    });
    playbackQueue=pending.catch(()=>{});
    const result=await pending;
    if(!result||generation!==playerGeneration)return;
    player={...result.player,progress_sequence:0};renderPlayerLanguages(); store(sessionStorage,'iptv-mobile-session',player.token);
    $('player-title').textContent=player.title;$('player-channel').textContent=player.channel;$('player-kind').textContent=player.kind==='live'?'LIVE':player.kind==='archive'?'TERUGKIJKEN':player.kind==='movie'?'FILM':player.kind==='episode'?'SERIE':'OPNAME';
    $('go-live').hidden=player.kind!=='live';$('player-message').textContent=player.kind==='live'?'Je kijkt live. Spoelen kan binnen het gebufferde deel.':player.kind==='recording'?'Je opname speelt vanaf het gekozen moment.':['movie','episode'].includes(player.kind)?'Je kijkt op jouw moment. De tv en opnames houden voorrang.':'Je kijkt de gekozen uitzending terug.';
    setLoading('Verbinding maken met de aanbieder…');const started=Date.now();
    const poll=async()=>{
      if(generation!==playerGeneration||!player)return;
      try{
        const state=await api('/api/mobile/status?token='+encodeURIComponent(player.token));if(generation!==playerGeneration)return;
        if(handlePlayerState(state))return;
        player={...player,...state};
        setPlayerStage(state.phase||'connecting');$('player-status').textContent=$('player-stage-note').textContent;
        if(state.ready){if(player.kind==='live'){heroPreviewKey='';renderHome();}if(attachVideo(generation)!==false)heartbeat=setInterval(()=>checkPlayer(generation),10000);return;}
        if(Date.now()-started>30000){restartPlayer('Deze bron reageert niet.');return;}
        playerTimer=setTimeout(poll,1000);
      }catch(error){if(generation===playerGeneration)failPlayer(error.message);}
    };poll();
  }catch(error){
    if(generation!==playerGeneration)return;
    if(previous)playbackMutation('/api/mobile/stop',{token:previous}).catch(()=>{});
    store(sessionStorage,'iptv-mobile-session',null);failPlayer(error.message);
  }
}
async function checkPlayer(generation) {
  if(!player||generation!==playerGeneration)return;
  try {const state=await api('/api/mobile/status?token='+encodeURIComponent(player.token));if(generation!==playerGeneration)return;handlePlayerState(state);}
  catch(error){if(generation===playerGeneration)failPlayer(error.message);}
}
async function recoverPlayer(message, generation=playerGeneration) {
  if(!player||generation!==playerGeneration||!$('player').open)return;
  try{
    const state=await api('/api/mobile/status?token='+encodeURIComponent(player.token));
    if(generation!==playerGeneration||handlePlayerState(state))return;
  }catch(_){}
  if(generation===playerGeneration)restartPlayer(message);
}
function renderPlayerLanguages(){
  const visible=!!player&&['movie','episode'].includes(player.kind);$('player-languages').hidden=!visible;
  if(!visible)return;
  const tracks=player.tracks||{},audio=tracks.audio||[],subtitles=tracks.subtitles||[];
  $('player-audio').replaceChildren(...audio.map((track,index)=>new Option(track.label+(track.original?' · origineel':track.default?' · bronstandaard':index===0?' · origineel/bronstandaard':''),String(track.id))));
  $('player-audio').value=String(tracks.audio_id??audio[0]?.id??'');$('player-audio').disabled=audio.length<2;
  $('player-subtitles').replaceChildren(new Option('Uit','off'),...subtitles.filter(t=>t.supported).map(track=>new Option(track.label,String(track.id))));
  $('player-subtitles').value=tracks.subtitle_id===null||tracks.subtitle_id===undefined?'off':String(tracks.subtitle_id);
  $('player-subtitles').disabled=!subtitles.some(t=>t.supported);
  $('player-language-note').textContent=!tracks.ready?'Beschikbare talen worden gelezen…':!subtitles.length?'Deze bron bevat geen losse ondertitels.':subtitles.some(t=>!t.supported)?'Beeldondertitels uit deze bron kunnen niet worden weergegeven. Kies een beschikbare tekstondertiteling.':'Je keuze geldt voor deze video. Op iPhone kun je ondertiteling ook in de fullscreenbediening wijzigen.';
}
async function changePlayerLanguage(){
  if(!player)return;
  cancelNext(true);saveVodProgress();
  const generation=++playerGeneration,token=player.token,current=position();clearVideo();
  $('player-audio').disabled=true;$('player-subtitles').disabled=true;setLoading('Je taalkeuze toepassen…');
  try{
    const result=await api('/api/mobile/tracks',{token,position:current,audio:$('player-audio').value?Number($('player-audio').value):null,subtitle:$('player-subtitles').value==='off'?null:Number($('player-subtitles').value)});
    if(generation!==playerGeneration)return;player={...player,...result.player};request={...request,audio_track:result.player.tracks.audio_id,subtitle_track:result.player.tracks.subtitle_id};
    const poll=async()=>{if(generation!==playerGeneration||!player)return;try{
      const state=await api('/api/mobile/status?token='+encodeURIComponent(token));if(generation!==playerGeneration)return;
      if(handlePlayerState(state))return;player={...player,...state};
      if(state.ready){renderPlayerLanguages();attachVideo(generation);heartbeat=setInterval(()=>checkPlayer(generation),10000);}
      else playerTimer=setTimeout(poll,500);
    }catch(error){if(generation===playerGeneration)failPlayer(error.message);}};poll();
  }catch(error){if(generation===playerGeneration){renderPlayerLanguages();failPlayer(error.message);}}
}
$('player-audio').onchange=changePlayerLanguage;$('player-subtitles').onchange=changePlayerLanguage;
function attachVideo(generation) {
  renderPlayerLanguages();
  if(!player||generation!==playerGeneration)return;
  for(const id of ['timeline','rewind','forward','pause'])$(id).disabled=false;attaching=true;setLoading('Video klaar. Even bufferen…','buffering');
  $('fullscreen').disabled=!!player.native_vod;
  video.controls=!!player.native_vod||!['movie','episode'].includes(player.kind);
  if(player.native_vod)video.addEventListener('loadedmetadata',()=>{
    if(generation!==playerGeneration||!player?.native_vod)return;
    $('fullscreen').disabled=false;
    if(player.initial_position>0)video.currentTime=Math.min(player.initial_position,player.duration-.1);
    if(nextRequestFullscreen){nextRequestFullscreen=false;try{video.webkitEnterFullscreen();}catch(_){}}
  },{once:true});
  const nativeHls=video.canPlayType('application/vnd.apple.mpegurl');
  if(nativeHls&&/Apple/.test(navigator.vendor)){video.src=player.playlist;video.load();tryPlay();}
  else if(window.Hls?.isSupported()){
    hls=new Hls({enableWorker:false,backBufferLength:90,maxBufferLength:25});
    hls.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal&&generation===playerGeneration)recoverPlayer('De videostream is onderbroken.',generation);});
    hls.on(Hls.Events.MANIFEST_PARSED,tryPlay);hls.loadSource(player.playlist);hls.attachMedia(video);
  }else if(nativeHls){video.src=player.playlist;video.load();tryPlay();}
  else{attaching=false;failPlayer('Deze browser ondersteunt deze videospeler niet. Gebruik Safari op je iPhone of een actuele Chrome-browser.');playbackMutation('/api/mobile/stop',{token:player.token}).catch(()=>{});return false;}
  function tryPlay(){attaching=false;video.play().catch(()=>{if(generation===playerGeneration){$('player-loading').hidden=true;$('player-message').textContent='Tik op afspelen om te beginnen.';}});}
}
function restartPlayer(message) {
  if(restarting||!request||!$('player').open)return;restarting=true;
  const limit=['movie','episode'].includes(request.kind)?1:2;
  if(retries>=limit){stopFailedPlayer(message||'Deze stream reageert niet. Probeer later opnieuw of kies een andere titel.');return;}
  retries++;const generation=playerGeneration;const resume=player?.kind==='live'?0:position();setLoading((message?message+' ':'')+'Opnieuw verbinden · poging '+(retries+1)+' van '+(limit+1)+'…');
  setTimeout(()=>{if(generation===playerGeneration&&$('player').open)openPlayer({...request,position:resume},true);},600);
}
async function closePlayer() {
  cancelNext(true);nextRequestFullscreen=false;exitPlayerFullscreen();
  const token=player?.token || stored(sessionStorage,'iptv-mobile-session'); if(player&&!video.ended&&['movie','episode','recording'].includes(player.kind)){store(localStorage,'iptv-position-'+playbackKey(request),JSON.stringify({position:position(),duration:player.duration}));saveVodProgress();} ++playerGeneration;clearVideo();player=null;request=null;
  if($('player').open)$('player').close();
  if(token)try{await playbackMutation('/api/mobile/stop',{token});if(stored(sessionStorage,'iptv-mobile-session')===token)store(sessionStorage,'iptv-mobile-session',null);}catch(_){}
  refreshHomeContinue();
}
function setPlayerExpanded(value) {
  playerExpanded=value;
  $('player').classList.toggle('player-expanded',value);
  $('player-surface').classList.toggle('expanded',value);
  $('fullscreen').textContent=value?'Kleiner scherm':'Volledig scherm';
  $('fullscreen').setAttribute('aria-pressed',String(value));
}
function exitPlayerFullscreen() {
  if(nativeVideoFullscreen&&video.webkitExitFullscreen){try{video.webkitExitFullscreen();}catch(_){}}
  nativeVideoFullscreen=false;
  setPlayerExpanded(false);
  if(document.fullscreenElement===$('player-surface')&&document.exitFullscreen)document.exitFullscreen().catch(()=>{});
}
async function togglePlayerFullscreen() {
  if(nativeVideoFullscreen||playerExpanded){exitPlayerFullscreen();return;}
  if(player?.native_vod&&video.webkitEnterFullscreen){try{video.webkitEnterFullscreen();return;}catch(_){}}
  if(playerExpanded){exitPlayerFullscreen();return;}
  setPlayerExpanded(true);
  // Fullscreen the complete player, so server-side seek controls stay inside.
  // iPhone browsers without element fullscreen retain the same viewport view.
  if($('player-surface').requestFullscreen){
    try{await $('player-surface').requestFullscreen();}catch(_){}
  }
}
video.addEventListener('webkitbeginfullscreen',()=>{nativeVideoFullscreen=true;$('fullscreen').textContent='Kleiner scherm';$('fullscreen').setAttribute('aria-pressed','true');});
video.addEventListener('webkitendfullscreen',()=>{nativeVideoFullscreen=false;if(nativeCountdownExit)nativeCountdownExit=false;else if(nextTimer)cancelNext();setPlayerExpanded(false);});
document.addEventListener('fullscreenchange',()=>{
  const active=document.fullscreenElement===$('player-surface');
  if(active&&(!playerExpanded||!$('player').open)){
    if(document.exitFullscreen)document.exitFullscreen().catch(()=>{});return;
  }
  if(active)setPlayerExpanded(true);else if(nativePlayerFullscreen)setPlayerExpanded(false);
  nativePlayerFullscreen=active;
});
function position() {
  if(!player)return 0;if(player.native_vod)return video.readyState<1?player.initial_position||0:video.currentTime;if(origin===null&&video.seekable.length)origin=video.seekable.start(0);
  return player.position+Math.max(0,video.currentTime-(origin??0));
}
function updateTimeline() {
  if(player&&['recording','movie','episode'].includes(player.kind)&&Date.now()-lastSaved>5000&&!video.ended&&!video.paused&&video.currentTime>0){
    lastSaved=Date.now();store(localStorage,'iptv-position-'+playbackKey(request),JSON.stringify({position:position(),duration:player.duration}));saveVodProgress();
  }
  $('pause').textContent=video.paused?'▶':'Ⅱ';$('pause').setAttribute('aria-label',video.paused?'Afspelen':'Pauzeren');
  if(!player||preview!==null)return;
  if(player.kind==='live'){
    const start=video.seekable.length?video.seekable.start(0):0,end=video.seekable.length?video.seekable.end(video.seekable.length-1):0;
    $('timeline').min=start;$('timeline').max=Math.max(start,end);$('timeline').value=video.currentTime;
    $('elapsed').textContent=end-video.currentTime>3?'−'+time(end-video.currentTime):'Live';$('total').textContent='Live';
  }else{$('timeline').disabled=!player.duration;$('timeline').min=0;$('timeline').max=player.duration;$('timeline').value=position();$('elapsed').textContent=time(position());$('total').textContent=player.duration?time(player.duration):'Duur onbekend';}
}
function seek(target) {
  if(!player||!Number.isFinite(target))return;
  cancelNext(true);nextRequestFullscreen=false;
  if(player.native_vod){video.currentTime=Math.max(0,Math.min(target,player.duration-.1));preview=null;updateTimeline();}
  else if(player.kind==='live'){if(video.seekable.length)video.currentTime=Math.max(video.seekable.start(0),Math.min(target,video.seekable.end(video.seekable.length-1)-.3));preview=null;updateTimeline();}
  else openPlayer({...request,position:Math.max(0,Math.min(target,player.duration?player.duration-1:7*86400))});
}
function setupSeek(id,direction) {
  let started=0,timer;
  const button=$(id);
  const step=amount=>{if(!player)return;preview=(preview??(player.kind==='live'?video.currentTime:position()))+direction*amount;preview=Math.max(0,preview);$('elapsed').textContent=time(preview);$('timeline').value=preview;};
  const finish=()=>{clearTimeout(timer);clearInterval(timer);if(!started)return;started=0;if(preview!==null){const target=preview;preview=null;seek(target);}};
  button.onpointerdown=e=>{if(!player)return;e.preventDefault();button.setPointerCapture(e.pointerId);started=Date.now();step(15);timer=setTimeout(()=>{timer=setInterval(()=>step(Date.now()-started>=2000?30:15),500);},500);};
  button.onpointerup=finish;button.onpointercancel=finish;button.onlostpointercapture=finish;
  button.onclick=e=>{if(e.detail===0){step(15);finishKeyboard();}function finishKeyboard(){if(preview!==null){const target=preview;preview=null;seek(target);}}};
  button.oncontextmenu=e=>e.preventDefault();
}
document.addEventListener('click',event=>{const nav=event.target.closest('[data-go]');if(nav){if(location.hash==='#'+nav.dataset.go)showView();else location.hash=nav.dataset.go;}});
$('channel-search').oninput=renderChannels;
for(const id of ['channel-search','vod-search']) {
  $(id).setAttribute('enterkeyhint','search');
  $(id).addEventListener('keydown',event=>{
    if(event.key!=='Enter'||event.isComposing)return;
    event.preventDefault();$(id).blur();
    if(id==='vod-search'){clearTimeout(vodSearchTimer);loadVod();}else renderChannels();
  });
}
$('filters').onclick=e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderChannels();};
$('recording-filters').onclick=e=>{const b=e.target.closest('[data-recording-filter]');if(!b)return;recordingFilter=b.dataset.recordingFilter;document.querySelectorAll('[data-recording-filter]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderRecordings();};
$('guide-days').onclick=e=>{const b=e.target.closest('[data-day]');if(!b)return;guideDay=Number(b.dataset.day);document.querySelectorAll('[data-day]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderGuide();};
$('guide-channel').onchange=loadGuide;$('guide-refresh').onclick=loadGuide;
$('close-programme').onclick=()=>$('programme').close();$('about').onclick=()=>$('about-dialog').showModal();$('close-about').onclick=()=>$('about-dialog').close();
$('close-player').onclick=closePlayer;$('player').oncancel=e=>{e.preventDefault();if(playerExpanded)exitPlayerFullscreen();else closePlayer();};$('retry-player').onclick=()=>request&&openPlayer(request);
$('pause').onclick=()=>{if(video.paused)video.play().catch(e=>toast('Tik op de afspeelknop in het beeld.'));else video.pause();};
$('go-live').onclick=()=>{if(video.seekable.length)video.currentTime=Math.max(0,video.seekable.end(video.seekable.length-1)-.5);};
$('fullscreen').onclick=togglePlayerFullscreen;
$('mute').onclick=()=>{video.muted=!video.muted;};
video.addEventListener('volumechange',()=>{$('mute').textContent=video.muted?'Geluid aan':'Geluid uit';$('mute').setAttribute('aria-pressed',String(video.muted));});
$('timeline').oninput=()=>{preview=Number($('timeline').value);$('elapsed').textContent=time(preview);};
$('timeline').onchange=()=>{const target=Number($('timeline').value);preview=null;seek(target);};
for(const event of ['timeupdate','durationchange','loadedmetadata','seeked'])video.addEventListener(event,updateTimeline);
video.addEventListener('ended',()=>{if(player&&['recording','movie','episode'].includes(player.kind)){store(localStorage,'iptv-position-'+playbackKey(request),null);saveVodProgress(true);scheduleNext();}});
video.addEventListener('playing',()=>{if(player?.kind==='live'){store(localStorage,'iptv-last-live-'+homeContext.signature,player.id||request.id);heroPreviewKey='';renderHome();}$('player-loading').hidden=true;setPlayerStage('playing');updateTimeline();});
video.addEventListener('waiting',()=>{if(player&&video.readyState<3)setLoading('Even laden…','buffering');});
video.addEventListener('error',()=>{if(player&&!attaching&&$('player').open)recoverPlayer('De videostream kon niet worden geladen.');});
for(const event of ['pause','play'])video.addEventListener(event,()=>{
  if(event==='play'&&nextCandidate)cancelNext(true);
  updateTimeline();if(!player||attaching||!player.ready)return;const paused=video.paused;if(lastPause===paused)return;lastPause=paused;
  if(hls&&player.kind!=='live'){if(paused)hls.stopLoad();else hls.startLoad();}
  api('/api/mobile/pause',{token:player.token,paused}).catch(()=>{});
});
setupSeek('rewind',-1);setupSeek('forward',1);
window.addEventListener('hashchange',showView);
window.addEventListener('pagehide',()=>{closePlayer();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){load();if(player)checkPlayer(playerGeneration);}});
$('greeting').textContent=new Date().getHours()<12?'Goedemorgen.':new Date().getHours()<18?'Goedemiddag.':'Goedenavond.';
showView();load().then(()=>{if(location.hash==='#guide')loadGuide();});setInterval(()=>{if(!document.hidden)load();},30000);

function rotateMobileCopy(){document.querySelectorAll('[data-copy]').forEach(e=>e.textContent=window.pickMobileCopy(e.dataset.copy));if(['#films','#series'].includes(location.hash)){$('vod-heading').textContent=window.pickMobileCopy(vodKind==='series'?'seriesHead':'filmsHead');$('vod-subtitle').textContent=window.pickMobileCopy(vodKind==='series'?'seriesSub':'filmsSub');}}
rotateMobileCopy();setInterval(()=>{if(!document.hidden)rotateMobileCopy();},120000);

setInterval(()=>{if(document.hidden||vodLoading||!['#films','#series'].includes(location.hash))return;const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Amsterdam',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(p=>[p.type,p.value]));if(featuredDay&&featuredDay!==parts.year+'-'+parts.month+'-'+parts.day)loadVod(false,true);},60000);
