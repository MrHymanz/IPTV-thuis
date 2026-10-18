'use strict';
const $ = id => document.getElementById(id);
const channels = [
  {name:'NPO 1',logo:'npo 1',style:'npo',category:'Nieuws',title:'NOS Journaal',time:'20:00 – 20:35',duration:2100},
  {name:'NPO 2',logo:'npo 2',style:'npo npo2',category:'Entertainment',title:'De mooiste treinreizen',time:'20:25 – 21:15',duration:3000},
  {name:'RTL 4',logo:'RTL 4',style:'rtl',category:'Entertainment',title:'B&B Vol Liefde',time:'20:30 – 22:00',duration:5400},
  {name:'Ziggo Sport',logo:'ZIGGO',style:'sport',category:'Sport',title:'Voetbal: de nabeschouwing',time:'20:00 – 21:00',duration:3600},
  {name:'NOS Nieuws',logo:'NOS',style:'news',category:'Nieuws',title:'Het nieuws van vandaag',time:'20:00 – 20:30',duration:1800},
  {name:'NPO 3',logo:'npo 3',style:'npo',category:'Entertainment',title:'3 op Reis',time:'20:30 – 21:15',duration:2700}
];
const recordings = [
  {title:'Dwars door de Lage Landen',channel:'NPO 2',meta:'Aflevering 3 · 50 min',date:'Gisteren opgenomen',art:'nature',symbol:'↟',position:1140,duration:3000},
  {title:'Het verhaal van Nederland',channel:'NPO 1',meta:'Aflevering 6 · 45 min',date:'Vrijdag opgenomen',art:'city',symbol:'⌂',position:0,duration:2700},
  {title:'De Avondshow',channel:'NPO 1',meta:'Aflevering 12 · 35 min',date:'Donderdag opgenomen',art:'night',symbol:'☾',position:0,duration:2100}
];
const logo = channel => `<span class="channel-logo logo-${channel.style.split(' ').join(' logo-')}">${channel.logo}</span>`;
function channelRow(channel) {
  return `<button class="channel-row" data-play="${channel.title}" data-channel="${channel.name}" data-duration="${channel.duration}">${logo(channel)}<span class="channel-info"><strong>${channel.title}</strong><span class="channel-meta"><span>${channel.name}</span><span>${channel.time}</span></span><span class="channel-track"><span></span></span></span><span class="row-play" aria-hidden="true">▶</span></button>`;
}
$('home-channels').innerHTML = channels.slice(0,3).map(channelRow).join('');
$('recordings-list').innerHTML = recordings.map(item => `<button class="recording-card" data-play="${item.title}" data-channel="${item.channel}" data-duration="${item.duration}" data-position="${item.position}"><span class="recording-art art-${item.art}" aria-hidden="true">${item.symbol}</span><span class="recording-copy"><span class="small-muted">${item.channel} · ${item.date}</span><strong>${item.title}</strong><p>${item.meta}</p><span class="tag">${item.position ? 'VERDER KIJKEN' : 'BEWAARD'}</span></span><span class="row-play" aria-hidden="true">▶</span></button>`).join('');
let filter = 'Alles';
function renderChannels() {
  const query = $('channel-search').value.toLocaleLowerCase('nl-NL').trim();
  const shown = channels.filter(channel => (filter === 'Alles' || channel.category === filter) && `${channel.name} ${channel.title}`.toLocaleLowerCase('nl-NL').includes(query));
  $('all-channels').innerHTML = shown.map(channelRow).join('');
  $('empty-search').hidden = shown.length > 0;
  $('channel-count').textContent = `${shown.length} zenders`;
}
$('channel-search').addEventListener('input', renderChannels);
$('filters').addEventListener('click', event => {
  const button = event.target.closest('[data-filter]');
  if (!button) return;
  filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(item => {
    item.classList.toggle('active', item === button);
    item.setAttribute('aria-pressed', String(item === button));
  });
  renderChannels();
});
renderChannels();
const guide = [
  {channel:channels[0],programmes:[['20:00','NOS Journaal','Nieuws · 35 min'],['20:35','Het verhaal van Nederland','Geschiedenis · 45 min'],['21:20','De Avondshow','Amusement · 35 min']]},
  {channel:channels[1],programmes:[['20:25','De mooiste treinreizen','Reizen · 50 min'],['21:15','Dwars door de Lage Landen','Documentaire · 50 min']]},
  {channel:channels[2],programmes:[['20:30','B&B Vol Liefde','Reality · 90 min'],['22:00','RTL Nieuws','Nieuws · 20 min']]}
];
$('guide-date').textContent = new Date().toLocaleDateString('nl-NL',{day:'numeric',month:'short',timeZone:'Europe/Amsterdam'});
$('guide-list').innerHTML = guide.map(group => `<div class="guide-group"><div class="guide-channel">${logo(group.channel)}${group.channel.name}</div>${group.programmes.map(item => `<div class="guide-programme"><time class="guide-time">${item[0]}</time><div><strong>${item[1]}</strong><p>${item[2]}</p></div><button class="record-button" data-record="${item[1]}" aria-label="Plan voorbeeldopname: ${item[1]}" aria-pressed="false">+</button></div>`).join('')}</div>`).join('');
let toastTimer;
function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3000);
}
function showView() {
  const requested = location.hash.slice(1) || 'home';
  const view = ['home','channels','recordings','guide'].includes(requested) ? requested : 'home';
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== `${view}-view`; });
  document.querySelectorAll('.nav-button').forEach(button => {
    const active = button.dataset.go === view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  window.scrollTo(0,0);
}
window.addEventListener('hashchange', showView);
let opener;
let playing = false;
let position = 0;
let duration = 3000;
const formatTime = value => {
  const seconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return `${hours ? `${hours}:` : ''}${hours ? String(minutes).padStart(2,'0') : minutes}:${String(seconds % 60).padStart(2,'0')}`;
};
function updatePlayer() {
  position = Math.max(0,Math.min(duration,position));
  $('timeline').value = String(position);
  $('elapsed').textContent = formatTime(position);
  $('total').textContent = formatTime(duration);
  $('pause').textContent = playing ? 'Ⅱ' : '▶';
  $('pause').setAttribute('aria-label',playing ? 'Pauzeren' : 'Afspelen');
}
function openPlayer(button) {
  opener = button;
  $('player-title').textContent = button.dataset.play;
  $('player-channel').textContent = button.dataset.channel;
  duration = Number(button.dataset.duration) || 3000;
  position = Number(button.dataset.position) || 0;
  playing = true;
  $('timeline').max = String(duration);
  updatePlayer();
  $('player').showModal();
}
document.addEventListener('click', event => {
  const nav = event.target.closest('[data-go]');
  if (nav) {
    if (location.hash === `#${nav.dataset.go}`) window.scrollTo(0,0);
    else location.hash = nav.dataset.go;
    return;
  }
  const play = event.target.closest('[data-play]');
  if (play) { openPlayer(play); return; }
  const record = event.target.closest('[data-record]');
  if (record) {
    const selected = record.getAttribute('aria-pressed') !== 'true';
    record.setAttribute('aria-pressed', String(selected));
    record.textContent = selected ? '✓' : '+';
    record.setAttribute('aria-label', `${selected ? 'Annuleer' : 'Plan'} voorbeeldopname: ${record.dataset.record}`);
    toast(selected ? 'Voorbeeldopname ingepland' : 'Voorbeeldopname geannuleerd');
  }
});
$('close-player').addEventListener('click', () => $('player').close());
$('player').addEventListener('close', () => { playing = false; stopHolding(); if (opener) opener.focus(); });
$('pause').addEventListener('click', () => { playing = !playing; updatePlayer(); });
$('timeline').addEventListener('input', event => { position = Number(event.target.value); updatePlayer(); });
let holdTimer;
let holding = false;
function stopHolding() { clearTimeout(holdTimer); holding = false; }
function seek(delta) { position += delta; updatePlayer(); }
for (const [id,direction] of [['rewind',-1],['forward',1]]) {
  const button = $(id);
  button.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    stopHolding();
    holding = true;
    button.setPointerCapture(event.pointerId);
    const started = performance.now();
    seek(direction * 15);
    const repeat = () => {
      if (!holding) return;
      seek(direction * (performance.now() - started >= 2000 ? 30 : 15));
      holdTimer = setTimeout(repeat,500);
    };
    holdTimer = setTimeout(repeat,500);
  });
  for (const name of ['pointerup','pointercancel','lostpointercapture']) button.addEventListener(name, stopHolding);
  button.addEventListener('click', event => { if (event.detail === 0) seek(direction * 15); });
  button.addEventListener('contextmenu', event => event.preventDefault());
}
window.addEventListener('blur', stopHolding);
document.addEventListener('visibilitychange', () => { if (document.hidden) stopHolding(); });
setInterval(() => {
  if (!playing || !$('player').open || document.hidden) return;
  position += 1;
  if (position >= duration) { position = duration; playing = false; }
  updatePlayer();
},1000);
$('about').addEventListener('click', () => $('about-dialog').showModal());
$('close-about').addEventListener('click', () => $('about-dialog').close());
showView();
