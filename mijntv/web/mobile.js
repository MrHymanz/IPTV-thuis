'use strict';
const $ = id => document.getElementById(id);
const video = $('video');
let channels = [], recordings = [], filter = 'Alles', recordingFilter = 'saved', guide = [], guideDay = 0, guideGeneration = 0;
let player = null, hls = null, request = null, playerGeneration = 0, playerTimer, heartbeat, retries = 0, restarting = false, attaching = false;
let origin = null, preview = null, toastTimer, lastPause = null, lastSaved = 0;
function stored(storage,key) {try{return storage.getItem(key);}catch(_){return null;}}
function store(storage,key,value) {try{if(value===null)storage.removeItem(key);else storage.setItem(key,value);}catch(_){}}
function resume(id) {try{return JSON.parse(stored(localStorage,'iptv-position-'+id)||'null');}catch(_){return null;}}
const clock = value => new Date(value * 1000).toLocaleTimeString('nl-NL', {hour:'2-digit', minute:'2-digit'});
const date = value => new Date(value * 1000).toLocaleDateString('nl-NL', {day:'numeric', month:'short'});
const time = value => { const s = Math.max(0, Math.floor(value || 0)); return (s >= 3600 ? Math.floor(s / 3600) + ':' : '') + (s >= 3600 ? String(Math.floor(s % 3600 / 60)).padStart(2,'0') : Math.floor(s / 60)) + ':' + String(s % 60).padStart(2,'0'); };
function element(tag, className, text) { const e = document.createElement(tag); if (className) e.className = className; if (text !== undefined) e.textContent = text; return e; }
function action(text, callback, className='text-button') { const e = element('button',className,text); e.type='button'; e.onclick=callback; return e; }
async function api(path, body, keepalive=false) {
  const response = await fetch(path, body === undefined ? {credentials:'same-origin',cache:'no-store'} : {method:'POST',credentials:'same-origin',keepalive,headers:{'Content-Type':'application/json','X-MijnTV':'1'},body:JSON.stringify(body)});
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
  main.append(element('span','recording-art',playable?'▶':'◷'),copy); row.append(main);
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
  const featured=channels.find(c=>c.available&&c.epg?.now)||channels.find(c=>c.available);
  $('home-channels').replaceChildren(...(channels.length?channels.slice(0,4).map(channelRow):[element('p','empty','Voeg eerst favorieten toe in het beheer van je mediacenter.')]));
  $('featured').disabled=!featured;
  if(featured) {
    const p=featured.epg?.now; $('hero-channel').textContent=featured.label; $('hero-title').textContent=p?.title || 'Zet je favoriete zender aan.';
    $('hero-time').textContent=p ? clock(p.start)+' – '+clock(p.end)+' · nog '+Math.max(1,Math.ceil((p.end-Date.now()/1000)/60))+' min' : 'Je programma speelt direct op je telefoon.';
    $('hero-progress').style.width=progress(p)+'%'; $('hero-logo').replaceChildren(logo(featured)); $('featured').onclick=()=>openPlayer({kind:'live',id:featured.id});
  }
}
async function load() {
  try {
    const [favorites,items,storage,status]=await Promise.all([api('/api/favorites'),api('/api/recordings'),api('/api/storage'),api('/api/status')]);
    channels=favorites; recordings=items; $('connection-message').hidden=true;document.querySelector('.connection-card').classList.remove('offline');document.querySelector('.connection-card strong').textContent='Verbonden met je mediacenter';
    const selected=$('guide-channel').value;
    $('guide-channel').replaceChildren(...channels.map(c=>new Option(c.label,c.id)));
    if(channels.some(c=>c.id===selected)) $('guide-channel').value=selected;
    $('version').textContent='Versie '+(status.revision||'local').slice(0,8);
    $('storage-text').textContent=storage.available?(storage.free/1e9).toFixed(1)+' GB vrij van '+(storage.total/1e9).toFixed(0)+' GB':storage.error||'Opslag niet beschikbaar';
    $('storage-progress').style.width=storage.available?Math.max(0,100*(storage.total-storage.free)/storage.total)+'%':'0%';
    renderHome(); renderChannels(); renderRecordings();
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
let vodFilter='all', vodSelected=null, vodSearchTimer, vodLoading=false, vodRequiresCategory=false;
const vodState={movie:{category:'',query:''},series:{category:'',query:''}};
function vodLocal(name) {try {const value=JSON.parse(stored(localStorage,'iptv-vod-'+name+'-'+vodSignature)||'[]');return Array.isArray(value)?value:[];}catch(_){return [];}}
function vodWrite(name,value) {store(localStorage,'iptv-vod-'+name+'-'+vodSignature,JSON.stringify(value.slice(0,300)));}
function vodKey(item) {return item.kind+':'+item.id;}
function playbackKey(data) {return ['movie','episode'].includes(data.kind)?data.signature+'-'+data.kind+'-'+data.id:data.id;}
function vodPoster(item) {
  const wrap=element('span','vod-poster');wrap.append(element('span','',item.title));
  if(item.cover){const img=element('img');img.src=item.cover;img.alt='';img.loading='lazy';img.onerror=()=>img.remove();wrap.append(img);}
  return wrap;
}
function vodCard(item) {
  const row=action('',()=>openVodDetail(item),'vod-card');const poster=vodPoster(item);
  if(item.rating)poster.append(element('b','vod-badge','★ '+item.rating));
  row.append(poster,element('strong','',item.title),element('span','small-muted',[item.year,item.kind==='series'?'Serie':'Film'].filter(Boolean).join(' · ')));
  if(item.position>10){const track=element('span','vod-progress'),fill=element('span');fill.style.width=(item.duration>0?Math.min(100,item.position/item.duration*100):0)+'%';track.append(fill);row.append(track);}
  return row;
}
function renderVod() {
  const local=vodFilter==='saved'?vodLocal('saved'):vodLocal('history');
  const q=vodState[vodKind].query.toLocaleLowerCase('nl-NL').trim();
  const shown=vodFilter==='all'?vodItems:local.filter(i=>i.kind===vodKind&&(!vodState[vodKind].category||i.category===vodState[vodKind].category)&&i.title.toLocaleLowerCase('nl-NL').includes(q));
  $('vod-list').replaceChildren(...shown.map(vodCard));
  if(!shown.length)$('vod-list').append(element('p','empty',vodFilter==='saved'?'Je kijklijst is nog leeg voor deze selectie. Bewaar een titel via de details.':vodFilter==='continue'?'Nog niets om verder te kijken voor deze selectie.':vodRequiresCategory?'Kies hierboven een bron of categorie. We halen het aanbod per categorie op.':'Geen titels gevonden. Kies een andere categorie of zoekterm.'));
  $('vod-count').textContent=vodFilter==='all'&&vodRequiresCategory?'':(vodFilter==='all'?vodTotal:shown.length)+' titels';
  $('vod-list-heading').textContent=vodFilter==='saved'?'Mijn kijklijst':vodFilter==='continue'?'Verder kijken':vodKind==='movie'?'Ontdek iets moois':'Jouw volgende serie';
  $('vod-more').hidden=vodFilter!=='all'||vodItems.length>=vodTotal;
  $('vod-featured').hidden=vodFilter!=='all'||!shown.length||!!q||!!vodState[vodKind].category;
  if(shown.length){const item=shown[0];$('vod-featured-art').replaceChildren(vodPoster(item));$('vod-featured-title').textContent=item.title;$('vod-featured-meta').textContent=[item.year,item.rating?'★ '+item.rating:''].filter(Boolean).join(' · ');$('vod-featured-tag').textContent=vodKind==='movie'?'UITGELICHT · FILM':'UITGELICHT · SERIE';$('vod-featured').onclick=()=>openVodDetail(item);}
  for(const [id,filter] of [['vod-all','all'],['vod-saved','saved'],['vod-continue','continue']]){const active=filter===vodFilter;$(id).classList.toggle('active',active);$(id).setAttribute('aria-pressed',String(active));}
}
async function loadVod(append=false) {
  const generation=++vodGeneration,kind=vodKind,state={...vodState[kind]};
  vodLoading=true;$('vod-more').disabled=true;$('vod-retry').hidden=true;$('vod-notice').textContent='';
  if(!append){vodItems=[];$('vod-featured').hidden=true;$('vod-more').hidden=true;$('vod-count').textContent='';$('vod-list').replaceChildren(element('p','empty','Het aanbod van je provider wordt opgehaald…'));}
  const params=new URLSearchParams({kind,category:state.category,q:state.query,offset:String(append?vodItems.length:0)});
  try {
    const result=await api('/api/mobile/vod?'+params);if(generation!==vodGeneration)return;
    if(append&&vodSignature&&vodSignature!==result.signature){vodItems=[];vodTotal=0;throw Error('Je provideraccount is gewijzigd. Open het aanbod opnieuw.');}
    vodRequiresCategory=!!result.requires_category;vodSignature=result.signature;vodItems=append?vodItems.concat(result.items):result.items;vodTotal=result.total;
    const categories=[new Option(result.requires_category?'Kies een categorie':'Alle categorieën',''),...result.categories.map(c=>new Option(c.name,c.id))];
    if(state.category&&!result.categories.some(c=>c.id===state.category))categories.push(new Option('Eerdere categorie',state.category));
    $('vod-category').replaceChildren(...categories);$('vod-category').value=state.category;$('vod-notice').textContent=result.notice||'';renderVod();
  }catch(error){if(generation!==vodGeneration)return;$('vod-notice').textContent=error.message;$('vod-retry').hidden=false;if(!append)$('vod-list').replaceChildren(element('p','empty',error.message));}
  finally{if(generation===vodGeneration){vodLoading=false;$('vod-more').disabled=false;}}
}
function enterVod(kind) {
  vodKind=kind;const series=kind==='series';$('vod-heading').textContent=series?'Nog één aflevering.':'Een avond voor jezelf.';$('vod-subtitle').textContent=series?'Een nieuw verhaal om in te verdwijnen.':'Grote verhalen. Gewoon bij jou thuis.';
  $('vod-search').placeholder=series?'Zoek een serie':'Zoek een film';$('vod-search').setAttribute('aria-label',$('vod-search').placeholder);$('vod-search').value=vodState[kind].query;
  $('vod-category').replaceChildren(new Option('Alle categorieën',''));vodFilter='all';loadVod();
}
function saveVod(item) {
  const list=vodLocal('saved'),key=vodKey(item),found=list.some(x=>vodKey(x)===key);
  vodWrite('saved',found?list.filter(x=>vodKey(x)!==key):[item,...list]);toast(found?'Verwijderd uit je kijklijst.':'Toegevoegd aan je kijklijst.');renderVodActions();if(vodFilter==='saved')renderVod();
}
function vodLibrary(item) {return {id:item.id,kind:item.kind,title:item.title,cover:item.cover,year:item.year,rating:item.rating,category:item.category||''};}
function playVod(item,episode=null,fromStart=false) {
  const kind=episode?'episode':'movie', id=episode?episode.id:item.id;
  const data={kind,id,signature:item.signature,library:vodLibrary(item),...(episode?{episode:{id:episode.id,title:episode.title,season:episode.season,number:episode.number}}:{})};
  const saved=resume(playbackKey(data));data.position=fromStart?0:saved?.position||0;
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
    copy.append(element('small','',[ep.duration?Math.round(ep.duration/60)+' min':'',saved?.position>10?'Verder vanaf '+time(saved.position):''].filter(Boolean).join(' · ')));
    if(ep.description)copy.append(element('small','',ep.description.slice(0,180)));row.append(element('span','',String(ep.number).padStart(2,'0')),copy,element('span','','▷'));return row;}));
  if(!episodes.length)$('vod-episodes').append(element('p','empty','Geen afleveringen beschikbaar voor dit seizoen.'));
}
async function openVodDetail(item) {
  const generation=++vodDetailGeneration;vodSelected=null;$('vod-detail-title').textContent=item.title;$('vod-detail-kind').textContent=item.kind==='series'?'SERIE':'FILM';$('vod-detail-cover').replaceChildren(vodPoster(item));
  for(const id of ['vod-detail-meta','vod-detail-cast','vod-detail-description'])$(id).textContent='';$('vod-detail-notice').textContent='Details ophalen…';$('vod-detail-actions').replaceChildren();$('vod-seasons').hidden=true;
  if(!$('vod-detail').open)$('vod-detail').showModal();
  try {
    const detail=await api('/api/mobile/vod/detail?'+new URLSearchParams({kind:item.kind,id:item.id}));if(generation!==vodDetailGeneration||!$('vod-detail').open)return;
    if(detail.signature!==vodSignature)throw Error('Het provideraccount is gewijzigd. Open het aanbod opnieuw.');
    vodSelected={...detail,category:item.category||''};$('vod-detail-title').textContent=detail.title;$('vod-detail-cover').replaceChildren(vodPoster(detail));
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
$('vod-more').onclick=()=>loadVod(true);$('vod-retry').onclick=()=>loadVod();$('vod-season').onchange=renderVodEpisodes;
$('vod-close').onclick=()=>{++vodDetailGeneration;$('vod-detail').close();};$('vod-detail').addEventListener('cancel',()=>{++vodDetailGeneration;});

function showView() {
  const requested=location.hash.slice(1)||'home', view=['home','channels','recordings','guide','films','series'].includes(requested)?requested:'home';
  const vod=['films','series'].includes(view);clearTimeout(vodSearchTimer);if(!vod)++vodGeneration;
  document.querySelectorAll('.view').forEach(e=>e.hidden=e.id!==(vod?'vod':view)+'-view');
  document.querySelectorAll('.nav-button').forEach(e=>{const active=e.dataset.go===(vod?'home':view);e.classList.toggle('active',active);if(active)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current');});
  document.querySelectorAll('.media-tabs button').forEach(e=>{const active=e.dataset.go===(vod?view:'home');e.classList.toggle('active',active);if(active)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current');});
  window.scrollTo(0,0); if(view==='guide'&&channels.length)loadGuide();if(vod)enterVod(view==='films'?'movie':'series');
}
function clearVideo() {
  attaching=true; clearTimeout(playerTimer); clearInterval(heartbeat); if(hls){hls.destroy();hls=null;}
  video.pause(); video.removeAttribute('src'); video.load(); origin=null;preview=null;lastPause=null;attaching=false;
}
function setLoading(message) { $('player-loading').hidden=false; $('player-status').textContent=message; $('retry-player').hidden=true; }
function failPlayer(message) { for(const id of ['timeline','rewind','forward'])$(id).disabled=true;if($('player-title').textContent==='Verbinden…')$('player-title').textContent='Even niet beschikbaar';$('player-loading').hidden=true;$('player-message').textContent=message;$('retry-player').hidden=false;$('pause').disabled=true; }
async function openPlayer(data, automatic=false) {
  if(!automatic) retries=0;
  if(['recording','movie','episode'].includes(data.kind)&&data.position===undefined)data={...data,position:resume(playbackKey(data))?.position||0};
  const generation=++playerGeneration, previous=player?.token || stored(sessionStorage,'iptv-mobile-session');
  request={...data}; restarting=false;clearVideo();player=null;
  for(const id of ['timeline','rewind','forward','pause'])$(id).disabled=true;
  $('timeline').value=0;$('elapsed').textContent='0:00';$('total').textContent=data.kind==='live'?'Live':'…';
  setLoading(data.kind==='recording'?'Je opname openen…':'Een vrij kijkaccount zoeken…');
  $('player-message').textContent='De tv en opnames houden voorrang.';$('player-title').textContent='Verbinden…';$('player-channel').textContent='';
  if(!$('player').open)$('player').showModal();
  try {
    const result=await api('/api/mobile/play',{...data,...(previous?{session:previous}:{}),...(automatic?{retry:true}:{})});
    if(generation!==playerGeneration){api('/api/mobile/stop',{token:result.player.token}).catch(()=>{});return;}
    player=result.player; store(sessionStorage,'iptv-mobile-session',player.token);
    $('player-title').textContent=player.title;$('player-channel').textContent=player.channel;$('player-kind').textContent=player.kind==='live'?'LIVE':player.kind==='archive'?'TERUGKIJKEN':player.kind==='movie'?'FILM':player.kind==='episode'?'SERIE':'OPNAME';
    $('go-live').hidden=player.kind!=='live';$('player-message').textContent=player.kind==='live'?'Je kijkt live. Spoelen kan binnen het gebufferde deel.':player.kind==='recording'?'Je opname speelt vanaf het gekozen moment.':['movie','episode'].includes(player.kind)?'Je kijkt op jouw moment. De tv en opnames houden voorrang.':'Je kijkt de gekozen uitzending terug.';
    setLoading('Je programma wordt klaargezet…');const started=Date.now();
    const poll=async()=>{
      if(generation!==playerGeneration||!player)return;
      try{
        const state=await api('/api/mobile/status?token='+encodeURIComponent(player.token));if(generation!==playerGeneration)return;
        if(['preempted','error','stopped'].includes(state.state)){restartPlayer(state.message);return;}
        player={...player,...state};
        if(state.ready){if(attachVideo(generation)!==false)heartbeat=setInterval(()=>checkPlayer(generation),10000);return;}
        if(Date.now()-started>30000){restartPlayer('Deze bron reageert niet.');return;}
        playerTimer=setTimeout(poll,1000);
      }catch(error){if(generation===playerGeneration)failPlayer(error.message);}
    };poll();
  }catch(error){
    if(generation!==playerGeneration)return;
    if(previous)api('/api/mobile/stop',{token:previous}).catch(()=>{});
    store(sessionStorage,'iptv-mobile-session',null);failPlayer(error.message);
  }
}
async function checkPlayer(generation) {
  if(!player||generation!==playerGeneration)return;
  try {const state=await api('/api/mobile/status?token='+encodeURIComponent(player.token));if(generation!==playerGeneration)return;if(['preempted','error','stopped'].includes(state.state))restartPlayer(state.message);}
  catch(error){if(generation===playerGeneration)failPlayer(error.message);}
}
function attachVideo(generation) {
  if(!player||generation!==playerGeneration)return;
  for(const id of ['timeline','rewind','forward','pause'])$(id).disabled=false;attaching=true;
  const nativeHls=video.canPlayType('application/vnd.apple.mpegurl');
  if(nativeHls&&/Apple/.test(navigator.vendor)){video.src=player.playlist;video.load();tryPlay();}
  else if(window.Hls?.isSupported()){
    hls=new Hls({enableWorker:false,backBufferLength:90,maxBufferLength:25});
    hls.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal&&generation===playerGeneration)restartPlayer('De videostream is onderbroken.');});
    hls.on(Hls.Events.MANIFEST_PARSED,tryPlay);hls.loadSource(player.playlist);hls.attachMedia(video);
  }else if(nativeHls){video.src=player.playlist;video.load();tryPlay();}
  else{attaching=false;failPlayer('Deze browser ondersteunt deze videospeler niet. Gebruik Safari op je iPhone of een actuele Chrome-browser.');api('/api/mobile/stop',{token:player.token}).catch(()=>{});return false;}
  function tryPlay(){attaching=false;video.play().catch(()=>{if(generation===playerGeneration){$('player-loading').hidden=true;$('player-message').textContent='Tik op afspelen om te beginnen.';}});}
}
function restartPlayer(message) {
  if(restarting||!request||!$('player').open)return;restarting=true;
  if(retries>=2){failPlayer(message||'Er is op dit moment geen vrij werkend kijkaccount.');clearInterval(heartbeat);return;}
  retries++;const generation=playerGeneration;const resume=player?.kind==='live'?0:position();setLoading(message||'Ander vrij account proberen…');
  setTimeout(()=>{if(generation===playerGeneration&&$('player').open)openPlayer({...request,position:resume},true);},600);
}
async function closePlayer() {
  const token=player?.token || stored(sessionStorage,'iptv-mobile-session'); if(player&&!video.ended&&['movie','episode'].includes(player.kind)){store(localStorage,'iptv-position-'+playbackKey(request),JSON.stringify({position:position(),duration:player.duration}));saveVodProgress();} ++playerGeneration;clearVideo();player=null;request=null;
  store(sessionStorage,'iptv-mobile-session',null);if($('player').open)$('player').close();
  if(token)try{await api('/api/mobile/stop',{token});}catch(_){}
}
function position() {
  if(!player)return 0;if(origin===null&&video.seekable.length)origin=video.seekable.start(0);
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
  if(player.kind==='live'){if(video.seekable.length)video.currentTime=Math.max(video.seekable.start(0),Math.min(target,video.seekable.end(video.seekable.length-1)-.3));preview=null;updateTimeline();}
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
$('filters').onclick=e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderChannels();};
$('recording-filters').onclick=e=>{const b=e.target.closest('[data-recording-filter]');if(!b)return;recordingFilter=b.dataset.recordingFilter;document.querySelectorAll('[data-recording-filter]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderRecordings();};
$('guide-days').onclick=e=>{const b=e.target.closest('[data-day]');if(!b)return;guideDay=Number(b.dataset.day);document.querySelectorAll('[data-day]').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderGuide();};
$('guide-channel').onchange=loadGuide;$('guide-refresh').onclick=loadGuide;
$('close-programme').onclick=()=>$('programme').close();$('about').onclick=()=>$('about-dialog').showModal();$('close-about').onclick=()=>$('about-dialog').close();
$('close-player').onclick=closePlayer;$('player').oncancel=e=>{e.preventDefault();closePlayer();};$('retry-player').onclick=()=>request&&openPlayer(request);
$('pause').onclick=()=>{if(video.paused)video.play().catch(e=>toast('Tik op de afspeelknop in het beeld.'));else video.pause();};
$('go-live').onclick=()=>{if(video.seekable.length)video.currentTime=Math.max(0,video.seekable.end(video.seekable.length-1)-.5);};
$('fullscreen').onclick=()=>{if(video.webkitEnterFullscreen)video.webkitEnterFullscreen();else if(video.requestFullscreen)video.requestFullscreen().catch(()=>toast('Volledig scherm is nu niet beschikbaar.'));};
$('timeline').oninput=()=>{preview=Number($('timeline').value);$('elapsed').textContent=time(preview);};
$('timeline').onchange=()=>{const target=Number($('timeline').value);preview=null;seek(target);};
for(const event of ['timeupdate','durationchange','loadedmetadata','seeked'])video.addEventListener(event,updateTimeline);
video.addEventListener('ended',()=>{if(player&&['recording','movie','episode'].includes(player.kind)){store(localStorage,'iptv-position-'+playbackKey(request),null);saveVodProgress(true);}});
video.addEventListener('playing',()=>{$('player-loading').hidden=true;updateTimeline();});
video.addEventListener('waiting',()=>{if(player&&video.readyState<3)setLoading('Even laden…');});
video.addEventListener('error',()=>{if(player&&!attaching&&$('player').open)restartPlayer('De videostream kon niet worden geladen.');});
for(const event of ['pause','play'])video.addEventListener(event,()=>{
  updateTimeline();if(!player||attaching||!player.ready)return;const paused=video.paused;if(lastPause===paused)return;lastPause=paused;
  if(hls&&player.kind!=='live'){if(paused)hls.stopLoad();else hls.startLoad();}
  api('/api/mobile/pause',{token:player.token,paused}).catch(()=>{});
});
setupSeek('rewind',-1);setupSeek('forward',1);
window.addEventListener('hashchange',showView);
window.addEventListener('pagehide',()=>{const token=player?.token;if(token)api('/api/mobile/stop',{token},true).catch(()=>{});});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){load();if(player)checkPlayer(playerGeneration);}});
$('greeting').textContent=new Date().getHours()<12?'Goedemorgen.':new Date().getHours()<18?'Goedemiddag.':'Goedenavond.';
showView();load().then(()=>{if(location.hash==='#guide')loadGuide();});setInterval(()=>{if(!document.hidden)load();},30000);
