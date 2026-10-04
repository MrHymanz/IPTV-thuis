'use strict';
const $ = id => document.getElementById(id);
let offset = 0, total = 0, favorites = [], busy = false, searchVersion = 0;
function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; $('message').hidden = false; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json', 'X-MijnTV':'1'}, body:JSON.stringify(body)});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Aanvraag mislukt.'); return result;
}
function button(label, action, secondary = true) { const b = document.createElement('button'); b.textContent = label; if (secondary) b.className = 'secondary'; b.onclick = action; return b; }
function title(name, subtitle) { const wrap = document.createElement('div'); wrap.className = 'row-title'; const strong = document.createElement('strong'); strong.textContent = name; const small = document.createElement('small'); small.textContent = subtitle; wrap.append(strong, small); return wrap; }
async function mutate(path, body, success) {
  if (busy) return; busy = true; message('Bezig…');
  try { const result = await api(path, body); if (result.imported) offset = 0; message(result.imported ? `${result.imported.toLocaleString('nl-NL')} zenders geïmporteerd.` : success); await load(); }
  catch (error) { message(error.message, true); } finally { busy = false; }
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
    const row = document.createElement('div'); row.className = 'row'; const actions = document.createElement('div'); actions.className = 'actions';
    const move = delta => { const ids = favorites.map(x => x.id); [ids[i],ids[i+delta]] = [ids[i+delta],ids[i]]; mutate('/api/favorites/order', {ids}, 'Volgorde opgeslagen.'); };
    const up = button('↑', () => move(-1)); up.setAttribute('aria-label', f.label + ' omhoog'); up.disabled = i === 0;
    const down = button('↓', () => move(1)); down.setAttribute('aria-label', f.label + ' omlaag'); down.disabled = i === favorites.length - 1;
    const rename = button('Naam', () => { const label = prompt('Naam op de televisie:', f.label); if (label !== null) mutate('/api/favorites/rename', {id:f.id, label}, 'Naam opgeslagen.'); });
    const remove = button('Verwijder', () => { if (confirm(`${f.label} uit de favorieten verwijderen?`)) mutate('/api/favorites/remove', {id:f.id}, 'Favoriet verwijderd.'); });
    actions.append(up, down, rename, remove); row.append(title(`${i+1}. ${f.label}`, f.available ? (f.name || 'Eigen stream') : '⚠ Niet meer aanwezig in de M3U — voeg de juiste versie opnieuw toe'), actions); $('favorites').append(row);
  });
}
async function load() {
  const [status, list, groups] = await Promise.all([api('/api/status'), api('/api/favorites'), api('/api/groups')]);
  favorites = list; $('stats').textContent = `${status.channels.toLocaleString('nl-NL')} zenders · ${status.favorites} favorieten`; $('refresh').disabled = !status.has_source;
  const selected = $('group').value; $('group').replaceChildren(new Option('Alle groepen', '')); groups.forEach(g => $('group').add(new Option(g || 'Zonder groep', g))); $('group').value = selected;
  renderFavorites(); await search();
}
$('file-form').onsubmit = async e => { e.preventDefault(); const file = $('file').files[0]; if (!file) return; if (file.size > 64*1024*1024) { message('Het bestand is groter dan 64 MB.', true); return; } await mutate('/api/import', {text:await file.text()}, 'Lijst geïmporteerd.'); $('file-form').reset(); };
$('url-form').onsubmit = async e => { e.preventDefault(); const url = $('source').value.trim(); $('source').value = ''; await mutate('/api/import', {url}, 'Lijst geïmporteerd.'); };
$('refresh').onclick = () => mutate('/api/refresh', {}, 'Lijst vernieuwd.');
$('manual-form').onsubmit = async e => { e.preventDefault(); const body = {label:$('manual-name').value, url:$('manual-url').value}; $('manual-url').value = ''; await mutate('/api/favorites/manual', body, 'Eigen zender toegevoegd.'); };
$('search-form').onsubmit = e => { e.preventDefault(); offset = 0; search().catch(e => message(e.message,true)); };
$('prev').onclick = () => { offset = Math.max(0,offset-100); search().catch(e => message(e.message,true)); };
$('next').onclick = () => { offset += 100; search().catch(e => message(e.message,true)); };
load().catch(e => message(e.message, true));
