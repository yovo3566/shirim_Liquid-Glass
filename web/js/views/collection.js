// Song collections: albums, your playlists, liked songs, mixes and YouTube playlists.

import { api, friendlyError } from '../api.js';
import { player } from '../player.js';
import { shareAlbum, shareTracks } from '../share.js';
import { store } from '../store.js';
import { TrackList } from '../tracklist.js';
import { addToPlaylistDialog, downloadTracks, playFrom, saveAsPlaylist } from '../tracks.js';
import { $, esc, icon, confirmDialog, dominantColor, openMenu, promptDialog, songCount, toast, totalDuration } from '../ui.js';
import { canExportToUsb, exportToUsb } from '../usb.js';
import { bindPlayButton, errorBox, hero, loading } from './common.js';

function meta(parts, tracks) {
  return [...parts.filter(Boolean).map((p) => esc(p)), tracks.length ? `${songCount(tracks.length)}, ${totalDuration(tracks)}` : ''].filter(Boolean).join(' · ');
}

/**
 * Shared layout: header, play/shuffle/download/more buttons and the song list. Returns the TrackList.
 * config: kind, title, metaParts, image | thumbs | liked, key, tracks, listOptions, menuItems, extraActions, editTitle
 */
function renderCollection(el, ctx, config) {
  const context = { title: config.title, key: config.key };
  el.innerHTML = `
    ${hero({ kind: config.kind, title: config.title, meta: meta(config.metaParts || [], config.tracks), image: config.image, thumbs: config.thumbs, liked: config.liked })}
    <div class="actions-bar">
      <button class="fab play-lg" data-play title="נגן"></button>
      <button class="icon-btn lg" data-shuffle title="ערבוב">${icon('shuffle')}</button>
      <button class="icon-btn lg" data-download title="שמור הכל לאופליין">${icon('download')}</button>
      ${config.extraActions || ''}
      <button class="icon-btn lg" data-more title="עוד">${icon('more')}</button>
    </div>
    <div data-list></div>
    <div data-below></div>`;

  if (config.image) {
    dominantColor(config.image).then((color) => { if (color && ctx.alive) el.style.setProperty('--hero-color', color); });
  } else if (config.liked) {
    el.style.setProperty('--hero-color', '#4a2bff');
  }
  if (config.editTitle) {
    const title = $('[data-hero-title]', el);
    title.classList.add('editable');
    title.title = 'שינוי שם';
    title.onclick = config.editTitle;
  }

  const list = new TrackList($('[data-list]', el), {
    tracks: config.tracks, context, filter: config.tracks.length > 30, selectable: true, ...config.listOptions,
  });
  ctx.onCleanup(() => list.destroy());

  bindPlayButton($('[data-play]', el), ctx, config.key, () => playFrom(list.tracks, context));
  $('[data-shuffle]', el).onclick = () => playFrom(list.tracks, context, { shuffle: true });
  $('[data-download]', el).onclick = () => downloadTracks(list.tracks);
  $('[data-more]', el).onclick = (e) => openMenu(e.currentTarget, [
    { label: 'הוסף לתור', icon: 'queue', action: () => player.addToQueue(list.tracks) },
    { label: 'הוסף לפלייליסט…', icon: 'plus', action: () => addToPlaylistDialog(list.tracks) },
    ...(canExportToUsb() ? [{ label: 'העתקה לדיסק און קי', icon: 'usb', action: () => exportToUsb(list.tracks, $('[data-hero-title]', el).textContent) }] : []),
    { label: 'שתף', icon: 'share', action: () => (config.share ? config.share() : shareTracks(list.tracks, $('[data-hero-title]', el).textContent)) },
    ...(config.menuItems || []),
  ], { title: config.title });

  list.updateMeta = (parts) => { $('[data-hero-meta]', el).innerHTML = meta(parts, list.tracks); };
  return list;
}

async function load(el, ctx, loader) {
  el.innerHTML = loading();
  try {
    const data = await loader();
    return ctx.alive ? data : null;
  } catch (error) {
    if (ctx.alive) el.innerHTML = errorBox(error);
    return null;
  }
}

export async function renderAlbum(el, params, ctx) {
  const album = await load(el, ctx, () => api.get('/api/album', { id: params.id }));
  if (!album) return;
  renderCollection(el, ctx, {
    kind: 'אלבום', title: album.title, metaParts: [album.artist, album.year], image: album.thumb,
    key: `album:${album.browseId}`, tracks: album.tracks,
    listOptions: { numbered: true, thumbs: false, album: false },
    share: () => shareAlbum(album),
    menuItems: [{ label: 'שמור כפלייליסט', icon: 'library', action: () => saveAsPlaylist(album.tracks, album.title) }],
  });
}

export async function renderMix(el, params, ctx) {
  const tracks = await load(el, ctx, () => api.get('/api/mix', { id: params.id }));
  if (!tracks) return;
  const title = params.title || 'מיקס';
  renderCollection(el, ctx, {
    kind: 'מיקס', title, metaParts: ['נבחרו בשבילך'], thumbs: tracks.slice(0, 4).map((t) => t.thumb),
    key: `mix:${params.id}`, tracks, listOptions: { album: false },
    menuItems: [{ label: 'שמור כפלייליסט', icon: 'library', action: () => saveAsPlaylist(tracks, title) }],
  });
}

export async function renderYtPlaylist(el, params, ctx) {
  const playlist = await load(el, ctx, () => api.get('/api/ytplaylist', { id: params.id }));
  if (!playlist) return;
  renderCollection(el, ctx, {
    kind: 'פלייליסט', title: playlist.title, metaParts: [playlist.author], image: playlist.thumb,
    key: `ytplaylist:${playlist.browseId}`, tracks: playlist.tracks,
    menuItems: [{ label: 'שמור לספרייה שלך', icon: 'library', action: () => saveAsPlaylist(playlist.tracks, playlist.title) }],
  });
}

export async function renderLiked(el, params, ctx) {
  const likes = await load(el, ctx, () => api.get('/api/likes'));
  if (!likes) return;
  const autoDownload = !!store.settings?.auto_download_likes;
  const list = renderCollection(el, ctx, {
    kind: 'פלייליסט', title: 'שירים שאהבתי', liked: true, key: 'liked', tracks: likes,
    listOptions: { emptyText: 'שירים שתסמנו בלב יופיעו כאן' },
    extraActions: `<label class="row-flex muted small" title="שירים שתסמנו בלב יישמרו גם לאופליין">
      <span class="switch"><input type="checkbox" data-auto ${autoDownload ? 'checked' : ''}><span></span></span>שמירה אוטומטית לאופליין</label>`,
  });

  $('[data-auto]', el).addEventListener('change', async (e) => {
    try {
      await store.saveSettings({ auto_download_likes: e.target.checked });
      if (e.target.checked && list.tracks.length) downloadTracks(list.tracks);
    } catch (error) {
      e.target.checked = !e.target.checked;
      toast(friendlyError(error), 'error');
    }
  });

  // Unliking a song here removes it from the list right away
  ctx.onCleanup(store.on('likes', async () => {
    try {
      const fresh = await api.get('/api/likes');
      if (!ctx.alive) return;
      list.setTracks(fresh);
      list.updateMeta([]);
    } catch {
      // keep the current list
    }
  }));
}

export async function renderPlaylist(el, params, ctx) {
  const id = Number(params.id);
  const playlist = await load(el, ctx, () => api.get('/api/playlists/get', { id }));
  if (!playlist) return;

  const reload = async () => {
    const fresh = await api.get('/api/playlists/get', { id });
    if (!ctx.alive) return;
    list.setTracks(fresh.tracks);
    list.updateMeta([]);
    store.refreshPlaylists();
    loadRecommendations();
  };

  const rename = async () => {
    const name = await promptDialog({ title: 'שינוי שם', value: $('[data-hero-title]', el).textContent, confirmText: 'שמור' });
    if (!name?.trim()) return;
    try {
      await api.post('/api/playlists/rename', { id, name: name.trim() });
      $('[data-hero-title]', el).textContent = name.trim();
      store.refreshPlaylists();
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  };

  const remove = async () => {
    const ok = await confirmDialog({ title: `למחוק את "${playlist.name}"?`, message: 'השירים עצמם לא יימחקו.', confirmText: 'מחק', danger: true });
    if (!ok) return;
    await api.post('/api/playlists/delete', { id });
    await store.refreshPlaylists();
    location.hash = '#/library';
  };

  const list = renderCollection(el, ctx, {
    kind: 'פלייליסט', title: playlist.name, thumbs: playlist.tracks.map((t) => t.thumb).slice(0, 4),
    key: `playlist:${id}`, tracks: playlist.tracks, editTitle: rename,
    listOptions: {
      emptyText: 'הפלייליסט ריק. אפשר להוסיף שירים מכל מקום בתפריט ⋯ של השיר.',
      onRemove: async (track) => {
        await api.post('/api/playlists/remove', { id, itemIds: [track.itemId] });
        reload();
      },
      onMove: async (track, index) => {
        await api.post('/api/playlists/move', { id, itemId: track.itemId, index });
        reload();
      },
    },
    menuItems: [
      '-',
      { label: 'שינוי שם', icon: 'edit', action: rename },
      { label: 'מחיקת הפלייליסט', icon: 'trash', danger: true, action: remove },
    ],
  });

  // "Recommended" songs to add, based on the playlist's last song
  const below = $('[data-below]', el);
  let recommendationsFor = null;
  const loadRecommendations = async () => {
    const seed = [...list.tracks].reverse().find((t) => !t.videoId.startsWith('local-'));
    if (!seed || seed.videoId === recommendationsFor) {
      if (!seed) below.innerHTML = '';
      return;
    }
    recommendationsFor = seed.videoId;
    try {
      const present = new Set(list.tracks.map((t) => t.videoId));
      const tracks = (await api.get('/api/radio', { videoId: seed.videoId })).filter((t) => !present.has(t.videoId)).slice(0, 10);
      if (!ctx.alive || !tracks.length) return;
      below.innerHTML = `<h2 class="section-title">מומלצים לפלייליסט</h2><div class="muted small" style="margin-top:-8px;margin-bottom:8px">לפי השירים שכבר בפלייליסט</div>
        ${tracks.map((t, i) => `
          <div class="row-track" data-rec="${i}">
            <img class="t-img" src="${esc(t.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">
            <div class="t-main"><div class="t-title ellipsis">${esc(t.title)}</div><div class="t-sub ellipsis">${esc(t.artist)}</div></div>
            <button class="btn btn-sm btn-outline" data-add="${i}">הוסף</button>
          </div>`).join('')}`;
      below.onclick = async (e) => {
        const add = e.target.closest('[data-add]');
        const row = e.target.closest('[data-rec]');
        if (add) {
          add.disabled = true;
          await store.addToPlaylist(id, [tracks[Number(add.dataset.add)]]);
          reload();
        } else if (row) {
          player.playTracks(tracks, Number(row.dataset.rec), { title: 'מומלצים', key: `rec:${id}` });
        }
      };
    } catch {
      below.innerHTML = '';
    }
  };
  loadRecommendations();
}
