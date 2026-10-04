'use strict';
const $ = id => document.getElementById(id);
let offset = 0, total = 0, favorites = [], busy = false, searchVersion = 0;
function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; $('message').hidden = false; }
async function api(path, body) {
  const file = body instanceof File;
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':file ? 'application/octet-stream' : 'application/json', 'X-MijnTV':'1'}, body:file ? body : JSON.stringify(body)});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Aanvraag mislukt.'); return result;
}
function button(label, action, secondary = true) { const b = document.createElement('button'); b.textContent = label; if (secondary) b.className = 'secondary'; b.onclick = action; return b; }
function title(name, subtitle) { const wrap = document.createElement('div'); wrap.className = 'row-title'; const strong = document.createElement('strong'); strong.textContent = name; const small = document.createElement('small'); small.textContent = subtitle; wrap.append(strong, small); return wrap; }
async function mutate(path, body, success) {
  if (busy) return; busy = true; message(path.startsWith('/api/import') || path === '/api/refresh' ? 'Zenderlijst ophalen en verwerken… Bij een grote lijst kan dit enkele minuten duren.' : 'Bezig…');
  try { const result = await api(path, body); if (result.imported) offset = 0; message(result.imported ? `${result.imported.toLocaleString('nl-NL')} zenders geïmporteerd.` : success); await load(); }
  catch (error) { if (path === '/api/favorites/order') renderFavorites(); message(error.message, true); } finally { busy = false; }
}
async function search() {
  const version = ++searchVersion;
  const params = new URLSearchParams({q:$('query').value, group:$('group').value, offset});
  const result = await api('/api/channels?' + params); if (version !== searchVersion) return;
  total = result.total; $('catalog').replaceChildren(); $('results-count').textContent = `${total.toLocaleString('nl-NL')} zenders gevonden`;
  const selected = new Set(favorites.map(f => f.id));
  for (const channel of result.items) {
    const row = document.createElement('div'); row.className = 'row'; const b = button(selected.has(channel.id) ? '✓ Toegevoegd' : '+ Toevoegen', () => mutate('/api/favorites/add', {id:channel.id}, 'Zender toegevoegd.'), false); b.disabled = selected.has(channel.id);
    row.append(title(channel.name, channel.group_name || 'Geen groep'), b); $('catalog').append(row);
  }
  $('prev').disabled = offset === 0; $('next').disabled = offset + 100 >= total;
  $('catalog-page').textContent = total ? `${offset + 1}–${Math.min(offset + 100, total)}` : 'Geen resultaten';
}
function renderFavorites() {
  $('favorites').replaceChildren();
  if (!favorites.length) { const p = document.createElement('p'); p.textContent = 'Nog geen favorieten. Zoek links een zender en klik op Toevoegen.'; $('favorites').append(p); }
  favorites.forEach((f, i) => {
    const row = document.createElement('div'); row.className = 'row favorite-row'; row.dataset.id = f.id; row.dataset.label = f.label;
    const actions = document.createElement('div'); actions.className = 'actions';
    const handle = button('⠿', () => {}); handle.className = 'drag-handle';
    handle.setAttribute('aria-label', f.label + ' verslepen'); handle.title = 'Sleep om de volgorde te wijzigen';
    handle.addEventListener('pointerdown', e => startFavoriteDrag(e, row, handle));
    const logo = document.createElement('span'); logo.className = 'favorite-logo';
    logo.textContent = f.label.replace(/^(NL\||PRIME\|)\s*/i, '').slice(0, 2).toUpperCase();
    if (f.logo) {
      const image = document.createElement('img'); image.src = f.logo; image.alt = ''; image.loading = 'lazy'; image.draggable = false;
      image.onload = () => { logo.replaceChildren(image); logo.classList.add('has-image'); };
      image.onerror = () => image.remove();
      // Lazy loading starts only once the image is in the document.
      logo.append(image);
    }
    const move = delta => { const ids = favorites.map(x => x.id); [ids[i],ids[i+delta]] = [ids[i+delta],ids[i]]; mutate('/api/favorites/order', {ids}, 'Volgorde opgeslagen.'); };
    const up = button('↑', () => move(-1)); up.setAttribute('aria-label', f.label + ' omhoog'); up.disabled = i === 0;
    const down = button('↓', () => move(1)); down.setAttribute('aria-label', f.label + ' omlaag'); down.disabled = i === favorites.length - 1;
    const rename = button('Naam', () => { const label = prompt('Naam op de televisie:', f.label); if (label !== null) mutate('/api/favorites/rename', {id:f.id, label}, 'Naam opgeslagen.'); });
    const remove = button('Verwijder', () => { if (confirm(`${f.label} uit de favorieten verwijderen?`)) mutate('/api/favorites/remove', {id:f.id}, 'Favoriet verwijderd.'); });
    actions.append(up, down, rename, remove); row.append(handle, logo, title(`${i+1}. ${f.label}`, f.available ? (f.name || 'Eigen stream') : '⚠ Niet meer aanwezig in de M3U — voeg de juiste versie opnieuw toe'), actions); $('favorites').append(row);
  });
}
function startFavoriteDrag(event, row, handle) {
  if (busy || !event.isPrimary || event.button !== 0) return;
  event.preventDefault(); busy = true;
  const list = $('favorites'), original = Array.from(list.children, r => r.dataset.id);
  const startY = event.clientY, pointer = event.pointerId;
  let y = startY, moved = false, frame;
  // Capture on the stationary list. Capturing the handle loses the pointer
  // when insertBefore moves its row, which cancels a held drag in browsers.
  list.setPointerCapture(pointer);
  function position() {
    if (!moved) return;
    const target = Array.from(list.children).find(r => r !== row && y < r.getBoundingClientRect().top + r.getBoundingClientRect().height / 2);
    if (row.nextElementSibling !== (target || null)) list.insertBefore(row, target || null);
    Array.from(list.children).forEach((r, i) => { r.querySelector('.row-title strong').textContent = `${i + 1}. ${r.dataset.label}`; });
  }
  function scroll() {
    if (moved) {
      const edge = 90;
      const delta = y < edge ? -Math.min(18, (edge - y) / 4) : y > window.innerHeight - edge ? Math.min(18, (y - window.innerHeight + edge) / 4) : 0;
      if (delta) { window.scrollBy(0, delta); position(); }
    }
    frame = requestAnimationFrame(scroll);
  }
  function move(e) {
    if (e.pointerId !== pointer) return;
    y = e.clientY;
    if (!moved && Math.abs(y - startY) >= 6) { moved = true; row.classList.add('dragging'); }
    position();
  }
  function finish(e, cancel = false) {
    if (e && e.pointerId !== undefined && e.pointerId !== pointer) return;
    cancelAnimationFrame(frame); busy = false; row.classList.remove('dragging');
    list.removeEventListener('pointermove', move); list.removeEventListener('pointerup', up);
    list.removeEventListener('pointercancel', abort); list.removeEventListener('lostpointercapture', abort);
    document.removeEventListener('keydown', escape);
    if (list.hasPointerCapture(pointer)) list.releasePointerCapture(pointer);
    const ids = Array.from(list.children, r => r.dataset.id);
    if (cancel) { renderFavorites(); return; }
    if (ids.some((id, i) => id !== original[i])) mutate('/api/favorites/order', {ids}, 'Volgorde opgeslagen.');
  }
  const up = e => finish(e);
  const abort = e => finish(e, true);
  const escape = e => { if (e.key === 'Escape') { e.preventDefault(); finish(null, true); } };
  list.addEventListener('pointermove', move); list.addEventListener('pointerup', up);
  list.addEventListener('pointercancel', abort); list.addEventListener('lostpointercapture', abort);
  document.addEventListener('keydown', escape); frame = requestAnimationFrame(scroll);
}
async function load() {
  const [status, list, groups] = await Promise.all([api('/api/status'), api('/api/favorites'), api('/api/groups')]);
  favorites = list; $('stats').textContent = `${status.channels.toLocaleString('nl-NL')} zenders · ${status.favorites} favorieten`; $('refresh').disabled = !status.has_source;
  const selected = $('group').value; $('group').replaceChildren(new Option('Alle groepen', '')); groups.forEach(g => $('group').add(new Option(g || 'Zonder groep', g))); $('group').value = selected;
  renderFavorites(); await search();
}
$('file-form').onsubmit = async e => { e.preventDefault(); const file = $('file').files[0]; if (!file || busy) return; if (file.size > 512*1024*1024) { message('Het bestand is groter dan 512 MB.', true); return; } await mutate('/api/import/file', file, 'Lijst geïmporteerd.'); $('file-form').reset(); };
$('url-form').onsubmit = async e => { e.preventDefault(); const url = $('source').value.trim(); $('source').value = ''; await mutate('/api/import', {url}, 'Lijst geïmporteerd.'); };
$('xtream-form').onsubmit = async e => {
  e.preventDefault();
  if (busy) return;
  const body = {server:$('xtream-server').value.trim(), username:$('xtream-user').value, password:$('xtream-password').value};
  $('xtream-password').value = '';
  await mutate('/api/import/xtream', body, 'Zenders opgehaald.');
};
$('refresh').onclick = () => mutate('/api/refresh', {}, 'Lijst vernieuwd.');
$('manual-form').onsubmit = async e => { e.preventDefault(); const body = {label:$('manual-name').value, url:$('manual-url').value}; $('manual-url').value = ''; await mutate('/api/favorites/manual', body, 'Eigen zender toegevoegd.'); };
$('search-form').onsubmit = e => { e.preventDefault(); offset = 0; search().catch(e => message(e.message,true)); };
$('prev').onclick = () => { offset = Math.max(0,offset-100); search().catch(e => message(e.message,true)); };
$('next').onclick = () => { offset += 100; search().catch(e => message(e.message,true)); };
load().catch(e => message(e.message, true));
