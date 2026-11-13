// Small DOM helpers, formatting, toasts, dialogs, menus and cover colours.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);

export const icon = (name, extra = '') => `<svg class="i ${extra}"><use href="#i-${name}"/></svg>`;

export const PLACEHOLDER = '/icon.svg';

// Filtered mode shows no pictures at all (covers, artist photos, backgrounds taken from covers).
// Hidden until the app says whether filtered mode is on, so none flashes up at startup.
let picturesHidden = true;
export const setPicturesHidden = (hidden) => { picturesHidden = !!hidden; };
export const picture = (src) => (picturesHidden ? '' : src || '');

export const cover = (src, cls = '') =>
  `<img class="${cls}" src="${esc(picture(src) || PLACEHOLDER)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${PLACEHOLDER}'">`;

export function mosaic(thumbs, cls = '') {
  const images = (thumbs || []).map(picture).filter(Boolean);
  if (!images.length) return `<div class="${cls} mosaic empty">${icon('music')}</div>`;
  if (images.length < 4) return cover(images[0], cls);
  return `<div class="${cls} mosaic">${images.slice(0, 4).map((src) => cover(src)).join('')}</div>`;
}

export const likedCover = (cls = '') => `<div class="${cls} liked-cover">${icon('heart-fill')}</div>`;

export function formatDuration(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function totalDuration(tracks) {
  const seconds = tracks.reduce((sum, t) => sum + (t.duration || 0), 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} שע׳ ${minutes} דק׳` : `${minutes} דק׳`;
}

/** Colour a slider from its start to the dot, like a progress bar (styled through --fill in style.css). */
export function paintRange(input) {
  if (!input) return;
  const min = Number(input.min) || 0;
  const max = input.max === '' ? 100 : Number(input.max);
  const share = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
  input.style.setProperty('--fill', `${Math.max(0, Math.min(100, share))}%`);
}

// Dragging any slider repaints it right away
document.addEventListener('input', (e) => {
  if (e.target instanceof HTMLInputElement && e.target.type === 'range') paintRange(e.target);
}, true);

export const songCount = (n) => (n === 1 ? 'שיר אחד' : `${Number(n || 0).toLocaleString('he-IL')} שירים`);

/** "לפני 5 דקות" for a Unix time in seconds. */
export function timeAgo(seconds) {
  if (!seconds) return 'אף פעם';
  const minutes = Math.round((Date.now() / 1000 - seconds) / 60);
  if (minutes < 1) return 'עכשיו';
  if (minutes < 60) return minutes === 1 ? 'לפני דקה' : `לפני ${minutes} דקות`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? 'לפני שעה' : `לפני ${hours} שעות`;
  const days = Math.round(hours / 24);
  return days === 1 ? 'אתמול' : `לפני ${days} ימים`;
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function toast(message, type = 'info', timeout = 3000) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), timeout);
}

// region dialogs

function openModal(html, onReady) {
  const modal = $('#modal');
  modal.innerHTML = `<div class="dialog" role="dialog" aria-modal="true">${html}</div>`;
  modal.hidden = false;
  return new Promise((resolve) => {
    const close = (value) => {
      modal.hidden = true;
      modal.innerHTML = '';
      modal.onclick = null;
      document.removeEventListener('keydown', onKey);
      resolve(value);
    };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    modal.onclick = (e) => { if (e.target === modal) close(null); };
    onReady(modal, close);
  });
}

export const isModalOpen = () => !$('#modal').hidden;
export const closeModal = () => $('#modal').dispatchEvent(new MouseEvent('click'));

export function confirmDialog({ title, message = '', confirmText = 'אישור', cancelText = 'ביטול', danger = false, checkbox = '' }) {
  return openModal(`
    <h3>${esc(title)}</h3>
    ${message ? `<p>${esc(message)}</p>` : ''}
    ${checkbox ? `<label class="row-flex"><input type="checkbox" class="check" data-check> ${esc(checkbox)}</label>` : ''}
    <div class="actions">
      <button class="btn btn-ghost" data-cancel>${esc(cancelText)}</button>
      <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-ok>${esc(confirmText)}</button>
    </div>`, (modal, close) => {
    $('[data-cancel]', modal).onclick = () => close(null);
    $('[data-ok]', modal).onclick = () => close({ checked: !!$('[data-check]', modal)?.checked });
    $('[data-ok]', modal).focus();
  });
}

export function promptDialog({ title, message = '', value = '', placeholder = '', confirmText = 'אישור', inputType = 'text' }) {
  return openModal(`
    <h3>${esc(title)}</h3>
    ${message ? `<p>${esc(message)}</p>` : ''}
    <input class="input" type="${inputType}" value="${esc(value)}" placeholder="${esc(placeholder)}" data-input>
    <div class="actions">
      <button class="btn btn-ghost" data-cancel>ביטול</button>
      <button class="btn btn-primary" data-ok>${esc(confirmText)}</button>
    </div>`, (modal, close) => {
    const input = $('[data-input]', modal);
    $('[data-cancel]', modal).onclick = () => close(null);
    $('[data-ok]', modal).onclick = () => close(input.value);
    input.onkeydown = (e) => { if (e.key === 'Enter') close(input.value); };
    input.focus();
    input.select();
  });
}

export function customDialog(html, setup) {
  return openModal(html, setup);
}

// endregion

// region menus

let activeMenu = null;

export function closeMenu() {
  activeMenu?.remove();
  activeMenu = null;
}

export const isMenuOpen = () => !!activeMenu;

/** items: [{label, icon, action, danger, disabled}] or '-' for a separator. Opens as a bottom sheet on phones. */
export function openMenu(anchor, items, { title = '' } = {}) {
  closeMenu();
  const sheet = matchMedia('(max-width: 800px)').matches;
  const layer = document.createElement('div');
  layer.className = `menu-layer${sheet ? ' sheet' : ''}`;
  layer.innerHTML = `<div class="menu" role="menu">
    ${title && sheet ? `<div class="menu-title ellipsis">${esc(title)}</div>` : ''}
    ${items.map((item, i) => (item === '-' ? '<hr>' : `
      <button class="menu-item${item.danger ? ' danger' : ''}" role="menuitem" data-i="${i}" ${item.disabled ? 'disabled' : ''}>
        ${icon(item.icon || 'dot')}<span>${esc(item.label)}</span>
      </button>`)).join('')}
  </div>`;
  document.body.append(layer);
  activeMenu = layer;

  const menu = layer.firstElementChild;
  if (!sheet && anchor) {
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = menu;
    menu.style.left = `${Math.min(Math.max(8, rect.right - width), innerWidth - width - 8)}px`;
    menu.style.top = `${rect.bottom + height + 8 > innerHeight ? Math.max(8, rect.top - height - 4) : rect.bottom + 4}px`;
  }
  layer.addEventListener('click', (e) => {
    const button = e.target.closest('[data-i]');
    if (button?.disabled) return;
    closeMenu();
    if (button) items[Number(button.dataset.i)].action?.();
  });
  layer.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    closeMenu();
  });
}

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

// endregion

// region colours

const colorCache = new Map();

/** A darkened average colour of a cover, for backgrounds behind white text. */
export function dominantColor(url) {
  if (picturesHidden || !url || url.startsWith('data:') || url === PLACEHOLDER) return Promise.resolve(null);
  if (colorCache.has(url)) return colorCache.get(url);
  // Remote covers go through the app so the canvas isn't tainted by a cross-origin image
  const source = url.startsWith('/') ? url : `/img?u=${encodeURIComponent(url)}`;
  const promise = new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 24;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(img, 0, 0, 24, 24);
        const data = context.getImageData(0, 0, 24, 24).data;
        let r = 0;
        let g = 0;
        let b = 0;
        let weight = 0;
        for (let i = 0; i < data.length; i += 4) {
          // Favour saturated pixels so grey borders don't wash the colour out
          const w = 1 + (Math.max(data[i], data[i + 1], data[i + 2]) - Math.min(data[i], data[i + 1], data[i + 2])) / 24;
          r += data[i] * w;
          g += data[i + 1] * w;
          b += data[i + 2] * w;
          weight += w;
        }
        const k = 0.6 / weight;
        resolve(`rgb(${Math.round(r * k)}, ${Math.round(g * k)}, ${Math.round(b * k)})`);
      } catch {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = source;
  });
  colorCache.set(url, promise);
  return promise;
}

// endregion
