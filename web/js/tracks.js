// Actions on songs shared by every screen: play, download, add to playlists, radio and the song menu.

import { api, friendlyError } from './api.js';
import { player } from './player.js';
import { shareTrack } from './share.js';
import { asTrack, isLocal, store } from './store.js';
import { esc, icon, mosaic, openMenu, toast, confirmDialog, promptDialog, customDialog } from './ui.js';

const AVERAGE_SONG_MB = 4;

const formatSize = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);

export const leadArtist = (artist) => (artist || '').split(', ')[0];

export async function downloadTracks(tracks, { confirmOver = 60 } = {}) {
  const candidates = tracks.filter((t) => t.videoId && !isLocal(t.videoId));
  const fresh = candidates.filter((t) => !store.isSaved(t.videoId));
  const already = tracks.length - fresh.length;
  if (!fresh.length) {
    toast(tracks.length === 1 ? 'השיר כבר שמור במכשיר' : 'כל השירים כבר שמורים במכשיר');
    return 0;
  }
  if (fresh.length > confirmOver) {
    const lines = [
      'השירים יישמרו באופליין ברקע, כמה במקביל.',
      `נפח משוער: כ-${formatSize(fresh.length * AVERAGE_SONG_MB)}`,
    ];
    if (already) lines.push(`${already} שירים כבר שמורים ולא יורדו שוב.`);
    const ok = await confirmDialog({
      title: `לשמור ${fresh.length.toLocaleString('he-IL')} שירים לאופליין?`,
      message: lines.join('\n'),
      confirmText: 'שמור',
    });
    if (!ok) return 0;
  }
  try {
    const { added } = await store.enqueue(fresh);
    if (!added) toast('השירים כבר בתור ההורדות');
    else toast(added === 1 ? 'השיר נשמר לאופליין ברקע' : `${added.toLocaleString('he-IL')} שירים נשמרים לאופליין ברקע`);
    return added;
  } catch (error) {
    toast(friendlyError(error), 'error');
    return 0;
  }
}

/** Play a list (or what an async loader returns); shuffle turns shuffle mode on first. */
export async function playFrom(loader, context, { shuffle = false } = {}) {
  try {
    const tracks = typeof loader === 'function' ? await loader() : loader;
    if (!tracks?.length) {
      toast('אין שירים לניגון', 'error');
      return;
    }
    if (shuffle) player.setShuffle(true);
    player.playTracks(tracks, shuffle ? Math.floor(Math.random() * tracks.length) : 0, context);
  } catch (error) {
    toast(friendlyError(error), 'error');
  }
}

export async function startRadio(track) {
  track = asTrack(track);
  if (isLocal(track.videoId)) return;
  toast('מכין רדיו…', 'info', 1500);
  try {
    const tracks = await api.get('/api/radio', { videoId: track.videoId });
    player.playTracks([track, ...tracks], 0, { title: `רדיו: ${track.title}`, key: `radio:${track.videoId}` });
  } catch (error) {
    toast(friendlyError(error), 'error');
  }
}

export async function createPlaylistFlow(tracks = [], suggestedName = '') {
  const name = await promptDialog({
    title: 'פלייליסט חדש',
    value: suggestedName || `הפלייליסט שלי #${store.playlists.length + 1}`,
    confirmText: 'יצירה',
  });
  if (name === null) return null;
  try {
    const created = await store.createPlaylist(name.trim() || 'פלייליסט חדש', tracks);
    toast(tracks.length ? `נוצר פלייליסט עם ${tracks.length} שירים` : 'הפלייליסט נוצר');
    return created;
  } catch (error) {
    toast(friendlyError(error), 'error');
    return null;
  }
}

export async function saveAsPlaylist(tracks, name) {
  const created = await createPlaylistFlow(tracks, name);
  if (created) location.hash = `#/playlist/${created.id}`;
}

export function addToPlaylistDialog(tracks) {
  tracks = tracks.map(asTrack);
  return customDialog(`
    <h3>הוספה לפלייליסט</h3>
    <div class="picker">
      <button class="picker-item" data-new><span class="picker-img new">${icon('plus')}</span><span class="grow">פלייליסט חדש</span></button>
      ${store.playlists.map((p) => `
        <button class="picker-item" data-id="${p.id}">
          ${mosaic(p.thumbs, 'picker-img')}<span class="grow ellipsis">${esc(p.name)}</span><span class="muted small">${p.count}</span>
        </button>`).join('')}
    </div>
    <div class="actions"><button class="btn btn-ghost" data-cancel>ביטול</button></div>`, (modal, close) => {
    modal.querySelector('.dialog').addEventListener('click', async (e) => {
      if (e.target.closest('[data-cancel]')) {
        close(null);
        return;
      }
      if (e.target.closest('[data-new]')) {
        close(null);
        const created = await createPlaylistFlow(tracks);
        if (created && tracks.length) toast(`נוסף ל"${created.name}"`);
        return;
      }
      const item = e.target.closest('[data-id]');
      if (!item) return;
      close(null);
      const id = Number(item.dataset.id);
      const name = store.playlists.find((p) => p.id === id)?.name || '';
      try {
        const added = await store.addToPlaylist(id, tracks);
        toast(added ? `נוסף ל"${name}"` : `כבר נמצא ב"${name}"`);
      } catch (error) {
        toast(friendlyError(error), 'error');
      }
    });
  });
}

/** Filtered mode: hide a song that slipped through, and offer to hide its performers too. */
async function reportTrack(track) {
  try {
    const { performers } = await api.post('/api/filter/report', { track: asTrack(track) });
    if (player.current?.videoId === track.videoId) player.next();
    toast('השיר הוסתר');
    for (const name of performers) {
      const hide = await confirmDialog({
        title: `להסתיר גם את כל השירים של ${name}?`,
        message: 'אפשר לבטל את זה בהגדרות, במצב מסונן.',
        confirmText: 'הסתר',
        cancelText: 'רק את השיר הזה',
        danger: true,
      });
      if (hide) await api.post('/api/filter/rule', { kind: 'artist', name, verdict: 'block' });
    }
  } catch (error) {
    toast(friendlyError(error), 'error');
  }
}

/** The ⋯ menu of a song. Options add screen-specific items (remove from playlist, reorder, delete file). */
export function trackMenu(anchor, track, { onRemove, onMoveUp, onMoveDown, extras = [] } = {}) {
  const local = isLocal(track.videoId);
  const saved = store.isSaved(track.videoId);
  const liked = store.isLiked(track.videoId);
  const items = [
    { label: 'הפעל הבא', icon: 'next', action: () => player.playNext([track]) },
    { label: 'הוסף לתור', icon: 'queue', action: () => player.addToQueue([track]) },
    { label: 'הוסף לפלייליסט…', icon: 'plus', action: () => addToPlaylistDialog([track]) },
    { label: liked ? 'הסר מהשירים שאהבתי' : 'הוסף לשירים שאהבתי', icon: liked ? 'heart-fill' : 'heart', action: () => store.toggleLike(track) },
  ];
  if (!local) {
    items.push({ label: 'רדיו על בסיס השיר', icon: 'radio', action: () => startRadio(track) });
    items.push({ label: saved ? 'שמור במכשיר' : 'שמור לאופליין', icon: saved ? 'saved' : 'download', disabled: saved, action: () => downloadTracks([track]) });
  }
  if (track.artist) {
    items.push({ label: 'חפש את הזמר', icon: 'user', action: () => { location.hash = `#/search?q=${encodeURIComponent(leadArtist(track.artist))}`; } });
  }
  items.push({ label: 'שתף', icon: 'share', action: () => shareTrack(track) });
  if (store.info?.filter?.active && !store.info.remote) {
    items.push({ label: 'לא מתאים (שירת נשים וכו׳): הסתר', icon: 'close', danger: true, action: () => reportTrack(track) });
  }
  if (onMoveUp || onMoveDown) {
    items.push('-');
    if (onMoveUp) items.push({ label: 'הזז למעלה', icon: 'up', action: onMoveUp });
    if (onMoveDown) items.push({ label: 'הזז למטה', icon: 'down', action: onMoveDown });
  }
  if (extras.length || onRemove) items.push('-');
  items.push(...extras);
  if (onRemove) items.push({ label: 'הסר מהפלייליסט', icon: 'trash', danger: true, action: onRemove });
  openMenu(anchor, items, { title: track.title });
}
