// Full-screen "Now playing" view, with time-synced lyrics.

import { api } from './api.js';
import { player } from './player.js';
import { shareTrack } from './share.js';
import { isLocal, store } from './store.js';
import { addToPlaylistDialog, downloadTracks, leadArtist, startRadio } from './tracks.js';
import { $, esc, icon, dominantColor, formatDuration, openMenu, paintRange, picture, toast } from './ui.js';

const LYRICS_LEAD = 0.3;  // highlight a line slightly before it's sung
const MANUAL_SCROLL_PAUSE = 4000;

const root = $('#now-playing');
let open = false;
let seeking = false;
let touchStartY = null;
let lyricsMode = false;
let lyrics = null;  // {videoId, synced, lines, source} for the current song, or null while loading
let activeLine = -1;
let userScrolledAt = 0;
const lyricsCache = new Map();

root.innerHTML = `
  <div class="np-bg"></div>
  <div class="np-inner">
    <header class="np-head">
      <button class="icon-btn" data-close title="סגור">${icon('chevron-down')}</button>
      <div class="np-context"><small>מתנגן מתוך</small><b class="ellipsis" data-context></b></div>
      <button class="icon-btn" data-more title="עוד">${icon('more')}</button>
    </header>
    <div class="np-art"><img data-art alt=""></div>
    <div class="np-lyrics" data-lyrics-view hidden></div>
    <div class="np-meta">
      <div class="grow"><h2 class="ellipsis" data-title></h2><a class="np-artist ellipsis" href="#" data-artist></a></div>
      <button class="icon-btn lg" data-like title="לייק"></button>
    </div>
    <div class="np-seek">
      <input type="range" min="0" max="1000" value="0" dir="ltr" data-seek aria-label="מיקום בשיר">
      <div class="np-times" dir="ltr"><span data-time>0:00</span><span data-duration>0:00</span></div>
    </div>
    <div class="np-controls" dir="ltr">
      <button class="icon-btn lg" data-shuffle title="ערבוב">${icon('shuffle')}</button>
      <button class="icon-btn xl" data-prev title="הקודם">${icon('prev')}</button>
      <button class="play-btn np-play" data-toggle title="נגן"></button>
      <button class="icon-btn xl" data-next title="הבא">${icon('next')}</button>
      <button class="icon-btn lg" data-repeat title="חזרה">${icon('repeat')}</button>
    </div>
    <div class="np-volume desktop-only">
      <button class="icon-btn" data-np-mute title="השתקה">${icon('volume')}</button>
      <input type="range" min="0" max="100" value="100" dir="ltr" data-np-volume aria-label="עוצמה">
    </div>
    <footer class="np-foot">
      <button class="np-chip" data-lyrics>${icon('mic')}<span>מילים</span></button>
      <button class="np-chip" data-sleep>${icon('moon')}<span data-sleep-label>טיימר שינה</span></button>
      <button class="np-chip" data-download>${icon('download')}<span data-download-label>שמור לאופליין</span></button>
      <button class="np-chip" data-queue>${icon('queue')}<span>הבאים בתור</span></button>
    </footer>
  </div>`;

const lyricsView = $('[data-lyrics-view]', root);

export const isNowPlayingOpen = () => open;

export function openNowPlaying(event) {
  if (!player.current) return;
  if (event?.detail?.lyrics) setLyricsMode(true);
  if (open) return;
  open = true;
  root.hidden = false;
  history.pushState({ nowPlaying: true }, '');
  requestAnimationFrame(() => root.classList.add('open'));
  render();
  renderTime();
}

export function closeNowPlaying({ fromHistory = false } = {}) {
  if (!open) return;
  open = false;
  root.classList.remove('open');
  setTimeout(() => { if (!open) root.hidden = true; }, 300);
  if (!fromHistory && history.state?.nowPlaying) history.back();
}

// region lyrics

function setLyricsMode(on) {
  lyricsMode = on;
  $('.np-inner', root).classList.toggle('lyrics-mode', on);
  lyricsView.hidden = !on;
  $('[data-lyrics]', root).classList.toggle('on', on);
  if (on) loadLyrics();
}

async function loadLyrics() {
  const track = player.current;
  if (!track || !lyricsMode) return;
  if (lyrics?.videoId === track.videoId) return;
  activeLine = -1;
  if (lyricsCache.has(track.videoId)) {
    lyrics = lyricsCache.get(track.videoId);
    renderLyrics();
    return;
  }
  lyrics = { videoId: track.videoId, loading: true, lines: [] };
  renderLyrics();
  let result;
  try {
    result = await api.get('/api/lyrics', {
      videoId: track.videoId, title: track.title, artist: track.artist, duration: track.duration || 0,
    });
  } catch {
    result = { synced: false, lines: [], source: '', failed: true };
  }
  const data = { videoId: track.videoId, ...result };
  if (!result.failed) lyricsCache.set(track.videoId, data);
  if (player.current?.videoId !== track.videoId) return;
  lyrics = data;
  renderLyrics();
}

function renderLyrics() {
  if (!lyrics) return;
  lyricsView.classList.toggle('plain', !lyrics.synced);
  if (lyrics.loading) {
    lyricsView.innerHTML = '<div class="spinner"></div>';
    return;
  }
  const lines = lyrics.lines;
  if (!lines.some((line) => line.text)) {
    lyricsView.innerHTML = `<div class="lyrics-empty">${icon('mic')}<div>${lyrics.failed
      ? 'לא הצלחנו לטעון מילים. בדקו את החיבור לאינטרנט.'
      : 'לשיר הזה אין עדיין מילים'}</div></div>`;
    return;
  }
  lyricsView.innerHTML = lines.map((line, i) => (lyrics.synced
    ? `<p data-line="${i}" data-time="${line.time}">${line.text ? esc(line.text) : '♪'}</p>`
    : `<p>${esc(line.text) || '&nbsp;'}</p>`)).join('')
    + (lyrics.source ? `<div class="lyrics-source">מילים: ${esc(lyrics.source)}</div>` : '');
  lyrics.shown = lines;
  highlightLyrics(true);
}

function highlightLyrics(jump = false) {
  if (!lyricsMode || !lyrics?.synced || !lyrics.shown) return;
  const position = player.position + LYRICS_LEAD;
  let index = -1;
  for (let i = 0; i < lyrics.shown.length; i++) {
    if (lyrics.shown[i].time <= position) index = i;
    else break;
  }
  if (index === activeLine && !jump) return;
  activeLine = index;
  for (const p of lyricsView.querySelectorAll('p[data-line]')) {
    const line = Number(p.dataset.line);
    p.classList.toggle('active', line === index);
    p.classList.toggle('past', line < index);
  }
  if (index < 0 || Date.now() - userScrolledAt < MANUAL_SCROLL_PAUSE) return;
  const element = lyricsView.querySelector(`p[data-line="${index}"]`);
  if (element) {
    lyricsView.scrollTo({
      top: element.offsetTop - lyricsView.clientHeight * 0.35,
      behavior: jump ? 'auto' : 'smooth',
    });
  }
}

lyricsView.addEventListener('click', (e) => {
  const line = e.target.closest('p[data-time]');
  if (line) {
    userScrolledAt = 0;
    player.seek(Number(line.dataset.time));
  }
});
for (const type of ['wheel', 'touchmove']) {
  lyricsView.addEventListener(type, () => { userScrolledAt = Date.now(); }, { passive: true });
}

// endregion

function render() {
  if (!open) return;
  const track = player.current;
  if (!track) {
    closeNowPlaying();
    return;
  }
  $('[data-context]', root).textContent = player.context?.title || 'התור שלך';
  const art = $('[data-art]', root);
  const thumb = picture(track.thumb);
  if (art.dataset.src !== thumb) {
    art.dataset.src = thumb;
    art.onerror = () => { art.onerror = null; art.src = '/icon.svg'; };
    art.src = thumb || '/icon.svg';
    dominantColor(track.thumb).then((color) => {
      if (player.current === track) root.style.setProperty('--np-color', color || '#3b2f7a');
    });
  }
  $('[data-title]', root).textContent = track.title;
  const artist = $('[data-artist]', root);
  artist.textContent = track.artist;
  artist.href = track.artist ? `#/search?q=${encodeURIComponent(leadArtist(track.artist))}` : '#';

  const liked = store.isLiked(track.videoId);
  const like = $('[data-like]', root);
  like.classList.toggle('on', liked);
  like.innerHTML = icon(liked ? 'heart-fill' : 'heart');

  const toggle = $('[data-toggle]', root);
  toggle.innerHTML = icon(player.playing ? 'pause' : 'play');
  toggle.classList.toggle('buffering', player.buffering && player.playing);
  $('[data-shuffle]', root).classList.toggle('on', player.shuffle);
  $('[data-repeat]', root).classList.toggle('on', player.repeat !== 'off');

  const local = isLocal(track.videoId);
  const saved = local || store.isSaved(track.videoId);
  const download = $('[data-download]', root);
  download.classList.toggle('on', saved);
  download.disabled = saved;
  $('[data-download-label]', root).textContent = local ? 'קובץ מהמכשיר' : saved ? 'שמור במכשיר' : 'שמור לאופליין';
  const mute = $('[data-np-mute]', root);
  mute.innerHTML = icon(player.volume === 0 ? 'volume-off' : 'volume');
  mute.classList.toggle('muted-volume', player.volume === 0);
  $('[data-np-volume]', root).value = Math.round(player.volume * 100);
  paintRange($('[data-np-volume]', root));
  renderSleep();
  if (lyricsMode) loadLyrics();
}

function renderTime() {
  if (!open) return;
  const duration = player.duration || player.current?.duration || 0;
  $('[data-duration]', root).textContent = formatDuration(duration);
  renderSleep();
  highlightLyrics();
  if (seeking) return;
  $('[data-time]', root).textContent = formatDuration(player.position);
  $('[data-seek]', root).value = duration ? Math.round((player.position / duration) * 1000) : 0;
  paintRange($('[data-seek]', root));
}

function renderSleep() {
  const chip = $('[data-sleep]', root);
  const minutes = player.sleepEndsAt ? Math.max(1, Math.ceil((player.sleepEndsAt - Date.now()) / 60000)) : 0;
  chip.classList.toggle('on', !!minutes);
  $('[data-sleep-label]', root).textContent = minutes ? `עוד ${minutes} דק׳` : 'טיימר שינה';
}

root.addEventListener('click', (e) => {
  const button = e.target.closest('button, a');
  if (!button) return;
  const track = player.current;
  if (button.matches('[data-close]')) closeNowPlaying();
  else if (button.matches('[data-toggle]')) player.toggle();
  else if (button.matches('[data-prev]')) player.prev();
  else if (button.matches('[data-next]')) player.next();
  else if (button.matches('[data-shuffle]')) player.toggleShuffle();
  else if (button.matches('[data-repeat]')) player.cycleRepeat();
  else if (button.matches('[data-like]') && track) store.toggleLike(track);
  else if (button.matches('[data-download]') && track) downloadTracks([track]);
  else if (button.matches('[data-queue]')) player.toggleQueuePanel(true);
  else if (button.matches('[data-lyrics]')) setLyricsMode(!lyricsMode);
  else if (button.matches('[data-np-mute]')) player.toggleMute();
  else if (button.matches('[data-artist]')) {
    e.preventDefault();
    const href = button.getAttribute('href');
    closeNowPlaying();
    if (href && href !== '#') setTimeout(() => { location.hash = href; }, 50);
  } else if (button.matches('[data-sleep]')) {
    const choose = (minutes, label) => ({ label, icon: 'moon', action: () => {
      player.setSleepTimer(minutes);
      if (minutes) toast(minutes === 'end' ? 'הניגון ייעצר בסוף השיר' : `הניגון ייעצר בעוד ${minutes} דקות`);
    } });
    openMenu(button, [
      choose(15, '15 דקות'), choose(30, '30 דקות'), choose(45, '45 דקות'), choose(60, 'שעה'),
      choose('end', 'בסוף השיר'),
      ...(player.sleepEndsAt ? ['-', { label: 'בטל טיימר', icon: 'close', action: () => player.setSleepTimer(0) }] : []),
    ], { title: 'טיימר שינה' });
  } else if (button.matches('[data-more]') && track) {
    const items = [{ label: 'הוסף לפלייליסט…', icon: 'plus', action: () => addToPlaylistDialog([track]) }];
    if (!isLocal(track.videoId)) items.push({ label: 'רדיו על בסיס השיר', icon: 'radio', action: () => startRadio(track) });
    items.push({ label: 'שתף', icon: 'share', action: () => shareTrack(track) });
    items.push({ label: 'הבאים בתור', icon: 'queue', action: () => player.toggleQueuePanel(true) });
    openMenu(button, items, { title: track.title });
  }
});

$('[data-np-volume]', root).addEventListener('input', (e) => player.setVolume(e.target.value / 100));

const seek = $('[data-seek]', root);
seek.addEventListener('input', () => {
  seeking = true;
  $('[data-time]', root).textContent = formatDuration((seek.value / 1000) * (player.duration || 0));
});
seek.addEventListener('change', () => {
  seeking = false;
  player.seek((seek.value / 1000) * (player.duration || 0));
});

// Swipe down on the artwork or header to close, like closing a sheet
root.addEventListener('touchstart', (e) => {
  touchStartY = e.target.closest('.np-art, .np-head') ? e.touches[0].clientY : null;
}, { passive: true });
root.addEventListener('touchend', (e) => {
  if (touchStartY !== null && e.changedTouches[0].clientY - touchStartY > 90) closeNowPlaying();
  touchStartY = null;
});

window.addEventListener('popstate', () => { if (open) closeNowPlaying({ fromHistory: true }); });
window.addEventListener('shirim:now-playing', openNowPlaying);
player.onChange(render);
player.onTime(renderTime);
store.on('likes', render);
store.on('library', render);
