// Music player: play queue, shuffle/repeat, autoplay radio and a sleep timer, driving either the HTML <audio>
// element or the Android app's native player. Every song plays from /stream/<id>, which the app serves from
// the device when the song is there and straight from YouTube otherwise.

import { api, friendlyError, native } from './api.js';
import { asTrack, isLocal, store } from './store.js';
import { $, esc, cover, dominantColor, formatDuration, icon, paintRange, picture, toast } from './ui.js';

const STORAGE_KEY = 'shirim.player.v2';
const LEGACY_STORAGE_KEY = 'shirim.player.v1';
const MAX_SAVED_TRACKS = 1000;
const RADIO_BATCH = 25;
const MAX_CONSECUTIVE_ERRORS = 3;
const PREPARE_AHEAD = 3;

export const EQ_BANDS = [
  { frequency: 60, label: 'בס' },
  { frequency: 230, label: '230' },
  { frequency: 910, label: 'אמצע' },
  { frequency: 3600, label: '3.6K' },
  { frequency: 14000, label: 'גבוהים' },
];

export const EQ_PRESETS = {
  flat: { label: 'רגיל', gains: [0, 0, 0, 0, 0] },
  bass: { label: 'בס חזק', gains: [6, 4, 0, 0, 1] },
  car: { label: 'לרכב', gains: [5, 2, -1, 2, 4] },
  vocal: { label: 'שירה ברורה', gains: [-2, -1, 3, 4, 1] },
  treble: { label: 'גבוהים', gains: [0, 0, 0, 3, 6] },
  custom: { label: 'מותאם אישית', gains: null },
};

export const parseBands = (text) => {
  const gains = String(text || '').split(',').map(Number).map((g) => (Number.isFinite(g) ? Math.max(-12, Math.min(12, g)) : 0));
  return [...gains, 0, 0, 0, 0, 0].slice(0, 5);
};

const streamUrl = (track) => `/stream/${encodeURIComponent(track.videoId)}`;

/** A tenth of a second of silence as a WAV file (see HtmlEngine.primeMedia). */
function silentClipUrl() {
  const samples = 800;  // 0.1 s at 8 kHz, mono, 16-bit
  const view = new DataView(new ArrayBuffer(44 + samples * 2));
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 8000, true);
  view.setUint32(28, 16000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, samples * 2, true);
  return URL.createObjectURL(new Blob([view.buffer], { type: 'audio/wav' }));
}

// Safety net for the desktop sound effects: if they aren't producing sound, playback continues without them.
// A broken Web Audio path shows either as silence or as a song whose time stops moving.
const EFFECTS_CHECK_MS = 1000;
const EFFECTS_START_TIMEOUT_MS = 1500;
const EFFECTS_FROZEN_LIMIT_MS = 3000;
const EFFECTS_SILENCE_LIMIT_MS = 6000;
const STALL_RECOVERY_MS = 12000;

/**
 * Equalizer and volume levelling for the desktop app, built on Web Audio. Phones browsing to the computer
 * don't get it: mobile browsers suspend Web Audio when the screen locks, which would stop the music.
 */
class AudioGraph {
  constructor() {
    this.ctx = new AudioContext();
    const ctx = this.ctx;
    this.decks = new Map();
    this.input = ctx.createGain();
    // Measures the output, so a graph that plays silence can be detected and bypassed
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.samples = new Float32Array(this.analyser.fftSize);
    this.filters = EQ_BANDS.map(({ frequency }, i) => {
      const filter = ctx.createBiquadFilter();
      filter.type = i === 0 ? 'lowshelf' : i === EQ_BANDS.length - 1 ? 'highshelf' : 'peaking';
      filter.frequency.value = frequency;
      filter.Q.value = 1;
      return filter;
    });
    this.compressor = ctx.createDynamicsCompressor();
    this.makeup = ctx.createGain();
    // Catches peaks that the levelling gain would otherwise push into distortion
    this.limiter = ctx.createDynamicsCompressor();
    this.limiter.threshold.value = -1;
    this.limiter.knee.value = 0;
    this.limiter.ratio.value = 20;
    this.limiter.attack.value = 0.002;
    this.limiter.release.value = 0.1;
    let node = this.input;
    for (const filter of this.filters) {
      node.connect(filter);
      node = filter;
    }
    node.connect(this.compressor);
    this.compressor.connect(this.makeup);
    this.makeup.connect(this.limiter);
    this.limiter.connect(ctx.destination);
    this.limiter.connect(this.analyser);
  }

  /** Whether any sound is leaving the graph right now. */
  hasSignal() {
    this.analyser.getFloatTimeDomainData(this.samples);
    return this.samples.some((v) => v !== 0);
  }

  close() {
    this.ctx.close().catch(() => {});
  }

  /** The gain node a deck (an <audio> element) plays through. */
  attach(audio) {
    if (!this.decks.has(audio)) {
      const source = this.ctx.createMediaElementSource(audio);
      const gain = this.ctx.createGain();
      source.connect(gain);
      gain.connect(this.input);
      this.decks.set(audio, gain);
    }
    return this.decks.get(audio);
  }

  resume() {
    if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
  }

  apply(settings) {
    const now = this.ctx.currentTime;
    const gains = settings.eq_enabled ? parseBands(settings.eq_bands) : [0, 0, 0, 0, 0];
    this.filters.forEach((filter, i) => filter.gain.setTargetAtTime(gains[i], now, 0.05));
    const level = !!settings.normalize_volume;
    const c = this.compressor;
    c.threshold.value = level ? -28 : 0;
    c.knee.value = level ? 12 : 0;
    c.ratio.value = level ? 4 : 1;
    c.attack.value = 0.006;
    c.release.value = 0.3;
    this.makeup.gain.setTargetAtTime(level ? 2 : 1, now, 0.05);
  }
}

class HtmlEngine {
  constructor(audio, events) {
    this.events = events;
    // A second <audio> lets the next song start while the current one fades out
    const second = audio.cloneNode();
    second.removeAttribute('id');
    audio.after(second);
    this.decks = [audio, second];
    this.active = 0;
    this.list = [];
    this.index = -1;
    this.repeat = 'off';
    this.errors = 0;
    this.volume = 1;
    this.settings = null;
    this.crossfade = 0;
    this.fading = null;
    this.graph = null;
    // 'off' until a song plays with effects on; 'bypassed' when they failed and playback went on without them
    this.effects = 'off';
    this.effectsWatch = null;
    this.primer = null;
    for (const deck of this.decks) this.bindDeck(deck);
    this.primeMedia();
  }

  /**
   * The browser engine doesn't start loading a song in a minimized or covered window until that page has played
   * something. A short silent clip played right away (muted, so no click is needed) lifts that, so the first song
   * also starts when the window is hidden, e.g. from the keyboard's media keys.
   */
  primeMedia() {
    if (this.primer) return;
    const clip = new Audio(silentClipUrl());
    clip.muted = true;
    this.primer = clip;  // keeps the clip alive until it has played
    clip.play().then(() => {
      this.primed = true;
      URL.revokeObjectURL(clip.src);
      clip.removeAttribute('src');
      clip.load();
    }, (error) => {
      // Blocked, or the page started hidden: try again when the window is shown or clicked
      this.primeError = `${error.name}: ${error.message}`;
      this.primer = null;
      const retry = () => {
        document.removeEventListener('visibilitychange', onVisible);
        document.removeEventListener('pointerdown', retry, true);
        this.primeMedia();
      };
      const onVisible = () => { if (document.visibilityState === 'visible') retry(); };
      document.addEventListener('visibilitychange', onVisible);
      document.addEventListener('pointerdown', retry, true);
    });
  }

  get audio() {
    return this.decks[this.active];
  }

  bindDeck(deck) {
    const events = this.events;
    // Only the deck that's playing the current song reports; the fading one stays quiet
    const current = (fn) => (e) => { if (deck === this.audio) fn(e); };
    const time = current(() => {
      events.time(deck.currentTime, deck.duration || 0);
      this.maybeCrossfade();
    });
    deck.addEventListener('timeupdate', time);
    deck.addEventListener('durationchange', time);
    deck.addEventListener('play', current(() => events.playing(true)));
    deck.addEventListener('pause', current(() => events.playing(false)));
    deck.addEventListener('waiting', current(() => {
      events.buffering(true);
      this.watchStall(deck);
    }));
    deck.addEventListener('playing', current(() => {
      this.errors = 0;
      clearTimeout(deck.stallTimer);
      events.buffering(false);
    }));
    deck.addEventListener('canplay', current(() => events.buffering(false)));
    deck.addEventListener('ended', current(() => this.next(true)));
    deck.addEventListener('error', current(async () => {
      if (!deck.getAttribute('src')) return;
      events.buffering(false);
      const blocked = await events.error(this.list[this.index]);
      // Skip a song that can't play, but stop when YouTube blocks playback or several fail in a row
      if (!blocked && ++this.errors < MAX_CONSECUTIVE_ERRORS && this.index < this.list.length - 1) {
        this.load(this.index + 1, true);
      }
    }));
  }

  /** A stream that stops delivering (an expired address, a dropped connection) is reopened where it stopped. */
  watchStall(deck) {
    clearTimeout(deck.stallTimer);
    const position = deck.currentTime;
    deck.stallTimer = setTimeout(() => {
      if (deck !== this.audio || deck.paused || !deck.getAttribute('src') || deck.currentTime > position + 0.5) return;
      const src = deck.getAttribute('src');
      deck.src = src;
      deck.addEventListener('loadedmetadata', () => { deck.currentTime = position; }, { once: true });
      deck.play().catch(() => {});
    }, STALL_RECOVERY_MS);
  }

  // region sound effects (desktop app)

  setAudio(settings) {
    this.settings = settings;
    this.crossfade = Number(settings.crossfade) || 0;
    this.graph?.apply(settings);
  }

  wantsEffects() {
    const s = this.settings;
    return !!s && this.effects !== 'bypassed' && !store.info?.remote && typeof AudioContext === 'function'
      && (s.eq_enabled || s.normalize_volume || this.crossfade > 0);
  }

  /**
   * Routes a deck through the effects, creating them on the first played song: an AudioContext made during
   * the user's click starts running, while one made at startup may stay silent.
   */
  useEffects(deck) {
    if (!this.graph) {
      if (!this.wantsEffects()) return;
      try {
        this.graph = new AudioGraph();
        this.graph.apply(this.settings);
        this.effects = 'on';
      } catch (error) {
        this.effects = 'bypassed';
        console.warn('Sound effects unavailable', error);
        return;
      }
    }
    this.graph.attach(deck);
    this.graph.resume();
    this.watchEffects();
  }

  /** Checks that the effects produce sound; if not, plays on without them. */
  watchEffects() {
    if (this.effectsWatch) return;
    let notRunningSince = null;
    let silentSince = null;
    let frozenSince = null;
    let lastTime = null;
    this.effectsWatch = setInterval(() => {
      const graph = this.graph;
      const deck = this.audio;
      const now = Date.now();
      if (!graph) {
        clearInterval(this.effectsWatch);
        this.effectsWatch = null;
        return;
      }
      if (deck.paused || deck.readyState < 3 || this.fading) {
        notRunningSince = silentSince = frozenSince = lastTime = null;
        return;
      }
      if (graph.ctx.state !== 'running') {
        graph.resume();
        notRunningSince ??= now;
        if (now - notRunningSince > EFFECTS_START_TIMEOUT_MS) this.bypassEffects(`audio context ${graph.ctx.state}`);
        return;
      }
      notRunningSince = null;
      const moved = lastTime === null || deck.currentTime - lastTime > 0.2;
      lastTime = deck.currentTime;
      frozenSince = moved ? null : (frozenSince ?? now);
      silentSince = graph.hasSignal() ? null : (silentSince ?? now);
      if (frozenSince !== null && now - frozenSince > EFFECTS_FROZEN_LIMIT_MS) {
        this.bypassEffects('the song stopped moving');
      } else if (silentSince !== null && now - silentSince > EFFECTS_SILENCE_LIMIT_MS) {
        this.bypassEffects('no sound from the effects');
      }
    }, EFFECTS_CHECK_MS);
  }

  /** Swaps the decks for fresh <audio> elements outside Web Audio and continues the song without effects. */
  bypassEffects(reason) {
    const graph = this.graph;
    if (!graph) return;
    console.warn('Sound effects turned off:', reason);
    clearInterval(this.effectsWatch);
    this.effectsWatch = null;
    this.finishFade();
    this.graph = null;
    this.effects = 'bypassed';
    this.decks = this.decks.map((old) => {
      if (!graph.decks.has(old)) return old;
      const fresh = document.createElement('audio');
      fresh.preload = 'auto';
      if (old.id) fresh.id = old.id;
      fresh.volume = this.volume;
      const src = old.getAttribute('src');
      const position = old.currentTime;
      const wasPlaying = !old.paused;
      old.pause();
      old.removeAttribute('src');
      old.load();
      old.replaceWith(fresh);
      this.bindDeck(fresh);
      if (src) {
        fresh.src = src;
        fresh.addEventListener('loadedmetadata', () => { fresh.currentTime = position; }, { once: true });
        if (wasPlaying) fresh.play().catch(() => {});
      }
      return fresh;
    });
    graph.close();
  }

  resetGain(deck) {
    const gain = this.graph?.decks.get(deck);
    if (gain) {
      gain.gain.cancelScheduledValues(0);
      gain.gain.value = 1;
    }
  }

  // endregion

  maybeCrossfade() {
    const deck = this.audio;
    if (!this.crossfade || !this.graph || this.fading || deck.paused || this.repeat === 'one') return;
    const remaining = (deck.duration || 0) - deck.currentTime;
    if (!Number.isFinite(deck.duration) || deck.duration < this.crossfade * 3 || remaining > this.crossfade) return;
    let next = this.index + 1;
    if (next >= this.list.length) {
      if (this.repeat !== 'all' || !this.list.length) return;
      next = 0;
    }
    const ctx = this.graph.ctx;
    const seconds = Math.max(0.5, remaining);
    const outgoing = deck;
    const outGain = this.graph.attach(outgoing);
    outGain.gain.cancelScheduledValues(ctx.currentTime);
    outGain.gain.setValueAtTime(1, ctx.currentTime);
    outGain.gain.linearRampToValueAtTime(0.0001, ctx.currentTime + seconds);
    this.fading = { deck: outgoing, timer: setTimeout(() => this.finishFade(), seconds * 1000 + 150) };

    this.active = 1 - this.active;
    const inGain = this.graph.attach(this.audio);
    inGain.gain.cancelScheduledValues(ctx.currentTime);
    inGain.gain.setValueAtTime(0.0001, ctx.currentTime);
    inGain.gain.exponentialRampToValueAtTime(1, ctx.currentTime + seconds);
    this.load(next, true, 0, true);
  }

  finishFade() {
    if (!this.fading) return;
    const { deck, timer } = this.fading;
    this.fading = null;
    clearTimeout(timer);
    deck.pause();
    deck.removeAttribute('src');
    deck.load();
    this.resetGain(deck);
  }

  setPlaylist(list, index, autoplay, start = 0) {
    const same = this.list[this.index]?.videoId === list[index]?.videoId && this.audio.getAttribute('src');
    this.list = list;
    if (same) {
      this.index = index;
      // Tapping the song that is loaded but paused should resume it
      if (autoplay) this.play();
      return;
    }
    this.load(index, autoplay, start);
  }

  load(index, autoplay, start = 0, crossfading = false) {
    if (!crossfading) {
      this.finishFade();
      this.resetGain(this.audio);
    }
    this.index = index;
    const track = this.list[index];
    if (!track) return;
    const deck = this.audio;
    clearTimeout(deck.stallTimer);
    deck.volume = this.volume;
    deck.src = streamUrl(track);
    if (start) {
      deck.addEventListener('loadedmetadata', () => { deck.currentTime = start; }, { once: true });
    }
    this.events.index(index);
    if (autoplay) {
      this.useEffects(deck);
      this.events.buffering(true);
      deck.play().catch(() => {});
    }
  }

  play() {
    if (this.audio.getAttribute('src')) {
      this.useEffects(this.audio);
      this.audio.play().catch(() => {});
    } else if (this.list.length) {
      this.load(Math.max(0, this.index), true);
    }
  }

  pause() {
    this.finishFade();
    this.audio.pause();
  }

  seek(seconds) {
    if (!Number.isFinite(seconds)) return;
    this.finishFade();
    this.resetGain(this.audio);
    this.audio.currentTime = seconds;
  }

  setRepeat(mode) { this.repeat = mode; }

  setVolume(volume) {
    this.volume = volume;
    for (const deck of this.decks) deck.volume = volume;
  }

  next(auto = false) {
    if (auto && this.repeat === 'one') {
      this.audio.currentTime = 0;
      this.play();
    } else if (this.index < this.list.length - 1) {
      this.load(this.index + 1, true);
    } else if (this.repeat === 'all' && this.list.length) {
      this.load(0, true);
    } else if (auto) {
      this.events.ended();
    }
  }

  prev() {
    if (this.audio.currentTime > 3 || this.index <= 0) this.seek(0);
    else this.load(this.index - 1, true);
  }

  diagnostics() {
    return {
      engine: 'html',
      primed: this.primed || this.primeError || false,
      effects: this.effects,
      context: this.graph?.ctx.state || null,
      signal: this.graph && this.graph.ctx.state === 'running' ? this.graph.hasSignal() : null,
      decks: this.decks.map((d) => ({ src: d.getAttribute('src'), time: d.currentTime, paused: d.paused, ready: d.readyState, error: d.error?.code || null })),
    };
  }
}

class NativeEngine {
  constructor(bridge, events) {
    this.bridge = bridge;
    this.index = -1;
    // The Android app pushes playback state through this global callback
    window.shirimPlayerEvent = (payload) => {
      const state = typeof payload === 'string' ? JSON.parse(payload) : payload;
      if (state.queue) {
        // Android Auto or another controller replaced the queue
        this.index = state.index ?? 0;
        events.queue(state.queue.map((item) => ({
          videoId: item.id, title: item.title, artist: item.artist, album: item.album, thumb: item.art,
        })), this.index);
      } else if (state.index !== undefined && state.index !== this.index) {
        this.index = state.index;
        events.index(state.index);
      }
      if (state.playing !== undefined) events.playing(state.playing);
      if (state.position !== undefined) events.time(state.position / 1000, (state.duration || 0) / 1000);
      if (state.error) events.error(null);
    };
  }

  setPlaylist(list, index, autoplay, start = 0) {
    const items = list.map((t) => ({
      id: t.videoId,
      url: new URL(streamUrl(t), location.href).href,
      title: t.title,
      artist: t.artist,
      album: t.album,
      art: picture(t.thumb) ? new URL(t.thumb, location.href).href : '',
    }));
    this.index = index;
    this.bridge.playerSetPlaylist(JSON.stringify(items), index, !!autoplay, Math.round(start * 1000));
  }

  setAudio(settings) {
    // Older app versions don't have native effects
    if (typeof this.bridge.playerSetAudio !== 'function') return;
    this.bridge.playerSetAudio(JSON.stringify({
      eqEnabled: !!settings.eq_enabled,
      eqBands: parseBands(settings.eq_bands),
      normalize: !!settings.normalize_volume,
      crossfade: Number(settings.crossfade) || 0,
    }));
  }

  diagnostics() {
    return { engine: 'native' };
  }

  play() { this.bridge.playerPlay(); }
  pause() { this.bridge.playerPause(); }
  seek(seconds) { this.bridge.playerSeek(Math.round(seconds * 1000)); }
  next() { this.bridge.playerNext(); }
  prev() { this.bridge.playerPrev(); }
  setRepeat(mode) { this.bridge.playerSetRepeat(mode); }
  setVolume(volume) { this.bridge.playerSetVolume(volume); }
}

class Player {
  constructor() {
    this.queue = [];
    this.order = [];
    this.pos = -1;
    this.shuffle = false;
    this.repeat = 'off';
    this.playing = false;
    this.buffering = false;
    this.position = 0;
    this.duration = 0;
    this.volume = 1;
    this.mutedVolume = 0;  // what to go back to when unmuting
    this.silentWarned = false;
    this.context = null;
    this.seeking = false;
    this.sleepTimer = null;
    this.sleepEndsAt = 0;
    this.radioSeed = null;
    this.radioLoad = null;
    this.playRecorded = false;
    this.listeners = new Set();
    this.timeListeners = new Set();

    const events = {
      time: (position, duration) => {
        this.position = position;
        this.duration = duration || this.current?.duration || 0;
        this.maybeRecordPlay();
        this.renderTime();
        for (const fn of this.timeListeners) fn();
      },
      playing: (playing) => {
        this.playing = playing;
        if (!playing) this.buffering = false;
        this.renderButtons();
        if ('mediaSession' in navigator) navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
        this.notify();
      },
      buffering: (buffering) => {
        this.buffering = buffering;
        this.renderButtons();
      },
      index: (index) => {
        this.pos = index;
        this.position = 0;
        this.playRecorded = false;
        this.changed();
        this.afterTrackChange();
      },
      queue: (tracks, index) => this.adoptQueue(tracks, index),
      error: (track) => this.handleError(track || this.current),
      ended: () => this.continueWithRadio(true),
    };
    this.engine = native ? new NativeEngine(native, events) : new HtmlEngine($('#audio'), events);
    this.bindUi();
    this.bindMediaSession();
    this.restore();
    store.on('likes', () => this.renderLike());
    store.on('settings', (settings) => this.engine.setAudio(settings));
  }

  get current() {
    return this.pos >= 0 ? this.queue[this.order[this.pos]] || null : null;
  }

  onChange(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onTime(fn) {
    this.timeListeners.add(fn);
    return () => this.timeListeners.delete(fn);
  }

  notify() {
    for (const fn of this.listeners) fn(this.current);
  }

  orderedTracks() {
    return this.order.map((i) => this.queue[i]);
  }

  isContext(key) {
    return !!key && this.context?.key === key && !!this.current;
  }

  // region queue

  playTracks(tracks, startIndex = 0, context = null) {
    const list = tracks.map(asTrack).filter((t) => t.videoId);
    if (!list.length) {
      toast('אין שירים לניגון', 'error');
      return;
    }
    this.queue = list;
    this.context = context;
    this.radioSeed = null;
    this.makeOrder(Math.min(Math.max(0, startIndex), list.length - 1));
    this.sync(true);
  }

  /** Mirror a queue that was set outside the app's UI (Android Auto) without sending it back. */
  adoptQueue(tracks, index) {
    this.queue = tracks.map(asTrack);
    this.order = this.queue.map((_, i) => i);
    this.pos = Math.min(Math.max(0, index), this.queue.length - 1);
    this.context = { title: 'Android Auto', key: 'external' };
    this.radioSeed = null;
    this.playRecorded = false;
    this.changed();
  }

  setShuffle(shuffle) {
    this.shuffle = shuffle;
    this.renderButtons();
    this.notify();
  }

  playNext(tracks) {
    tracks = tracks.map(asTrack);
    if (!this.current) return this.playTracks(tracks);
    const first = this.queue.length;
    this.queue.push(...tracks);
    this.order.splice(this.pos + 1, 0, ...tracks.map((_, i) => first + i));
    this.sync(this.playing, this.position);
    toast(tracks.length === 1 ? 'יתנגן הבא בתור' : `${tracks.length} שירים יתנגנו הבאים בתור`);
  }

  addToQueue(tracks) {
    tracks = tracks.map(asTrack);
    if (!this.current) return this.playTracks(tracks);
    this.appendTracks(tracks);
    toast(tracks.length === 1 ? 'נוסף לתור' : `נוספו ${tracks.length} שירים לתור`);
  }

  appendTracks(tracks) {
    const first = this.queue.length;
    this.queue.push(...tracks);
    this.order.push(...tracks.map((_, i) => first + i));
    this.sync(this.playing, this.position);
  }

  jumpTo(orderPos) {
    this.pos = orderPos;
    this.engine.setPlaylist(this.orderedTracks(), this.pos, true);
    this.changed();
  }

  removeAt(orderPos) {
    if (orderPos === this.pos) return;
    this.order.splice(orderPos, 1);
    if (orderPos < this.pos) this.pos -= 1;
    this.sync(this.playing, this.position);
  }

  makeOrder(startIndex) {
    const indices = this.queue.map((_, i) => i);
    if (this.shuffle) {
      indices.splice(startIndex, 1);
      shuffleInPlace(indices);
      this.order = [startIndex, ...indices];
      this.pos = 0;
    } else {
      this.order = indices;
      this.pos = startIndex;
    }
  }

  sync(autoplay, start = 0) {
    this.engine.setPlaylist(this.orderedTracks(), this.pos, autoplay, start);
    this.engine.setRepeat(this.repeat);
    this.changed();
  }

  // endregion

  // region transport

  toggle() {
    if (!this.current) return;
    if (this.playing) this.engine.pause();
    else this.engine.play();
  }

  pause() { this.engine.pause(); }
  next() { this.engine.next(); }
  prev() { this.engine.prev(); }
  seek(seconds) { this.engine.seek(seconds); }

  toggleShuffle() {
    this.shuffle = !this.shuffle;
    if (this.current) {
      this.makeOrder(this.order[this.pos]);
      this.sync(this.playing, this.position);
    } else {
      this.changed();
    }
  }

  cycleRepeat() {
    this.repeat = { off: 'all', all: 'one', one: 'off' }[this.repeat];
    this.engine.setRepeat(this.repeat);
    this.changed();
  }

  setSleepTimer(minutes) {
    clearTimeout(this.sleepTimer);
    this.sleepTimer = null;
    this.sleepEndsAt = 0;
    if (minutes === 'end') minutes = Math.max(0.05, ((this.duration || 0) - this.position) / 60);
    if (minutes) {
      this.sleepEndsAt = Date.now() + minutes * 60000;
      this.sleepTimer = setTimeout(() => {
        this.engine.pause();
        this.sleepTimer = null;
        this.sleepEndsAt = 0;
        this.notify();
        toast('טיימר השינה עצר את הניגון');
      }, minutes * 60000);
    }
    this.notify();
  }

  // endregion

  // region background work

  afterTrackChange() {
    const upcoming = this.order.slice(this.pos, this.pos + PREPARE_AHEAD)
      .map((i) => this.queue[i])
      .filter((t) => t && !isLocal(t.videoId) && !store.isSaved(t.videoId))
      .map((t) => t.videoId);
    if (upcoming.length) api.post('/api/stream/prepare', { videoIds: upcoming }).catch(() => {});
    if (this.pos >= this.order.length - 2) this.continueWithRadio(false);
    const track = this.current;
    if (track) {
      dominantColor(track.thumb).then((color) => {
        if (color && this.current === track) document.documentElement.style.setProperty('--track-color', color);
      });
    }
  }

  /** Autoplay: when the queue is about to run out, keep going with similar songs. */
  continueWithRadio(startNow) {
    const seed = [...this.orderedTracks()].reverse().find((t) => !isLocal(t.videoId));
    if (!seed || store.settings?.autoplay === false || this.repeat === 'all') return;
    if (this.radioSeed !== seed.videoId) {
      this.radioSeed = seed.videoId;
      this.radioLoad = this.loadRadio(seed);
    }
    if (startNow) {
      this.radioLoad.then(() => {
        if (this.pos < this.order.length - 1) this.jumpTo(this.pos + 1);
      });
    }
  }

  async loadRadio(seed) {
    try {
      const tracks = await api.get('/api/radio', { videoId: seed.videoId });
      const known = new Set(this.queue.map((t) => t.videoId));
      const fresh = tracks.map(asTrack).filter((t) => t.videoId && !known.has(t.videoId)).slice(0, RADIO_BATCH);
      if (fresh.length) this.appendTracks(fresh);
    } catch {
      // offline or blocked: playback simply ends
    }
  }

  maybeRecordPlay() {
    const track = this.current;
    if (this.playRecorded || !track || !this.playing) return;
    if (this.position >= Math.min(10, (this.duration || 20) / 2)) {
      this.playRecorded = true;
      api.post('/api/plays', { track }).catch(() => {});
    }
  }

  async handleError(track) {
    if (!track) return false;
    let result = null;
    if (!isLocal(track.videoId)) {
      try {
        result = await api.post('/api/stream/prepare', { videoIds: [track.videoId], wait: true });
      } catch {
        // the server itself is unreachable; the generic message below covers it
      }
    }
    if (result?.blocked) {
      toast('יוטיוב חוסם זמנית את ההשמעה. אפשר להתחבר לחשבון יוטיוב בהגדרות או לנסות שוב מאוחר יותר.', 'error', 7000);
      this.engine.pause();
      return true;
    }
    const reason = result?.error ? `: ${friendlyError({ message: result.error })}` : '';
    toast(`לא ניתן לנגן את "${track.title}"${reason}`, 'error', 5000);
    return false;
  }

  // endregion

  // region rendering

  changed() {
    this.renderTrack();
    this.renderButtons();
    this.renderVolume();
    this.renderTime();
    this.renderQueue();
    this.updateMediaSession();
    this.save();
    this.notify();
  }

  bindUi() {
    $('#p-play').onclick = () => this.toggle();
    $('#pm-play').onclick = (e) => {
      e.stopPropagation();
      this.toggle();
    };
    $('#p-next').onclick = () => this.next();
    $('#p-prev').onclick = () => this.prev();
    $('#p-shuffle').onclick = () => this.toggleShuffle();
    $('#p-repeat').onclick = () => this.cycleRepeat();
    $('#p-like').onclick = (e) => {
      e.stopPropagation();
      if (this.current) store.toggleLike(this.current);
    };
    $('#p-queue').onclick = () => this.toggleQueuePanel();
    $('#p-expand').onclick = () => window.dispatchEvent(new CustomEvent('shirim:now-playing'));
    $('#p-lyrics').onclick = () => window.dispatchEvent(new CustomEvent('shirim:now-playing', { detail: { lyrics: true } }));
    $('[data-open-np]').addEventListener('click', (e) => {
      if (!e.target.closest('button')) window.dispatchEvent(new CustomEvent('shirim:now-playing'));
    });
    $('[data-close-queue]').onclick = () => { $('#queue-panel').hidden = true; };

    const seek = $('#p-seek');
    seek.addEventListener('input', () => {
      this.seeking = true;
      $('#p-time').textContent = formatDuration((seek.value / 1000) * this.duration);
    });
    seek.addEventListener('change', () => {
      this.seeking = false;
      this.seek((seek.value / 1000) * this.duration);
    });

    $('#p-volume').addEventListener('input', (e) => this.setVolume(e.target.value / 100));
    $('#p-mute').onclick = () => this.toggleMute();

    $('#queue-list').addEventListener('click', (e) => {
      const row = e.target.closest('[data-pos]');
      if (!row) return;
      const pos = Number(row.dataset.pos);
      if (e.target.closest('[data-remove]')) this.removeAt(pos);
      else this.jumpTo(pos);
    });

    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.code === 'Space' && this.current) {
        e.preventDefault();
        this.toggle();
      }
    });
  }

  setVolume(value) {
    this.volume = Math.max(0, Math.min(1, value));
    if (this.volume > 0) this.mutedVolume = 0;
    this.engine.setVolume(this.volume);
    this.renderVolume();
    this.save();
  }

  toggleMute() {
    if (this.volume > 0) {
      this.mutedVolume = this.volume;  // unmuting goes back to this level
      this.setVolume(0);
    } else {
      this.setVolume(this.mutedVolume || 1);
    }
  }

  /** The speaker button shows when the app itself is silent, so a song is never quietly muted. */
  renderVolume() {
    const silent = this.volume === 0;
    $('#p-volume').value = Math.round(this.volume * 100);
    paintRange($('#p-volume'));
    const button = $('#p-mute');
    button.innerHTML = icon(silent ? 'volume-off' : 'volume');
    button.classList.toggle('muted-volume', silent);
    button.title = silent ? 'העוצמה באפליקציה מושתקת' : 'השתקה';
    if (silent && this.playing && !this.silentWarned) {
      this.silentWarned = true;
      toast('העוצמה באפליקציה על אפס. לחצו על סמל הרמקול כדי להחזיר קול.', 'error', 6000);
    }
    if (!silent) this.silentWarned = false;
  }

  toggleQueuePanel(show) {
    const panel = $('#queue-panel');
    panel.hidden = show === undefined ? !panel.hidden : !show;
    this.renderQueue();
  }

  renderTrack() {
    const track = this.current;
    $('#player').hidden = !track;
    if (!track) return;
    $('#p-title').textContent = track.title;
    $('#p-artist').textContent = track.artist;
    const img = $('#p-cover');
    img.onerror = () => {
      img.onerror = null;
      img.src = '/icon.svg';
    };
    img.src = picture(track.thumb) || '/icon.svg';
    this.renderLike();
  }

  renderLike() {
    const liked = !!this.current && store.isLiked(this.current.videoId);
    const button = $('#p-like');
    button.classList.toggle('on', liked);
    button.innerHTML = icon(liked ? 'heart-fill' : 'heart');
  }

  renderButtons() {
    const name = this.playing ? 'pause' : 'play';
    for (const id of ['#p-play', '#pm-play']) {
      const button = $(id);
      button.innerHTML = icon(name);
      button.classList.toggle('buffering', this.buffering && this.playing);
    }
    $('#p-shuffle').classList.toggle('on', this.shuffle);
    $('#p-repeat').classList.toggle('on', this.repeat !== 'off');
    $('#p-repeat-one').hidden = this.repeat !== 'one';
  }

  renderTime() {
    const duration = this.duration || this.current?.duration || 0;
    $('#p-duration').textContent = formatDuration(duration);
    $('#p-mini-progress').style.width = `${duration ? (this.position / duration) * 100 : 0}%`;
    if (this.seeking) return;
    $('#p-time').textContent = formatDuration(this.position);
    $('#p-seek').value = duration ? Math.round((this.position / duration) * 1000) : 0;
    paintRange($('#p-seek'));
  }

  renderQueue() {
    if ($('#queue-panel').hidden) return;
    const tracks = this.orderedTracks();
    const row = (t, pos) => `
      <div class="row-track ${pos === this.pos ? 'playing' : ''}" data-pos="${pos}">
        ${cover(t.thumb, 't-img')}
        <div class="t-main"><div class="t-title ellipsis">${esc(t.title)}</div><div class="t-sub ellipsis">${esc(t.artist)}</div></div>
        ${pos === this.pos ? '' : `<button class="icon-btn" data-remove title="הסר מהתור">${icon('close')}</button>`}
      </div>`;
    if (!tracks.length || this.pos < 0) {
      $('#queue-list').innerHTML = '<div class="empty small">התור ריק</div>';
      return;
    }
    const upcoming = tracks.slice(this.pos + 1, this.pos + 201);
    $('#queue-list').innerHTML = `
      <div class="queue-group">מתנגן עכשיו</div>${row(tracks[this.pos], this.pos)}
      ${upcoming.length ? `<div class="queue-group">הבאים${this.context?.title ? ` מתוך ${esc(this.context.title)}` : ''}</div>` : ''}
      ${upcoming.map((t, i) => row(t, this.pos + 1 + i)).join('')}`;
  }

  // endregion

  // region media session & persistence

  bindMediaSession() {
    if (native || !('mediaSession' in navigator)) return;
    const handlers = {
      play: () => this.engine.play(),
      pause: () => this.engine.pause(),
      nexttrack: () => this.next(),
      previoustrack: () => this.prev(),
      seekto: (details) => this.seek(details.seekTime),
    };
    for (const [action, handler] of Object.entries(handlers)) {
      try {
        navigator.mediaSession.setActionHandler(action, handler);
      } catch {
        // unsupported action in this browser
      }
    }
  }

  updateMediaSession() {
    if (native || !('mediaSession' in navigator) || !this.current) return;
    const track = this.current;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artist,
      album: track.album,
      artwork: picture(track.thumb) ? [{ src: new URL(track.thumb, location.href).href, sizes: '544x544' }] : [],
    });
  }

  save() {
    try {
      const tracks = this.orderedTracks().slice(0, MAX_SAVED_TRACKS);
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        tracks,
        pos: Math.min(this.pos, tracks.length - 1),
        shuffle: this.shuffle,
        repeat: this.repeat,
        volume: this.volume,
        context: this.context,
      }));
    } catch {
      // storage unavailable or full
    }
  }

  restore() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY) || 'null');
    } catch {
      saved = null;
    }
    if (!saved) return;
    this.shuffle = !!saved.shuffle;
    this.repeat = ['off', 'all', 'one'].includes(saved.repeat) ? saved.repeat : 'off';
    // A volume of zero is never restored: it would silence the app with no obvious reason
    const savedVolume = Number.isFinite(saved.volume) ? saved.volume : 1;
    this.volume = savedVolume > 0.01 ? savedVolume : 1;
    this.context = saved.context || null;
    this.engine.setVolume(this.volume);
    this.renderVolume();
    const tracks = (saved.tracks || []).map(asTrack).filter((t) => t.videoId);
    if (tracks.length && saved.pos >= 0) {
      // The saved order is already the play order
      this.queue = tracks;
      this.order = tracks.map((_, i) => i);
      this.pos = Math.min(saved.pos, tracks.length - 1);
      this.engine.setPlaylist(this.orderedTracks(), this.pos, false);
      this.engine.setRepeat(this.repeat);
    }
    this.changed();
  }

  // endregion
}

function shuffleInPlace(items) {
  for (let i = items.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [items[i], items[j]] = [items[j], items[i]];
  }
  return items;
}

export const player = new Player();

// For troubleshooting playback: shirimDiagnostics() in the page's console
window.shirimDiagnostics = () => ({
  playing: player.playing,
  position: player.position,
  track: player.current?.videoId || null,
  ...player.engine.diagnostics(),
});
