// Sharing songs, playlists, albums, artists and the monthly recap: the phone's share sheet, WhatsApp or a copy.

import { api, native } from './api.js';
import { isLocal, store } from './store.js';
import { customDialog, esc, icon, toast } from './ui.js';

const MAX_LINKED_SONGS = 50;  // YouTube's "watch_videos" link takes up to 50 songs
const MAX_LISTED_SONGS = 10;

export function shareTrack(track) {
  const text = `🎵 ${track.title}${track.artist ? ` – ${track.artist}` : ''}`;
  return share({
    title: track.title,
    text,
    url: isLocal(track.videoId) ? '' : `https://music.youtube.com/watch?v=${encodeURIComponent(track.videoId)}`,
  });
}

export function shareTracks(tracks, name) {
  const lines = tracks.slice(0, MAX_LISTED_SONGS).map((t, i) => `${i + 1}. ${t.title}${t.artist ? ` – ${t.artist}` : ''}`);
  if (tracks.length > MAX_LISTED_SONGS) lines.push(`ועוד ${tracks.length - MAX_LISTED_SONGS} שירים`);
  const ids = tracks.filter((t) => !isLocal(t.videoId)).slice(0, MAX_LINKED_SONGS).map((t) => t.videoId);
  return share({
    title: name,
    text: `🎧 ${name}\n${lines.join('\n')}${ids.length ? '\n\nלהאזנה:' : ''}`,
    url: ids.length ? `https://www.youtube.com/watch_videos?video_ids=${ids.join(',')}` : '',
  });
}

export const shareAlbum = (album) => share({
  title: album.title,
  text: `💿 ${album.title}${album.artist ? ` – ${album.artist}` : ''}`,
  url: `https://music.youtube.com/browse/${encodeURIComponent(album.browseId)}`,
});

export const shareArtist = (artist) => share({
  title: artist.name,
  text: `🎤 ${artist.name}`,
  url: `https://music.youtube.com/channel/${encodeURIComponent(artist.browseId)}`,
});

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    // clipboard API unavailable (plain http on another device); fall back to a hidden textarea
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.cssText = 'position:fixed;opacity:0';
  document.body.append(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}

async function openLink(url) {
  if (store.info?.actions?.includes('open_url')) {
    await api.post('/api/action', { name: 'open_url', url });
  } else if (!window.open(url, '_blank')) {
    location.href = url;
  }
}

export async function share({ title = '', text, url = '' }) {
  const message = url ? `${text}\n${url}` : text;
  // Inside the Android app: the system share sheet (WhatsApp, Telegram, messages…)
  if (typeof native?.share === 'function') {
    native.share(title, message);
    return;
  }
  if (navigator.share && window.isSecureContext && store.info?.remote) {
    try {
      await navigator.share({ title, text: message });
      return;
    } catch (error) {
      if (error.name === 'AbortError') return;
    }
  }
  customDialog(`
    <h3>שיתוף</h3>
    <p class="share-preview">${esc(message)}</p>
    <div class="row-flex">
      <button class="btn btn-primary" data-whatsapp>${icon('share')} שליחה בוואטסאפ</button>
      <button class="btn" data-copy>${icon('link')} העתקה</button>
    </div>
    <div class="actions"><button class="btn btn-ghost" data-cancel>סגור</button></div>`, (modal, close) => {
    modal.querySelector('[data-cancel]').onclick = () => close(null);
    modal.querySelector('[data-whatsapp]').onclick = () => {
      close(null);
      openLink(`https://wa.me/?text=${encodeURIComponent(message)}`).catch(() => toast('לא ניתן לפתוח את וואטסאפ', 'error'));
    };
    modal.querySelector('[data-copy]').onclick = async () => {
      await copyText(message);
      close(null);
      toast('הועתק. אפשר להדביק בכל אפליקציה.');
    };
  });
}
