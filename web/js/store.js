// Shared app state: server info, settings, library, likes, playlists, followed artists and the download queue.

import { api, friendlyError, native } from './api.js';
import { toast } from './ui.js';

const listeners = new Map();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export const isLocal = (videoId) => String(videoId || '').startsWith('local-');

/** Copy the newer phone app from the paired computer and open Android's installer. */
export async function installUpdate(button) {
  if (button) button.disabled = true;
  toast('מעתיק את הגרסה החדשה מהמחשב…', 'info', 4000);
  try {
    await api.post('/api/app/update');
    toast('אשרו את העדכון בחלון של אנדרואיד', 'info', 6000);
  } catch (error) {
    toast(friendlyError(error), 'error', 6000);
  } finally {
    if (button) button.disabled = false;
  }
}

/** The one track shape used across the UI and the player. */
export function asTrack(t) {
  return {
    videoId: t.videoId || t.video_id,
    title: t.title || '',
    artist: t.artist || '',
    album: t.album || '',
    duration: Number(t.duration) || 0,
    thumb: t.thumb || '',
  };
}

function fromLibrary(row) {
  return {
    ...asTrack(row),
    exists: row.exists,
    size: row.size || 0,
    format: row.format || '',
    source: row.source || 'download',
    addedAt: row.added_at || 0,
  };
}

export const store = {
  info: null,
  settings: null,
  library: [],
  libraryById: new Map(),
  likes: new Set(),
  follows: new Set(),
  followList: [],
  playlists: [],
  queue: null,

  on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => listeners.get(event).delete(fn);
  },

  emit(event, data) {
    for (const fn of listeners.get(event) || []) {
      try {
        fn(data);
      } catch (error) {
        console.error(error);
      }
    }
  },

  // region app

  async loadInfo() {
    this.info = await api.get('/api/info');
    this.emit('info', this.info);
    return this.info;
  },

  async loadSettings() {
    this.settings = await api.get('/api/settings');
    this.emit('settings', this.settings);
    return this.settings;
  },

  async saveSettings(changes) {
    this.settings = await api.post('/api/settings', changes);
    this.emit('settings', this.settings);
    return this.settings;
  },

  // endregion

  // region library (songs on this device)

  async loadLibrary() {
    this.library = (await api.get('/api/library')).map(fromLibrary);
    this.libraryById = new Map(this.library.map((t) => [t.videoId, t]));
    this.emit('library', this.library);
    return this.library;
  },

  isSaved(videoId) {
    return this.libraryById.get(videoId)?.exists === true;
  },

  // endregion

  // region collection

  async loadCollection() {
    const data = await api.get('/api/collection');
    this.likes = new Set(data.likes);
    this.followList = data.follows;
    this.follows = new Set(data.follows.map((f) => f.browseId));
    this.playlists = data.playlists;
    this.emit('likes');
    this.emit('follows');
    this.emit('playlists');
  },

  isLiked(videoId) {
    return this.likes.has(videoId);
  },

  async setLike(track, liked) {
    track = asTrack(track);
    const before = this.likes.has(track.videoId);
    if (liked) this.likes.add(track.videoId);
    else this.likes.delete(track.videoId);
    this.emit('likes');
    try {
      await api.post('/api/likes/set', { track, liked });
      const downloading = liked && this.settings?.auto_download_likes && !isLocal(track.videoId) && !this.isSaved(track.videoId);
      toast(liked ? (downloading ? 'נוסף לשירים שאהבתי ויישמר לאופליין' : 'נוסף לשירים שאהבתי') : 'הוסר מהשירים שאהבתי', 'info', 1800);
    } catch (error) {
      if (before) this.likes.add(track.videoId);
      else this.likes.delete(track.videoId);
      this.emit('likes');
      toast(friendlyError(error), 'error');
    }
  },

  toggleLike(track) {
    return this.setLike(track, !this.isLiked(track.videoId));
  },

  isFollowing(browseId) {
    return this.follows.has(browseId);
  },

  async setFollow(artist, followed) {
    if (followed) this.follows.add(artist.browseId);
    else this.follows.delete(artist.browseId);
    this.emit('follows');
    try {
      await api.post('/api/follows/set', { artist, followed });
      this.followList = await api.get('/api/follows');
    } catch (error) {
      if (followed) this.follows.delete(artist.browseId);
      else this.follows.add(artist.browseId);
      toast(friendlyError(error), 'error');
    }
    this.emit('follows');
  },

  async refreshPlaylists() {
    this.playlists = await api.get('/api/playlists');
    this.emit('playlists');
    return this.playlists;
  },

  async createPlaylist(name, tracks = []) {
    const created = await api.post('/api/playlists/create', { name, tracks: tracks.map(asTrack) });
    await this.refreshPlaylists();
    return created;
  },

  async addToPlaylist(id, tracks) {
    const { added } = await api.post('/api/playlists/add', { id, tracks: tracks.map(asTrack) });
    await this.refreshPlaylists();
    return added;
  },

  // endregion

  enqueue(tracks) {
    return api.post('/api/queue/add', { tracks: tracks.map(asTrack) });
  },

  startPolling() {
    if (this._polling) return;
    this._polling = true;
    pollQueue(this);
  },
};

async function pollQueue(state) {
  let version = -1;
  let lastFinished = null;
  let lastScan = null;
  let libraryTimer = null;
  let lastLibraryLoad = 0;
  let lastNativeState = '';

  // With hundreds of songs finishing, reload the library at most every few seconds
  const refreshLibrary = () => {
    if (libraryTimer) return;
    const wait = Math.max(0, 3000 - (Date.now() - lastLibraryLoad));
    libraryTimer = setTimeout(async () => {
      libraryTimer = null;
      lastLibraryLoad = Date.now();
      try {
        await state.loadLibrary();
      } catch {
        // retried on the next change
      }
    }, wait);
  };

  // The Android app keeps itself alive with a notification while downloads run
  const reportToNative = ({ counts, paused }) => {
    const active = paused ? 0 : counts.queued + counts.running;
    const done = counts.done + counts.skipped + counts.error;
    const key = `${active}/${done}`;
    if (key === lastNativeState) return;
    lastNativeState = key;
    native.updateDownloads(active, done, active + done);
  };

  for (;;) {
    const started = Date.now();
    try {
      const snapshot = await api.get('/api/queue/state', { since: version });
      version = snapshot.version;
      state.queue = snapshot;
      state.emit('queue', snapshot);
      if (native) reportToNative(snapshot);

      const finished = snapshot.counts.done + snapshot.counts.skipped;
      if (lastFinished !== null && finished > lastFinished) refreshLibrary();
      lastFinished = finished;

      const scan = snapshot.scan;
      if (scan) {
        state.emit('scan', scan);
        if (lastScan !== null && scan.finished_at !== lastScan && !scan.running) refreshLibrary();
        lastScan = scan.finished_at;
      }
      // Progress events arrive many times a second; cap the refresh rate
      await sleep(Math.max(0, 400 - (Date.now() - started)));
    } catch (error) {
      if (error.status === 401) state.emit('pin-required');
      await sleep(error.status === 401 ? 5000 : 2000);
    }
  }
}
