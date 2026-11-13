import { api, friendlyError } from '../api.js';
import { player } from '../player.js';
import { store } from '../store.js';
import { TrackList } from '../tracklist.js';
import { downloadTracks, playFrom, saveAsPlaylist } from '../tracks.js';
import { $, $$, esc, icon, cover, debounce } from '../ui.js';
import { errorBox, loading, shelf, tile } from './common.js';

const TABS = [
  ['all', 'הכל'],
  ['songs', 'שירים'],
  ['artists', 'זמרים'],
  ['albums', 'אלבומים'],
  ['playlists', 'פלייליסטים'],
];
const RELEASE_TYPES = { Album: 'אלבום', Single: 'סינגל', EP: 'EP' };
const HISTORY_KEY = 'shirim.searches';
const YOUTUBE_LINK = /^(https?:\/\/)?([\w-]+\.)?(youtube\.com|youtu\.be)\//i;

function recentSearches() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
  } catch {
    return [];
  }
}

function remember(query) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify([query, ...recentSearches().filter((q) => q !== query)].slice(0, 12)));
  } catch {
    // storage unavailable
  }
}

const artistTile = (a) => tile({ href: `#/artist/${encodeURIComponent(a.browseId)}`, thumb: a.thumb, title: a.name, subtitle: 'זמר', round: true });
const albumTile = (a) => tile({
  href: `#/album/${encodeURIComponent(a.browseId)}`, thumb: a.thumb, title: a.title,
  subtitle: [RELEASE_TYPES[a.type] || a.type, a.artist, a.year].filter(Boolean).join(' · '),
  play: `album|${a.browseId}|${a.title}`,
});
const playlistTile = (p) => tile({
  href: `#/ytplaylist/${encodeURIComponent(p.browseId)}`, thumb: p.thumb, title: p.title,
  subtitle: [p.author, p.count && `${p.count} שירים`].filter(Boolean).join(' · '),
});

function topCard(top) {
  if (!top) return '';
  if (top.type === 'artist') {
    return `<a class="top-card round" href="#/artist/${encodeURIComponent(top.browseId)}">
      ${cover(top.thumb)}<h3 class="ellipsis">${esc(top.name)}</h3><span class="type-pill">זמר</span>
      <button class="fab" data-play-artist="${esc(top.browseId)}" data-title="${esc(top.name)}" title="נגן">${icon('play')}</button></a>`;
  }
  if (top.type === 'album') {
    return `<a class="top-card" href="#/album/${encodeURIComponent(top.browseId)}">
      ${cover(top.thumb)}<h3 class="ellipsis">${esc(top.title)}</h3><span class="muted">${esc(top.artist)}</span> <span class="type-pill">אלבום</span></a>`;
  }
  if (top.type === 'playlist') {
    return `<a class="top-card" href="#/ytplaylist/${encodeURIComponent(top.browseId)}">
      ${cover(top.thumb)}<h3 class="ellipsis">${esc(top.title)}</h3><span class="type-pill">פלייליסט</span></a>`;
  }
  return `<div class="top-card" data-top-song>
    ${cover(top.thumb)}<h3 class="ellipsis">${esc(top.title)}</h3><span class="muted">${esc(top.artist)}</span> <span class="type-pill">שיר</span>
    <button class="fab" data-top-song-play title="נגן">${icon('play')}</button></div>`;
}

export function render(el, params, ctx) {
  let tab = TABS.some(([key]) => key === params.tab) ? params.tab : 'all';
  let token = 0;
  let lists = [];
  let suggestions = [];
  let activeSuggestion = -1;

  el.innerHTML = `
    <div class="search-top">
      <div class="search-box">
        ${icon('search')}
        <input class="input" id="q" type="search" autocomplete="off" enterkeyhint="search"
          placeholder="מה בא לך לשמוע? זמר, שיר, אלבום או קישור מיוטיוב" value="${esc(params.q || '')}">
        <div class="suggest" data-suggest hidden></div>
      </div>
      <div class="tabs" data-tabs>${TABS.map(([key, label]) => `<button class="tab ${key === tab ? 'active' : ''}" data-tab="${key}">${label}</button>`).join('')}</div>
    </div>
    <div data-results></div>`;

  const input = $('#q', el);
  const results = $('[data-results]', el);
  const tabsEl = $('[data-tabs]', el);
  const suggestEl = $('[data-suggest]', el);

  const destroyLists = () => {
    lists.forEach((list) => list.destroy());
    lists = [];
  };
  const mountList = (selector, options) => {
    const holder = $(selector, results);
    if (!holder) return null;
    const list = new TrackList(holder, options);
    lists.push(list);
    return list;
  };

  const browse = () => {
    const recent = recentSearches();
    const artists = store.followList;
    results.innerHTML = `
      ${recent.length ? `<div class="shelf-head" style="margin-top:18px"><h2>חיפושים אחרונים</h2><button class="btn btn-sm btn-ghost" data-clear-history>נקה</button></div>
        <div class="chips">${recent.map((q) => `<button class="tab" data-recent="${esc(q)}">${esc(q)}</button>`).join('')}</div>` : ''}
      ${artists.length ? shelf('זמרים שאתם עוקבים אחריהם', artists.map(artistTile).join('')) : ''}
      ${!recent.length && !artists.length ? `<div class="welcome"><h2>מה בא לכם לשמוע?</h2>
        <div class="muted">חפשו זמר, שיר או אלבום ותתחילו לשמוע מיד. אפשר גם להדביק קישור לשיר או לפלייליסט מיוטיוב.</div></div>` : ''}`;
  };

  const renderAll = (data, query) => {
    const hasTop = data.top || data.songs.length;
    results.innerHTML = `
      ${hasTop ? `<div class="top-grid">
        <section><h2 class="section-title">התוצאה המובילה</h2>${topCard(data.top || { type: 'song', ...data.songs[0] })}</section>
        <section><h2 class="section-title">שירים</h2><div data-songs></div></section>
      </div>` : ''}
      ${shelf('זמרים', data.artists.map(artistTile).join(''))}
      ${shelf('אלבומים', data.albums.map(albumTile).join(''))}
      ${shelf('פלייליסטים', data.playlists.map(playlistTile).join(''))}
      ${data.videos.length ? '<h2 class="section-title">סרטונים</h2><div data-videos></div>' : ''}
      ${!hasTop && !data.artists.length && !data.albums.length ? '<div class="empty">לא נמצאו תוצאות</div>' : ''}`;
    const context = { title: `חיפוש: ${query}`, key: `search:${query}` };
    const songs = data.top?.type === 'song' ? [data.top, ...data.songs.filter((s) => s.videoId !== data.top.videoId)] : data.songs;
    mountList('[data-songs]', { tracks: songs.slice(0, 5), context, album: false });
    mountList('[data-videos]', { tracks: data.videos, context, album: false });

    const topSong = data.top?.type === 'song' ? data.top : !data.top ? data.songs[0] : null;
    results.onclick = (e) => {
      const artistPlay = e.target.closest('[data-play-artist]');
      if (artistPlay) {
        e.preventDefault();
        const id = artistPlay.dataset.playArtist;
        playFrom(async () => (await api.get('/api/artist/songs', { id })).tracks, { title: artistPlay.dataset.title, key: `artist:${id}` });
        return;
      }
      if (topSong && e.target.closest('[data-top-song]')) {
        player.playTracks(songs, 0, context);
        return;
      }
      const albumPlay = e.target.closest('[data-play]');
      if (albumPlay) {
        e.preventDefault();
        const [, id, title] = albumPlay.dataset.play.split('|');
        playFrom(async () => (await api.get('/api/album', { id })).tracks, { title, key: `album:${id}` });
      }
    };
  };

  const renderKind = (kind, items, query) => {
    results.onclick = null;
    if (!items.length) {
      results.innerHTML = '<div class="empty">לא נמצאו תוצאות</div>';
      return;
    }
    if (kind === 'songs') {
      results.innerHTML = '<div data-list></div>';
      mountList('[data-list]', { tracks: items, context: { title: `חיפוש: ${query}`, key: `search:${query}` }, selectable: true });
      return;
    }
    const render = { artists: artistTile, albums: albumTile, playlists: playlistTile }[kind];
    results.innerHTML = `<div class="grid" style="margin-top:8px">${items.map(render).join('')}</div>`;
    results.onclick = (e) => {
      const albumPlay = e.target.closest('[data-play]');
      if (!albumPlay) return;
      e.preventDefault();
      const [, id, title] = albumPlay.dataset.play.split('|');
      playFrom(async () => (await api.get('/api/album', { id })).tracks, { title, key: `album:${id}` });
    };
  };

  const renderLink = (tracks, url) => {
    results.onclick = null;
    const context = { title: 'קישור מיוטיוב', key: `link:${url}` };
    results.innerHTML = `
      <div class="actions-bar" style="margin:8px 0 0;padding:8px 0;background:none">
        <button class="fab" data-link-play title="נגן">${icon('play')}</button>
        <b>${tracks.length === 1 ? 'שיר אחד בקישור' : `${tracks.length.toLocaleString('he-IL')} שירים בקישור`}</b>
        <span class="spacer"></span>
        ${tracks.length > 1 ? `<button class="btn btn-sm" data-link-save>${icon('plus')} שמור כפלייליסט</button>` : ''}
        <button class="btn btn-sm" data-link-download>${icon('download')} שמור לאופליין</button>
      </div>
      <div data-list></div>`;
    mountList('[data-list]', { tracks, context, album: false, selectable: tracks.length > 1, filter: tracks.length > 30 });
    $('[data-link-play]', results).onclick = () => player.playTracks(tracks, 0, context);
    $('[data-link-download]', results).onclick = () => downloadTracks(tracks);
    const save = $('[data-link-save]', results);
    if (save) save.onclick = () => saveAsPlaylist(tracks, 'פלייליסט מיוטיוב');
  };

  const run = async () => {
    hideSuggestions();
    const query = input.value.trim();
    history.replaceState(null, '', `#/search?${new URLSearchParams(query ? { q: query, tab } : { tab })}`);
    destroyLists();
    const mine = ++token;
    const isLink = YOUTUBE_LINK.test(query);
    tabsEl.hidden = isLink || !query;
    if (!query) {
      results.onclick = null;
      browse();
      return;
    }
    results.innerHTML = loading();
    try {
      if (isLink) {
        const tracks = await api.post('/api/resolve', { url: query });
        if (mine === token && ctx.alive) renderLink(tracks, query);
        return;
      }
      const data = tab === 'all' ? await api.get('/api/search/all', { q: query }) : await api.get('/api/search', { q: query, kind: tab });
      if (mine !== token || !ctx.alive) return;
      remember(query);
      if (tab === 'all') renderAll(data, query);
      else renderKind(tab, data, query);
    } catch (error) {
      if (mine === token && ctx.alive) results.innerHTML = errorBox(error);
    }
  };

  // region suggestions

  const hideSuggestions = () => {
    suggestEl.hidden = true;
    activeSuggestion = -1;
  };

  const showSuggestions = debounce(async () => {
    const query = input.value.trim();
    if (!query || YOUTUBE_LINK.test(query) || document.activeElement !== input) {
      hideSuggestions();
      return;
    }
    try {
      suggestions = await api.get('/api/suggest', { q: query });
    } catch {
      suggestions = [];
    }
    if (!ctx.alive || input.value.trim() !== query || document.activeElement !== input) return;
    activeSuggestion = -1;
    suggestEl.innerHTML = suggestions.map((s, i) => `<button data-suggestion="${i}">${icon('search')}<span class="ellipsis">${esc(s)}</span></button>`).join('');
    suggestEl.hidden = !suggestions.length;
  }, 150);

  const highlight = (index) => {
    activeSuggestion = index;
    $$('[data-suggestion]', suggestEl).forEach((button, i) => button.classList.toggle('active', i === index));
  };

  // endregion

  const runSoon = debounce(run, 600);
  input.addEventListener('input', () => {
    showSuggestions();
    runSoon();
  });
  input.addEventListener('keydown', (e) => {
    if (!suggestEl.hidden && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      highlight((activeSuggestion + step + suggestions.length) % suggestions.length);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (activeSuggestion >= 0) input.value = suggestions[activeSuggestion];
      run();
      input.blur();
    }
    if (e.key === 'Escape') hideSuggestions();
  });
  input.addEventListener('blur', () => setTimeout(hideSuggestions, 150));
  suggestEl.addEventListener('mousedown', (e) => e.preventDefault());
  suggestEl.addEventListener('click', (e) => {
    const button = e.target.closest('[data-suggestion]');
    if (!button) return;
    input.value = suggestions[Number(button.dataset.suggestion)];
    run();
    input.blur();
  });

  tabsEl.addEventListener('click', (e) => {
    const button = e.target.closest('[data-tab]');
    if (!button || button.dataset.tab === tab) return;
    tab = button.dataset.tab;
    for (const t of tabsEl.children) t.classList.toggle('active', t === button);
    run();
  });

  el.addEventListener('click', (e) => {
    const chip = e.target.closest('[data-recent]');
    if (chip) {
      input.value = chip.dataset.recent;
      run();
      return;
    }
    if (e.target.closest('[data-clear-history]')) {
      localStorage.removeItem(HISTORY_KEY);
      browse();
    }
  });

  ctx.onCleanup(() => {
    token++;
    destroyLists();
  });

  if (matchMedia('(min-width: 801px)').matches) input.focus();
  run();
}
