import { api, friendlyError } from './api.js';
import { closeNowPlaying, isNowPlayingOpen } from './nowplaying.js';
import { player } from './player.js';
import { installUpdate, store } from './store.js';
import { createPlaylistFlow } from './tracks.js';
import { $, $$, esc, icon, cover, mosaic, likedCover, songCount, toast, promptDialog, closeMenu, isMenuOpen, isModalOpen, closeModal, setPicturesHidden } from './ui.js';
import * as artist from './views/artist.js';
import * as collection from './views/collection.js';
import * as discover from './views/discover.js';
import * as downloads from './views/downloads.js';
import * as home from './views/home.js';
import * as library from './views/library.js';
import * as recap from './views/recap.js';
import * as releases from './views/releases.js';
import * as search from './views/search.js';
import * as settings from './views/settings.js';
import * as storage from './views/storage.js';

const ROUTES = [
  [/^home$/, 'home', home.render],
  [/^new$/, 'home', releases.render],
  [/^search$/, 'search', search.render],
  [/^discover$/, 'discover', discover.render],
  [/^artist\/(.+)$/, 'search', artist.render],
  [/^album\/(.+)$/, 'search', collection.renderAlbum],
  [/^ytplaylist\/(.+)$/, 'search', collection.renderYtPlaylist],
  [/^mix\/(.+)$/, 'home', collection.renderMix],
  [/^liked$/, 'library', collection.renderLiked],
  [/^playlist\/(\d+)$/, 'library', collection.renderPlaylist],
  [/^library$/, 'library', library.render],
  [/^storage$/, 'library', storage.render],
  [/^recap$/, 'home', recap.render],
  [/^downloads$/, 'downloads', downloads.render],
  [/^settings$/, 'settings', settings.render],
];

let context = null;
let usbWasRunning = false;
let lastSyncVersion = null;
const UPDATE_DISMISSED_KEY = 'shirim.update.dismissed';

function dismissedUpdate(versionName) {
  try {
    return localStorage.getItem(UPDATE_DISMISSED_KEY) === versionName;
  } catch {
    return false;
  }
}

function createContext() {
  const cleanups = [];
  return {
    alive: true,
    onCleanup(fn) {
      cleanups.push(fn);
    },
    dispose() {
      this.alive = false;
      for (const fn of cleanups) {
        try {
          fn();
        } catch (error) {
          console.error(error);
        }
      }
    },
  };
}

function route() {
  closeMenu();
  if (isNowPlayingOpen()) closeNowPlaying({ fromHistory: true });
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const found = ROUTES.map(([pattern, nav, view]) => [path.match(pattern), nav, view]).find(([match]) => match);
  if (!found) {
    location.replace('#/home');
    return;
  }
  const [match, nav, view] = found;
  context?.dispose();
  context = createContext();
  for (const item of $$('[data-nav]')) item.classList.toggle('active', item.dataset.nav === nav);
  $('#queue-panel').hidden = true;

  const el = $('#view');
  el.innerHTML = '';
  el.style.removeProperty('--hero-color');
  $('#main').scrollTop = 0;
  const params = Object.fromEntries(new URLSearchParams(query));
  if (match[1]) params.id = decodeURIComponent(match[1]);
  const ctx = context;
  Promise.resolve(view(el, params, ctx)).catch((error) => {
    console.error(error);
    if (ctx.alive) el.innerHTML = `<div class="empty">${icon('warning')}<div>${esc(friendlyError(error))}</div></div>`;
  });
  markSidebar();
}

// region sidebar

function renderSidebar() {
  const rows = [
    `<a class="side-item" href="#/liked" data-key="liked">${likedCover('side-img')}
      <div class="grow"><div class="side-name ellipsis">שירים שאהבתי</div><div class="muted small ellipsis">פלייליסט · ${songCount(store.likes.size)}</div></div></a>`,
    ...store.playlists.map((p) => `
      <a class="side-item" href="#/playlist/${p.id}" data-key="playlist:${p.id}">${mosaic(p.thumbs, 'side-img')}
        <div class="grow"><div class="side-name ellipsis">${esc(p.name)}</div><div class="muted small ellipsis">פלייליסט · ${songCount(p.count)}</div></div></a>`),
    ...store.followList.map((a) => `
      <a class="side-item round" href="#/artist/${encodeURIComponent(a.browseId)}" data-key="artist:${esc(a.browseId)}">${cover(a.thumb, 'side-img')}
        <div class="grow"><div class="side-name ellipsis">${esc(a.name)}</div><div class="muted small">זמר</div></div></a>`),
  ];
  if (store.library.length) {
    rows.push(`
      <a class="side-item" href="#/library?tab=device" data-key="device"><span class="side-img saved-cover">${icon('download')}</span>
        <div class="grow"><div class="side-name">השירים שבמכשיר</div><div class="muted small">${songCount(store.library.length)}</div></div></a>`);
  }
  $('#side-list').innerHTML = rows.join('');
  markSidebar();
}

function markSidebar() {
  const hash = location.hash;
  for (const item of $$('.side-item')) {
    const href = item.getAttribute('href');
    item.classList.toggle('active', hash === href || hash.startsWith(`${href}?`));
    item.classList.toggle('playing', player.isContext(item.dataset.key) && player.playing);
  }
}

// endregion

function renderQueueChrome(snapshot) {
  const active = snapshot.counts.queued + snapshot.counts.running;
  for (const badge of $$('[data-downloads-badge]')) {
    badge.hidden = !active;
    badge.textContent = active > 999 ? '999+' : active;
  }
  for (const badge of $$('[data-releases-nav-badge]')) {
    badge.hidden = !snapshot.releasesUnseen;
    badge.textContent = snapshot.releasesUnseen;
  }

  // The paired computer or phone changed likes, playlists or followed artists
  const syncVersion = snapshot.sync?.version ?? 0;
  if (lastSyncVersion !== null && syncVersion !== lastSyncVersion) {
    store.loadCollection().catch(() => {});
    store.loadLibrary().catch(() => {});
  }
  lastSyncVersion = syncVersion;

  const usb = snapshot.usb;
  if (usb) {
    if (usbWasRunning && !usb.running && !usb.error) toast(`ההעתקה לדיסק און קי הסתיימה: ${songCount(usb.copied)} הועתקו`);
    usbWasRunning = usb.running;
  }

  const banner = $('#banner');
  const update = snapshot.sync?.update;
  if (!snapshot.blocked && update && !dismissedUpdate(update.versionName)) {
    banner.hidden = false;
    banner.innerHTML = `
      ${icon('download')}
      <div class="grow"><b>גרסה חדשה של שירים (${esc(update.versionName)})</b> מחכה במחשב. העדכון לוקח כמה שניות.</div>
      <button class="btn btn-sm btn-primary" data-banner-update>${icon('download')} עדכון</button>
      <button class="icon-btn" data-banner-dismiss="${esc(update.versionName)}" title="אחר כך">${icon('close')}</button>`;
    return;
  }
  if (!snapshot.blocked) {
    banner.hidden = true;
    return;
  }
  const canLogin = store.info?.actions?.includes('login');
  banner.hidden = false;
  banner.innerHTML = `
    ${icon('warning')}
    <div class="grow"><b>השמירה לאופליין הושהתה.</b> יוטיוב מבקש לאמת שזה לא בוט. אפשר להתחבר לחשבון יוטיוב או לחכות קצת ולנסות שוב.</div>
    ${canLogin ? `<button class="btn btn-sm" data-banner-login>${icon('user')} התחברות ליוטיוב</button>` : `<a class="btn btn-sm" href="#/settings">${icon('user')} התחברות</a>`}
    <button class="btn btn-sm btn-primary" data-banner-resume>${icon('retry')} נסה שוב</button>`;
}

function applyFilteredMode(filter) {
  const active = !!filter?.active;
  setPicturesHidden(active);
  document.documentElement.classList.toggle('filtered', active);
  $('.brand').querySelector('[data-filtered-pill]')?.remove();
  if (active) $('.brand').insertAdjacentHTML('beforeend', '<span class="pill ok" data-filtered-pill>מסונן</span>');
  player.changed();
}

async function askForPin() {
  while (store.info.pinRequired) {
    const pin = await promptDialog({
      title: 'קוד גישה',
      message: 'הכניסו את קוד הגישה שהגדרתם באפליקציה במחשב.',
      inputType: 'password',
      confirmText: 'כניסה',
    });
    if (pin === null) continue;
    try {
      await api.post('/api/pin', { pin });
      await store.loadInfo();
    } catch {
      toast('קוד שגוי', 'error');
    }
  }
}

// Called by the Android app when the system back button is pressed; returns true if handled
window.shirimBack = () => {
  if (isMenuOpen()) {
    closeMenu();
    return true;
  }
  if (isModalOpen()) {
    closeModal();
    return true;
  }
  if (isNowPlayingOpen()) {
    closeNowPlaying();
    return true;
  }
  if (!$('#queue-panel').hidden) {
    $('#queue-panel').hidden = true;
    return true;
  }
  if (!/^#\/home/.test(location.hash)) {
    history.back();
    return true;
  }
  return false;
};

async function init() {
  try {
    await store.loadInfo();
  } catch (error) {
    $('#view').innerHTML = `<div class="empty">${icon('warning')}<div>לא ניתן להתחבר לאפליקציה: ${esc(friendlyError(error))}</div>
      <button class="btn" style="margin-top:12px" onclick="location.reload()">נסה שוב</button></div>`;
    return;
  }
  document.documentElement.dataset.platform = store.info.platform;
  document.documentElement.classList.toggle('remote', store.info.remote);
  await askForPin();
  applyFilteredMode(store.info.filter);

  await Promise.all([store.loadSettings(), store.loadLibrary(), store.loadCollection()])
    .catch((error) => toast(friendlyError(error), 'error'));

  store.on('queue', renderQueueChrome);
  store.on('pin-required', () => location.reload());
  for (const event of ['playlists', 'follows', 'likes', 'library']) store.on(event, renderSidebar);
  player.onChange(markSidebar);
  renderSidebar();
  store.startPolling();

  $('#banner').addEventListener('click', (e) => {
    if (e.target.closest('[data-banner-resume]')) api.post('/api/queue/resume').catch((err) => toast(friendlyError(err), 'error'));
    if (e.target.closest('[data-banner-login]')) api.post('/api/action', { name: 'login' }).catch((err) => toast(friendlyError(err), 'error'));
    if (e.target.closest('[data-banner-update]')) installUpdate(e.target.closest('button'));
    const dismiss = e.target.closest('[data-banner-dismiss]');
    if (dismiss) {
      try {
        localStorage.setItem(UPDATE_DISMISSED_KEY, dismiss.dataset.bannerDismiss);
      } catch {
        // storage unavailable: the banner comes back next time
      }
      $('#banner').hidden = true;
    }
  });
  $('[data-create-playlist]').addEventListener('click', async () => {
    const created = await createPlaylistFlow();
    if (created) location.hash = `#/playlist/${created.id}`;
  });

  window.addEventListener('hashchange', route);
  if (!location.hash || location.hash === '#/') location.replace('#/home');
  route();
}

init();
