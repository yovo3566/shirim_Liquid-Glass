// A list of songs: tap to play, like, the ⋯ menu, an optional filter and a selection mode for bulk actions.

import { player } from './player.js';
import { isLocal, store } from './store.js';
import { addToPlaylistDialog, downloadTracks, trackMenu } from './tracks.js';
import { $, $$, esc, icon, cover, formatDuration, debounce } from './ui.js';

const PAGE = 200;

export class TrackList {
  /**
   * options: tracks, context ({title, key}), numbered, thumbs, album, filter, selectable, emptyText,
   * onRemove(track, index), onMove(track, newIndex), menuExtras(track) -> menu items,
   * selectionActions: [{label, icon, danger, action(tracks, clearSelection)}]
   */
  constructor(el, options = {}) {
    this.el = el;
    this.o = {
      context: null, numbered: false, thumbs: true, album: true, filter: false, selectable: false,
      emptyText: 'אין שירים', onRemove: null, onMove: null, menuExtras: null, selectionActions: [], ...options,
    };
    this.tracks = [];
    this.query = '';
    this.limit = PAGE;
    this.selecting = false;
    this.selected = new Set();

    const tools = this.o.filter || this.o.selectable;
    el.innerHTML = `
      ${tools ? `<div class="list-tools">
        ${this.o.filter ? `<div class="filter">${icon('search')}<input class="input" data-filter placeholder="חיפוש ברשימה"></div>` : ''}
        <span class="spacer"></span>
        ${this.o.selectable ? `<button class="btn btn-sm btn-ghost" data-select-mode>${icon('check')} בחירה</button>` : ''}
      </div>` : ''}
      <div class="tracklist" data-rows></div>
      <div class="select-bar" data-bar hidden>
        <label class="row-flex"><input type="checkbox" class="check" data-all> <b data-count></b></label>
        <span class="spacer"></span>
        <button class="btn btn-sm" data-sel-play>${icon('play')} נגן</button>
        <button class="btn btn-sm" data-sel-playlist>${icon('plus')} לפלייליסט</button>
        <button class="btn btn-sm" data-sel-download>${icon('download')} שמור לאופליין</button>
        ${this.o.selectionActions.map((a, i) => `<button class="btn btn-sm ${a.danger ? 'btn-danger' : ''}" data-sel-extra="${i}">${icon(a.icon)} ${esc(a.label)}</button>`).join('')}
        <button class="icon-btn" data-sel-close title="סגור">${icon('close')}</button>
      </div>`;
    this.rowsEl = $('[data-rows]', el);

    this.onClickBound = (e) => this.onClick(e);
    el.addEventListener('click', this.onClickBound);
    $('[data-filter]', el)?.addEventListener('input', debounce((e) => {
      this.query = e.target.value;
      this.limit = PAGE;
      this.render();
    }, 150));
    $('[data-all]', el).addEventListener('change', (e) => {
      for (const track of this.visible()) {
        if (e.target.checked) this.selected.add(track.videoId);
        else this.selected.delete(track.videoId);
      }
      this.render();
    });
    this.offs = [
      store.on('library', () => this.render()),
      store.on('likes', () => this.render()),
      player.onChange(() => this.markPlaying()),
    ];
    this.setTracks(options.tracks || []);
  }

  destroy() {
    this.el.removeEventListener('click', this.onClickBound);
    this.offs.forEach((off) => off());
  }

  setTracks(tracks) {
    this.tracks = tracks;
    const ids = new Set(tracks.map((t) => t.videoId));
    for (const id of [...this.selected]) if (!ids.has(id)) this.selected.delete(id);
    this.render();
  }

  visible() {
    const query = this.query.trim().toLowerCase();
    if (!query) return this.tracks;
    return this.tracks.filter((t) => `${t.title} ${t.artist} ${t.album}`.toLowerCase().includes(query));
  }

  render() {
    const rows = this.visible();
    this.rowsEl.classList.toggle('selecting', this.selecting);
    if (!rows.length) {
      this.rowsEl.innerHTML = `<div class="empty small">${esc(this.query ? 'לא נמצאו שירים' : this.o.emptyText)}</div>`;
    } else {
      const more = rows.length - this.limit;
      this.rowsEl.innerHTML = rows.slice(0, this.limit).map((t, i) => this.row(t, i)).join('')
        + (more > 0 ? `<button class="btn btn-ghost show-more" data-more>הצג עוד ${more.toLocaleString('he-IL')}</button>` : '');
    }
    this.markPlaying();
    this.renderBar();
  }

  row(track, index) {
    const local = isLocal(track.videoId);
    const saved = !local && store.isSaved(track.videoId);
    const liked = store.isLiked(track.videoId);
    const selected = this.selected.has(track.videoId);
    const badge = local ? icon('folder', 'local-mark') : saved ? icon('saved', 'saved-mark') : '';
    return `
      <div class="row-track ${selected ? 'selected' : ''}" data-i="${index}" data-id="${esc(track.videoId)}">
        ${this.selecting ? `<input type="checkbox" class="check" data-check ${selected ? 'checked' : ''}>` : ''}
        ${this.o.numbered ? `<span class="t-num"><span class="n">${index + 1}</span>${icon('play', 'hover-play')}</span>` : ''}
        ${this.o.thumbs ? cover(track.thumb, 't-img') : ''}
        <div class="t-main">
          <div class="t-title ellipsis">${esc(track.title)}</div>
          <div class="t-sub ellipsis">${badge}${esc(track.artist)}</div>
        </div>
        ${this.o.album ? `<div class="t-album ellipsis">${esc(track.album)}</div>` : ''}
        <button class="icon-btn t-like ${liked ? 'on' : ''}" data-like title="${liked ? 'הסר מהשירים שאהבתי' : 'הוסף לשירים שאהבתי'}">${icon(liked ? 'heart-fill' : 'heart')}</button>
        <span class="t-dur">${track.duration ? formatDuration(track.duration) : ''}</span>
        <button class="icon-btn t-more" data-menu title="עוד">${icon('more')}</button>
      </div>`;
  }

  onClick(e) {
    const target = e.target;
    if (target.closest('[data-more]')) {
      this.limit += PAGE;
      this.render();
      return;
    }
    if (target.closest('[data-select-mode]')) {
      this.selecting = !this.selecting;
      this.selected.clear();
      this.render();
      return;
    }
    if (target.closest('[data-sel-close]')) {
      this.clearSelection();
      return;
    }
    const chosen = () => this.tracks.filter((t) => this.selected.has(t.videoId));
    if (target.closest('[data-sel-play]')) {
      player.playTracks(chosen(), 0, this.o.context);
      return;
    }
    if (target.closest('[data-sel-playlist]')) {
      addToPlaylistDialog(chosen());
      return;
    }
    if (target.closest('[data-sel-download]')) {
      downloadTracks(chosen()).then((added) => { if (added) this.clearSelection(); });
      return;
    }
    const extra = target.closest('[data-sel-extra]');
    if (extra) {
      this.o.selectionActions[Number(extra.dataset.selExtra)].action(chosen(), () => this.clearSelection());
      return;
    }

    const rowEl = target.closest('.row-track');
    if (!rowEl) return;
    const index = Number(rowEl.dataset.i);
    const rows = this.visible();
    const track = rows[index];
    if (target.closest('[data-like]')) {
      store.toggleLike(track);
      return;
    }
    if (target.closest('[data-menu]')) {
      this.openMenu(target.closest('[data-menu]'), track, index);
      return;
    }
    if (this.selecting) {
      if (this.selected.has(track.videoId)) this.selected.delete(track.videoId);
      else this.selected.add(track.videoId);
      rowEl.classList.toggle('selected', this.selected.has(track.videoId));
      const box = $('[data-check]', rowEl);
      if (box) box.checked = this.selected.has(track.videoId);
      this.renderBar();
      return;
    }
    player.playTracks(rows, index, this.o.context);
  }

  openMenu(anchor, track, index) {
    const options = {};
    if (this.o.onRemove) options.onRemove = () => this.o.onRemove(track, index);
    // Reordering only makes sense on the full, unfiltered list
    if (this.o.onMove && !this.query) {
      if (index > 0) options.onMoveUp = () => this.o.onMove(track, index - 1);
      if (index < this.tracks.length - 1) options.onMoveDown = () => this.o.onMove(track, index + 1);
    }
    if (this.o.menuExtras) options.extras = this.o.menuExtras(track);
    trackMenu(anchor, track, options);
  }

  clearSelection() {
    this.selecting = false;
    this.selected.clear();
    this.render();
  }

  renderBar() {
    const bar = $('[data-bar]', this.el);
    bar.hidden = !this.selecting;
    if (!this.selecting) return;
    const count = this.selected.size;
    $('[data-count]', this.el).textContent = count ? `נבחרו ${count.toLocaleString('he-IL')}` : 'בחרו שירים';
    for (const button of $$('[data-sel-play], [data-sel-playlist], [data-sel-download], [data-sel-extra]', bar)) {
      button.disabled = !count;
    }
    const visible = this.visible();
    const all = $('[data-all]', this.el);
    all.checked = visible.length > 0 && visible.every((t) => this.selected.has(t.videoId));
    all.indeterminate = !all.checked && visible.some((t) => this.selected.has(t.videoId));
  }

  markPlaying() {
    const current = player.current?.videoId;
    for (const row of $$('.row-track[data-id]', this.rowsEl)) row.classList.toggle('playing', row.dataset.id === current);
  }
}
