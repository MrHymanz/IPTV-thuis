'use strict';
let favorites = [], selected = 0, watching = false, signature = '';
const $ = id => document.getElementById(id);
const grid = $('channels');
function render(focus = false) {
  grid.replaceChildren();
  const page = Math.floor(selected / 10), pages = Math.max(1, Math.ceil(favorites.length / 10));
  if (!favorites.length) {
    const empty = document.createElement('p'); empty.className = 'empty';
    empty.textContent = 'Nog geen favorieten. Open Zenders beheren, importeer je M3U en voeg zenders toe.'; grid.append(empty);
  }
  favorites.slice(page * 10, page * 10 + 10).forEach((channel, i) => {
    const index = page * 10 + i, button = document.createElement('button');
    button.className = 'channel' + (selected === index ? ' selected' : '');
    const number = document.createElement('span'); number.className = 'number'; number.textContent = String(index + 1).padStart(2, '0');
    const logo = document.createElement('span'); logo.className = 'logo'; logo.textContent = 'TV';
    const label = document.createElement('span'); label.className = 'channel-name'; label.textContent = channel.label;
    button.setAttribute('aria-label', `${index + 1}. ${channel.label}${channel.available ? '' : ', niet beschikbaar'}`);
    button.append(number, logo, label);
    button.onclick = () => { selected = index; openChannel(); };
    button.onfocus = () => { selected = index; grid.querySelectorAll('.channel').forEach((b, j) => b.classList.toggle('selected', j === i)); };
    grid.append(button);
  });
  $('page-label').textContent = `Pagina ${page + 1} van ${pages}`;
  $('dots').replaceChildren();
  for (let i = 0; i < pages; i++) { const dot = document.createElement('span'); dot.className = 'dot' + (i === page ? ' active' : ''); $('dots').append(dot); }
  $('previous').disabled = page === 0; $('next').disabled = page + 1 >= pages;
  if (focus && favorites.length) grid.children[selected % 10].focus();
}
function changePage(delta) {
  if (!favorites.length) return;
  const page = Math.max(0, Math.min(Math.ceil(favorites.length / 10) - 1, Math.floor(selected / 10) + delta));
  selected = Math.min(favorites.length - 1, page * 10 + selected % 10); render(true);
}
function openChannel() {
  const channel = favorites[selected]; if (!channel) return;
  watching = true; $('player-title').textContent = channel.label;
  $('player-logo').textContent = channel.available ? 'TV' : 'Niet beschikbaar';
  $('current').textContent = `${selected + 1} · ${channel.label}`;
  $('player').hidden = false; $('home').inert = true; $('back').focus();
}
function closeChannel() { watching = false; $('player').hidden = true; $('home').inert = false; render(true); }
function zap(delta) {
  const available = favorites.map((c, i) => c.available ? i : -1).filter(i => i >= 0);
  if (!available.length) { closeChannel(); return; }
  const current = available.indexOf(selected);
  selected = available[current < 0 ? 0 : (current + delta + available.length) % available.length]; openChannel();
}
$('previous').onclick = () => changePage(-1); $('next').onclick = () => changePage(1); $('back').onclick = closeChannel;
document.addEventListener('keydown', e => {
  if (['Escape', 'Backspace', 'BrowserBack'].includes(e.key)) { e.preventDefault(); if (watching) closeChannel(); return; }
  const direction = {ArrowLeft:-1, ArrowRight:1, ArrowUp:-5, ArrowDown:5};
  if (watching) {
    const delta = ['ArrowRight','ArrowUp','PageDown'].includes(e.key) ? 1 : ['ArrowLeft','ArrowDown','PageUp'].includes(e.key) ? -1 : 0;
    if (delta) { e.preventDefault(); zap(delta); } return;
  }
  if (!favorites.length) return;
  if (e.key === 'Enter' && ['previous','next'].includes(document.activeElement.id)) return;
  if (e.key === 'Enter') { e.preventDefault(); openChannel(); }
  else if (e.key === 'PageUp' || e.key === 'PageDown') { e.preventDefault(); changePage(e.key === 'PageUp' ? -1 : 1); }
  else if (Object.hasOwn(direction, e.key)) { e.preventDefault(); selected = Math.max(0, Math.min(favorites.length - 1, selected + direction[e.key])); render(true); }
});
async function refresh() {
  try {
    const response = await fetch('/api/favorites'); if (!response.ok) throw new Error('Beheerverbinding verbroken; vernieuw deze pagina om opnieuw in te loggen.');
    const next = await response.json(), nextSignature = JSON.stringify(next);
    if (signature !== nextSignature) {
      const oldId = favorites[selected]?.id; favorites = next;
      const index = favorites.findIndex(c => c.id === oldId);
      selected = index >= 0 ? index : Math.min(selected, Math.max(0, favorites.length - 1)); signature = nextSignature;
      if (watching && favorites.length) openChannel(); else { if (watching) closeChannel(); render(); }
    }
    $('clock').textContent = new Date().toLocaleTimeString('nl-NL', {hour:'2-digit', minute:'2-digit'});
  } catch (error) { $('page-label').textContent = error.message; }
}
render(); refresh(); setInterval(refresh, 5000);
