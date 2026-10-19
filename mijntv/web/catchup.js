'use strict';
const $ = id => document.getElementById(id);
const sources = CATCHUP_DATA.sources;
const cleanName = name => name.replace(/^NL\|\s*/, '');
const channelName = name => cleanName(name).replace(/\s+(?:FHD|HD)(?:\s+²)?$/, '');
const dayFormat = new Intl.DateTimeFormat('nl-NL',{weekday:'long',day:'numeric',month:'long',timeZone:'UTC'});
const shortFormat = new Intl.DateTimeFormat('nl-NL',{day:'numeric',month:'short',timeZone:'UTC'});
const calendarParts = new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Europe/Amsterdam'}).format(new Date());
const calendar = offset => { const day = new Date(`${calendarParts}T12:00:00Z`); day.setUTCDate(day.getUTCDate()+offset); return day; };
const planned = new Map();
const contextChannel = new URLSearchParams(location.search).get('channel') || 'NPO 1';
let sourceIndex = Math.max(0,sources.findIndex(source => channelName(source.channel).toLowerCase()===contextChannel.toLowerCase()));
let dayOffset = 0;
let programmeIndex = 0;
let currentProgrammes = [];
let selectedProgramme;
let playerProgramme;
let playerOpener;
let toastTimer;
function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; },4000);
}
function setMenu(open) {
  document.body.classList.toggle('menu-closed',!open);
  $('toggle-menu').setAttribute('aria-expanded',String(open));
}
setMenu(window.matchMedia('(min-width: 851px)').matches);
$('toggle-menu').addEventListener('click', () => setMenu(document.body.classList.contains('menu-closed')));
$('close-menu').addEventListener('click', () => { setMenu(false); focusProgramme(); });
$('guide-home').addEventListener('click', () => { jumpNow(); if (window.innerWidth<=850) setMenu(false); });
function fixtures(source) {
  const category = source.category;
  const name = channelName(source.channel);
  if (category.includes('SPORT')) return [
    ['Sportjournaal','Het laatste sportnieuws, de uitslagen en de verhalen achter de wedstrijd.','Sport',30],
    ['De voorbeschouwing','Analyses, verwachtingen en de belangrijkste spelers van vandaag.','Sport',60],
    ['Wedstrijd van de dag','De belangrijkste momenten van een sportieve dag.','Sport',120],
    ['De nabeschouwing','Een terugblik op de wedstrijd met analyses en reacties.','Sport',55],
    ['Sportverhalen','Bijzondere verhalen van sporters, op en naast het veld.','Documentaire',45]
  ];
  if (category.includes('FILMS')) return [
    ['Een nieuwe horizon','Een onverwachte ontmoeting zet een bijzonder avontuur in beweging.','Drama',100],
    ['De laatste zomer','Een zomer om nooit te vergeten, vol kleine en grote ontdekkingen.','Film',105],
    ['Onderweg naar morgen','Twee onbekenden vinden elkaar tijdens een onverwachte reis.','Drama',110],
    ['Een avond in de stad','Een verhaal over vriendschap, kansen en opnieuw beginnen.','Film',115],
    ['Voorbij de horizon','Een verrassende reis naar een onbekende bestemming.','Avontuur',105]
  ];
  if (category.includes('KINDEREN')) return [
    ['De kleine ontdekkers','Samen valt er elke dag iets nieuws te ontdekken.','Jeugd',30],
    ['Een groot avontuur','Een middag vol fantasie en nieuwe vriendjes.','Animatie',60],
    ['Op pad met vrienden','Samen op zoek naar de oplossing voor een bijzonder raadsel.','Jeugd',30],
    ['De verhalenclub','De mooiste verhalen om de dag mee af te sluiten.','Jeugd',30],
    ['Nog één verhaaltje','Een rustig verhaaltje voor het slapengaan.','Jeugd',30]
  ];
  if (category.includes('DOCUMENTAIRE') || name.startsWith('NPO 2')) return [
    ['De wereld van dichtbij','Een nieuwe blik op de wereld om ons heen.','Documentaire',45],
    ['Langs onbekende wegen','Een reis langs bijzondere landschappen en de mensen die er wonen.','Reizen',50],
    ['Een leven in het wild','Ontdek het dagelijkse leven in een bijzonder natuurgebied.','Natuur',45],
    ['De mooiste treinreizen','Een reis door de bergen, langs stille dorpen en spectaculaire vergezichten.','Reizen',55],
    ['Verhalen van vroeger','Persoonlijke verhalen brengen een stukje geschiedenis tot leven.','Geschiedenis',45]
  ];
  if (name.startsWith('NPO 1')) return [
    ['Nederland in Beweging','Beweeg mee met oefeningen voor volwassenen, begeleid door de presentatoren.','Bewegen',30],
    ['NOS Journaal','Het belangrijkste nieuws uit binnen- en buitenland.','Nieuws',30],
    ['EenVandaag','Achtergronden en de verhalen achter het nieuws van de dag.','Actualiteiten',30],
    ['NOS Journaal','Het nieuws van de dag. Met achtergronden, reportages en het weer.','Nieuws',30],
    ['Het verhaal van Nederland','Een reis door onze geschiedenis, verteld op de plekken waar het gebeurde.','Geschiedenis',45]
  ];
  return [
    ['Een goed begin','Verhalen en gesprekken om de dag mee te beginnen.','Amusement',30],
    ['Het beste van de dag','De mooiste momenten en bijzondere ontmoetingen.','Entertainment',60],
    ['Samen aan tafel','Een goed gesprek over alles wat ons bezighoudt.','Talkshow',45],
    ['Een avond vol verhalen','Onverwachte ontmoetingen, bijzondere mensen en nieuwe verhalen.','Entertainment',55],
    ['Nog even napraten','Een ontspannen afsluiting van de dag.','Amusement',45]
  ];
}
const minuteTime = minutes => `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`;
function phase(programme,offset=programme.offset ?? dayOffset) {
  if (offset < 0) return 'past';
  if (offset > 0) return 'future';
  const parts = new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:'Europe/Amsterdam'}).format(new Date()).split(':').map(Number);
  const now = parts[0]*60+parts[1];
  return now < programme.start ? 'future' : now >= programme.start+programme.minutes ? 'past' : 'current';
}
function available(programme) { return phase(programme) === 'past'; }
function planKey(programme,offset=programme.offset ?? dayOffset,index=sourceIndex) {
  return `${channelName(sources[index].channel)}|${calendar(offset).toISOString().slice(0,10)}|${programme.start}`;
}
function rowAction(programme) {
  const scheduled=planned.has(planKey(programme));
  const state=phase(programme);
  return scheduled&&state==='future' ? 'Gepland · OK annuleren' : state==='past' ? 'OK terugkijken' : state==='current' ? 'OK live kijken' : 'OK opname plannen';
}
function render() {
  const source=sources[sourceIndex],starts=[9*60,12*60,18*60,20*60,21*60+30];
  if(source.category.includes('FILMS'))starts.splice(0,starts.length,8*60,10*60,13*60,16*60,20*60);
  currentProgrammes=[];
  for(const offset of [-2,-1,0,1,2]) {
    fixtures(source).forEach((item,slot)=>currentProgrammes.push({title:item[0],description:item[1],genre:item[2],minutes:item[3],start:starts[slot],offset,slot}));
  }
  $('programme-count').textContent='Eerder · nu · straks';
  $('programmes').replaceChildren();
  let previousDay;
  currentProgrammes.forEach((programme,index)=>{
    if(programme.offset!==previousDay) {
      const heading=document.createElement('div');heading.className='timeline-day';heading.textContent=dayFormat.format(calendar(programme.offset));
      $('programmes').append(heading);previousDay=programme.offset;
    }
    const button=document.createElement('button');button.className='programme';button.dataset.programme=String(index);
    const time=document.createElement('span');time.className='programme-time';time.textContent=minuteTime(programme.start);
    const copy=document.createElement('span');copy.className='programme-copy';
    const title=document.createElement('strong');title.textContent=programme.title;
    const meta=document.createElement('span');meta.textContent=`${programme.genre} · ${programme.minutes} min`;copy.append(title,meta);
    const progress=document.createElement('progress');progress.className='programme-progress';progress.max=100;progress.hidden=true;progress.setAttribute('aria-label','Voortgang huidige uitzending');copy.append(progress);
    const replay=document.createElement('span');replay.className='replay-label';
    const icon=document.createElement('span');icon.setAttribute('aria-hidden','true');
    const label=document.createElement('span');label.className='replay-text';replay.append(icon,label);
    button.append(time,copy,replay);
    button.addEventListener('focus',()=>{programmeIndex=index;selectProgramme();});
    button.addEventListener('click',()=>{programmeIndex=index;selectProgramme();primaryAction(button);});
    button.addEventListener('keydown',event=>{
      const delta={ArrowUp:-1,ArrowDown:1,PageUp:-5,PageDown:5}[event.key];
      if(delta===undefined)return;
      event.preventDefault();event.stopPropagation();
      programmeIndex=Math.max(0,Math.min(currentProgrammes.length-1,index+delta));selectProgramme();focusProgramme();
    });
    $('programmes').append(button);
  });
  programmeIndex=Math.min(programmeIndex,currentProgrammes.length-1);
  selectProgramme();
}
function scrollProgramme() {
  const list=$('programmes'),button=document.querySelector(`[data-programme="${programmeIndex}"]`);
  if(!button)return;
  const top=button.offsetTop,bottom=top+button.offsetHeight;
  if(top<list.scrollTop+35)list.scrollTop=Math.max(0,top-45);
  else if(bottom>list.scrollTop+list.clientHeight)list.scrollTop=bottom-list.clientHeight+12;
}
function focusProgramme() {
  document.querySelector(`[data-programme="${programmeIndex}"]`).focus({preventScroll:true});scrollProgramme();
}
function jumpNow() {
  const index=currentProgrammes.findIndex(programme=>phase(programme)!=='past');
  programmeIndex=index<0?currentProgrammes.length-1:index;selectProgramme();focusProgramme();
  const button=document.querySelector(`[data-programme="${programmeIndex}"]`);
  $('programmes').scrollTop=Math.max(0,button.offsetTop-$('programmes').clientHeight*.35);
}
$('jump-now').addEventListener('click',jumpNow);
function selectProgramme() {
  selectedProgramme=currentProgrammes[programmeIndex];dayOffset=selectedProgramme.offset;
  const source=sources[sourceIndex],state=phase(selectedProgramme),isPlanned=planned.has(planKey(selectedProgramme));
  document.querySelectorAll('[data-programme]').forEach(button=>{
    const index=Number(button.dataset.programme),programme=currentProgrammes[index],scheduled=planned.has(planKey(programme)),programmeState=phase(programme);
    button.setAttribute('aria-pressed',String(index===programmeIndex));button.tabIndex=index===programmeIndex?0:-1;
    button.classList.toggle('is-planned',scheduled);button.classList.toggle('is-current',programmeState==='current');
    button.querySelector('.replay-label>span:first-child').textContent=scheduled?'●':programmeState==='past'?'↶':programmeState==='current'?'●':'◷';
    button.querySelector('.replay-text').textContent=rowAction(programme);
    const progress=button.querySelector('.programme-progress');
    progress.hidden=programmeState!=='current';
    if(!progress.hidden){progress.value=liveProgress(programme).percent;progress.setAttribute('aria-valuetext',`${Math.round(progress.value)} procent verstreken`);}
  });
  $('list-date').textContent=dayFormat.format(calendar(dayOffset));
  $('detail-state').textContent=isPlanned?'OPNAME GEPLAND':state==='past'?'TERUGKIJKEN':state==='current'?'NU BEZIG':'BINNENKORT';
  $('detail-date').textContent=shortFormat.format(calendar(dayOffset));
  $('detail-time').textContent=`${minuteTime(selectedProgramme.start)} – ${minuteTime(selectedProgramme.start+selectedProgramme.minutes)}`;
  $('detail-duration').textContent=`${selectedProgramme.minutes} minuten`;
  $('detail-title').textContent=selectedProgramme.title;
  $('detail-description').textContent=selectedProgramme.description||'';
  $('detail-description').hidden=!selectedProgramme.description;
  updateLiveProgress();
  $('detail-expiry').textContent=state==='past'?`Terug te kijken tot ${shortFormat.format(calendar(dayOffset+source.days))}, ${minuteTime(selectedProgramme.start)}`:isPlanned?'Je opname staat gepland en wordt op je tv bewaard.':state==='current'?'Dit programma is nu bezig. Je kunt het resterende deel opnemen.':'Je kunt deze uitzending vooraf inplannen voor opname.';
  $('detail-action').textContent=isPlanned&&state==='future'?'Opname annuleren':state==='past'?'Vanaf begin terugkijken':state==='current'?(isPlanned?'Opname annuleren':'Resterend deel opnemen'):'Opname plannen';
  document.querySelector('.available-until').hidden=state==='current';
  $('record-hint').hidden=true;$('detail-record-action').textContent=isPlanned?'Opname annuleren':'Resterend deel opnemen';
}
function liveProgress(programme) {
  const parts=new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZone:'Europe/Amsterdam'}).format(new Date()).split(':').map(Number);
  const total=Math.max(1,programme.minutes*60);
  const elapsed=Math.max(0,Math.min(total,parts[0]*3600+parts[1]*60+parts[2]-programme.start*60));
  return {elapsed,total,percent:elapsed/total*100,remaining:total-elapsed};
}
function updateLiveProgress() {
  const visible=phase(selectedProgramme)==='current';
  $('live-progress-panel').hidden=!visible;
  if(!visible)return;
  const value=liveProgress(selectedProgramme),percent=Math.floor(value.percent);
  $('live-progress').value=value.percent;
  $('live-progress').setAttribute('aria-valuetext',`${percent} procent verstreken, nog ${Math.ceil(value.remaining/60)} minuten`);
  $('live-percent').textContent=`${percent}%`;
  $('live-start').textContent=minuteTime(selectedProgramme.start);
  $('live-end').textContent=minuteTime(selectedProgramme.start+selectedProgramme.minutes);
  $('live-relative').textContent=`${Math.floor(value.elapsed/60)} min verstreken · nog ${Math.ceil(value.remaining/60)} min`;
}
function toggleRecording() {
  const key=planKey(selectedProgramme);
  if (planned.has(key)) {
    planned.delete(key);
    toast('Voorbeeldopname geannuleerd.');
  } else {
    const start=selectedProgramme.start;
    const conflict=[...planned.values()].find(item => item.date===calendar(dayOffset).toISOString().slice(0,10)&&start<item.start+item.minutes&&start+selectedProgramme.minutes>item.start);
    if (conflict) {toast(`Overlapt met ${conflict.title}. Annuleer die opname eerst via Opnames.`);return;}
    planned.set(key,{...selectedProgramme,key,date:calendar(dayOffset).toISOString().slice(0,10),offset:dayOffset,sourceIndex,channel:cleanName(sources[sourceIndex].channel)});
    toast('Voorbeeldopname ingepland. Je vindt hem onder Opnames.');
  }
  updatePlanning();selectProgramme();
}
function updatePlanning() {
  $('nav-planned-count').textContent=String(planned.size);
  $('nav-planned-count').hidden=planned.size===0;
  $('planned-dialog-count').textContent=String(planned.size);
  $('planned-list').replaceChildren();
  const entries=[...planned.values()].sort((a,b)=>a.date.localeCompare(b.date)||a.start-b.start);
  if (!entries.length) {
    const empty=document.createElement('div');empty.className='planned-empty';
    const heading=document.createElement('h3');heading.textContent='Nog niets gepland.';
    const message=document.createElement('p');message.textContent='Kies Vandaag, Morgen of Overmorgen in de gids. Selecteer een programma dat nog moet beginnen en kies Opname plannen.';
    empty.append(heading,message);$('planned-list').append(empty);
  }
  for (const item of entries) {
    const row=document.createElement('div');row.className='planned-row';
    const icon=document.createElement('span');icon.className='planned-dot';icon.textContent='●';
    const copy=document.createElement('div');const title=document.createElement('strong');title.textContent=item.title;
    const meta=document.createElement('p');meta.textContent=`${item.channel} · ${shortFormat.format(new Date(item.date+'T12:00:00Z'))} · ${minuteTime(item.start)} – ${minuteTime(item.start+item.minutes)}`;
    copy.append(title,meta);
    const cancel=document.createElement('button');cancel.className='cancel-recording';cancel.textContent='Annuleren';cancel.setAttribute('aria-label',`Annuleer voorbeeldopname ${item.title}`);
    cancel.addEventListener('click',()=>{planned.delete(item.key);updatePlanning();selectProgramme();toast('Voorbeeldopname geannuleerd.');$('close-recordings').focus();});
    row.append(icon,copy,cancel);$('planned-list').append(row);
  }
}
function recordingView(view) {
  $('planned-list').hidden=view!=='planned';$('saved-list').hidden=view!=='saved';
  for(const name of ['planned','saved']){const active=name===view;$(`${name}-view-tab`).classList.toggle('active',active);$(`${name}-view-tab`).setAttribute('aria-pressed',String(active));}
}
function openRecordings() {recordingView('planned');updatePlanning();$('recordings-dialog').showModal();}
$('recordings-open').addEventListener('click',openRecordings);
$('close-recordings').addEventListener('click',()=>$('recordings-dialog').close());
$('planned-view-tab').addEventListener('click',()=>recordingView('planned'));
$('saved-view-tab').addEventListener('click',()=>recordingView('saved'));
$('plan-from-guide').addEventListener('click',()=>{$('recordings-dialog').close();programmeIndex=currentProgrammes.findIndex(programme=>phase(programme)==='future');selectProgramme();focusProgramme();});
let playerKind='archive';
let playing=false;
let position=0;
let duration=0;
let progressVisibleUntil=0;
let holdTimer;
let holding=false;
const timeText=seconds => { const total=Math.floor(seconds);return `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}`; };
function updatePlayer() {
  position=Math.max(0,Math.min(duration,position));
  $('position').value=String(position);
  $('elapsed').textContent=timeText(position);
  $('duration').textContent=timeText(duration);
  $('pause').textContent=playing ? 'Ⅱ' : '▶';
  $('pause').setAttribute('aria-label',playing ? 'Pauzeren' : 'Afspelen');
  $('seek-area').hidden=performance.now()>=progressVisibleUntil;
}
function openPlayer(opener,kind=phase(selectedProgramme)==='current' ? 'live' : 'archive') {
  if (phase(selectedProgramme)==='future') return;
  playerKind=kind;
  playerOpener=opener;
  $('player-mode').textContent=kind==='live' ? '● LIVE KIJKEN' : kind==='recording' ? '◉ OPNAME' : '↶ TERUGKIJKEN';
  $('rewind').disabled=$('forward').disabled=kind==='live';
  document.querySelector('.player-hint').hidden=kind==='live';
  playerProgramme={...selectedProgramme,channel:cleanName(sources[sourceIndex].channel),day:shortFormat.format(calendar(dayOffset))};
  position=0;duration=playerProgramme.minutes*60;progressVisibleUntil=0;playing=true;
  $('position').max=String(duration);
  $('player-title').textContent=playerProgramme.title;
  $('player-meta').textContent=`${playerProgramme.channel} · ${playerProgramme.day} · ${minuteTime(playerProgramme.start)} · ${kind==='live' ? 'LIVE · VOORBEELD' : kind==='recording' ? 'VAN JE TV · VOORBEELD' : 'UIT PROVIDERARCHIEF'}`;
  updatePlayer();$('player-dialog').showModal();
}
function primaryAction(opener) {const state=phase(selectedProgramme);if(state==='future')toggleRecording();else if(state==='current'){const wasPlanned=planned.has(planKey(selectedProgramme));toggleRecording();if(!wasPlanned&&planned.has(planKey(selectedProgramme)))openPlayer(opener);}else openPlayer(opener);}
$('play-saved').addEventListener('click',()=>{$('recordings-dialog').close();programmeIndex=currentProgrammes.findIndex(programme=>programme.offset===-1&&programme.slot===4);selectProgramme();openPlayer(document.querySelector(`[data-programme="${programmeIndex}"]`),'recording');});
$('close-player').addEventListener('click',() => $('player-dialog').close());
function stopHolding() {clearTimeout(holdTimer);holding=false;}
$('player-dialog').addEventListener('close',() => {playing=false;stopHolding();if(playerOpener)playerOpener.focus();});
$('pause').addEventListener('click',() => {playing=!playing;if(position>=duration)position=0;updatePlayer();});
function seek(seconds) {if(playerKind==='live')return;position+=seconds;progressVisibleUntil=performance.now()+3000;updatePlayer();}
$('position').addEventListener('input',event => {position=Number(event.target.value);progressVisibleUntil=performance.now()+3000;updatePlayer();});
for (const [id,direction] of [['rewind',-1],['forward',1]]) {
  const button=$(id);
  button.addEventListener('pointerdown',event => {
    if(event.button!==0)return;
    stopHolding();holding=true;button.setPointerCapture(event.pointerId);
    const started=performance.now();seek(direction*15);
    const repeat=() => {if(!holding)return;seek(direction*(performance.now()-started>=2000?30:15));holdTimer=setTimeout(repeat,500);};
    holdTimer=setTimeout(repeat,500);
  });
  for(const type of ['pointerup','pointercancel','lostpointercapture'])button.addEventListener(type,stopHolding);
  button.addEventListener('click',event => {if(event.detail===0)seek(direction*15);});
  button.addEventListener('contextmenu',event => event.preventDefault());
}
let lastTick=performance.now();
setInterval(() => {
  const now=performance.now(),elapsed=(now-lastTick)/1000;lastTick=now;
  if(!$('player-dialog').open)return;
  if(playing&&!document.hidden){position+=Math.min(elapsed,1);if(position>=duration){position=duration;playing=false;}}
  updatePlayer();
},250);
window.addEventListener('blur',stopHolding);
document.addEventListener('visibilitychange',() => {if(document.hidden)stopHolding();});
function goLive() {if($('player-dialog').open)$('player-dialog').close();jumpNow();toast('De gids staat weer bij de huidige uitzending.');}
$('player-live').addEventListener('click',goLive);
document.addEventListener('keydown',event => {
  const player=$('player-dialog');
  if (player.open) {
    if (event.target.matches('input')) return;
    if (['ArrowLeft','ArrowRight'].includes(event.key)) {event.preventDefault();seek(event.key==='ArrowLeft'?-15:15);}
    return;
  }
  if ($('recordings-dialog').open || event.target.matches('input,select')) return;
  const menuOpen=!document.body.classList.contains('menu-closed');
  if ((event.key==='Escape'||event.key==='Backspace'||event.key==='ArrowLeft')&&menuOpen&&!event.target.closest('.day-picker,.tabbar')) {event.preventDefault();setMenu(false);focusProgramme();}
  if(['r','R','MediaRecord','Record'].includes(event.key)&&event.target.matches('.programme')&&phase(selectedProgramme)!=='past'){event.preventDefault();toggleRecording();}
  if(event.key==='Enter'&&event.target.matches('.programme')) {event.preventDefault();primaryAction(event.target);}
});
function updateClock() { $('clock').textContent=new Date().toLocaleTimeString('nl-NL',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Amsterdam'}); if(currentProgrammes.length)selectProgramme(); }
updateClock();setInterval(updateClock,15000);
updatePlanning();render();
requestAnimationFrame(jumpNow);
