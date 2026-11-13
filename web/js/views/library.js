// Your Library: liked songs, playlists, followed artists and the songs on this device.

import { api, friendlyError, native } from '../api.js';
import { importPlaylistDialog } from '../importer.js';
import { player } from '../player.js';
import { isLocal, store } from '../store.js';
import { TrackList } from '../tracklist.js';
import { createPlaylistFlow } from '../tracks.js';
import { $, esc, icon, cover, mosaic, likedCover, confirmDialog, formatBytes, songCount, toast } from '../ui.js';
import { canExportToUsb, exportToUsb } from '../usb.js';

const TABS = [
  ['all', 'הכל'],
  ['playlists', 'פלייליסטים'],
  ['artists', 'זמרים'],
  ['device', 'במכשיר'],
];
const SORTS = {
  recent: (a, b) => b.addedAt - a.addedAt,
  title: (a, b) => a.title.localeCompare(b.title, 'he'),
  artist: (a, b) => a.artist.localeCompare(b.artist, 'he') || a.album.localeCompare(b.album, 'he') || a.title.localeCompare(b.title, 'he'),
};

function collectionRows(tab) {
  const rows = [];
  if (tab === 'all' || tab === 'playlists') {
    rows.push(`<a class="lib-row" href="#/liked">${likedCover('lib-img')}<div class="grow"><div class="lib-name ellipsis">שירים שאהבתי</div><div class="muted small">פלייליסט · ${songCount(store.likes.size)}</div></div></a>`);
    for (const p of store.playlists) {
      rows.push(`<a class="lib-row" href="#/playlist/${p.id}">${mosaic(p.thumbs, 'lib-img')}<div class="grow"><div class="lib-name ellipsis">${esc(p.name)}</div><div class="muted small">פלייליסט · ${songCount(p.count)}</div></div></a>`);
    }
  }
  if (tab === 'all' || tab === 'artists') {
    for (const a of store.followList) {
      rows.push(`<a class="lib-row round" href="#/artist/${encodeURIComponent(a.browseId)}">${cover(a.thumb, 'lib-img')}<div class="grow"><div class="lib-name ellipsis">${esc(a.name)}</div><div class="muted small">זמר</div></div></a>`);
    }
  }
  if (tab === 'all' && store.library.length) {
    rows.push(`<a class="lib-row" href="#/library?tab=device"><span class="lib-img saved-cover">${icon('download')}</span><div class="grow"><div class="lib-name">השירים שבמכשיר</div><div class="muted small">${songCount(store.library.length)}</div></div></a>`);
  }
  if (tab === 'artists' && !store.followList.length) {
    rows.push(`<div class="empty">זמרים שבמעקב יופיעו כאן, ותקבלו הודעה כשהם מוציאים שיר חדש.
      <br><a class="btn btn-sm" href="#/new" style="margin-top:12px">${icon('bell')} לעקוב אחרי הזמרים שלי</a></div>`);
  }
  return rows.join('');
}

function deviceView(body, ctx) {
  let sort = 'recent';
  let artistFilter = '';
  let source = 'all';
  const remote = store.info?.remote;
  const canOpenFolder = store.info?.actions?.includes('open_folder');
  const where = native ? 'בטלפון' : 'במחשב';

  body.innerHTML = `
    <div class="scan-note" data-scan></div>
    <div class="row-flex" style="margin-bottom:6px">
      <button class="fab" data-play-all title="נגן הכל">${icon('play')}</button>
      <button class="icon-btn lg" data-shuffle title="ערבוב">${icon('shuffle')}</button>
      <span class="spacer"></span>
      <select class="input" data-source style="width:auto">
        <option value="all">כל השירים</option>
        <option value="download">נשמרו מהאפליקציה</option>
        <option value="local">קבצים שהיו ${where}</option>
      </select>
      <select class="input" data-artist style="width:auto;max-width:200px"></select>
      <select class="input" data-sort style="width:auto">
        <option value="recent">נוספו לאחרונה</option>
        <option value="artist">לפי זמר</option>
        <option value="title">לפי שם</option>
      </select>
      ${canExportToUsb() ? `<button class="btn btn-sm btn-ghost" data-usb>${icon('usb')} לדיסק און קי</button>` : ''}
      ${canOpenFolder ? `<button class="btn btn-sm btn-ghost" data-open-folder>${icon('folder')} פתח תיקייה</button>` : ''}
      <a class="btn btn-sm btn-ghost" href="#/storage">${icon('disk')} ניהול מקום</a>
    </div>
    <div data-list></div>`;

  const remove = async (tracks, done) => {
    const answer = await confirmDialog({
      title: tracks.length === 1 ? `להסיר את "${tracks[0].title}"?` : `להסיר ${tracks.length} שירים?`,
      message: 'השירים יוסרו מהספרייה.',
      checkbox: remote ? '' : `למחוק גם את הקבצים ${where}`,
      confirmText: 'הסר',
      danger: true,
    });
    if (!answer) return;
    try {
      await api.post('/api/library/delete', { videoIds: tracks.map((t) => t.videoId), deleteFiles: answer.checked });
      await store.loadLibrary();
      done?.();
      toast('הוסר');
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  };

  const selectionActions = [{ label: 'הסר', icon: 'trash', danger: true, action: (tracks, done) => remove(tracks, done) }];
  if (canExportToUsb()) {
    selectionActions.unshift({ label: 'לדיסק און קי', icon: 'usb', action: (tracks) => exportToUsb(tracks, 'Shirim') });
  }
  if (remote) {
    selectionActions.unshift({
      label: 'שמור למכשיר הזה', icon: 'download',
      action: (tracks) => { location.href = `/api/library/zip?ids=${tracks.map((t) => encodeURIComponent(t.videoId)).join(',')}`; },
    });
  }

  const list = new TrackList($('[data-list]', body), {
    context: { title: `השירים ${where}`, key: 'device' },
    filter: true,
    selectable: true,
    emptyText: `עוד אין שירים ${where}. שירים שתשמרו לאופליין, וגם מוזיקה שכבר יש ${where}, יופיעו כאן.`,
    selectionActions,
    menuExtras: (track) => [
      ...(remote ? [{ label: 'שמור למכשיר הזה', icon: 'download', action: () => { location.href = `/media/${encodeURIComponent(track.videoId)}?download=1`; } }] : []),
      { label: 'הסר מהספרייה', icon: 'trash', danger: true, action: () => remove([track]) },
    ],
  });
  ctx.onCleanup(() => list.destroy());

  const visible = () => store.library
    .filter((t) => t.exists !== false)
    .filter((t) => source === 'all' || (source === 'local' ? isLocal(t.videoId) : !isLocal(t.videoId)))
    .filter((t) => !artistFilter || t.artist.split(', ').includes(artistFilter))
    .sort(SORTS[sort]);

  const draw = () => {
    const counts = new Map();
    for (const track of store.library) {
      for (const name of track.artist.split(', ').filter(Boolean)) counts.set(name, (counts.get(name) || 0) + 1);
    }
    if (artistFilter && !counts.has(artistFilter)) artistFilter = '';
    $('[data-artist]', body).innerHTML = `<option value="">כל הזמרים (${counts.size})</option>`
      + [...counts.entries()].sort((a, b) => b[1] - a[1])
        .map(([name, count]) => `<option value="${esc(name)}" ${name === artistFilter ? 'selected' : ''}>${esc(name)} (${count})</option>`).join('');
    list.setTracks(visible());
  };

  const drawScan = (scan = store.queue?.scan || store.info?.scan) => {
    const note = $('[data-scan]', body);
    const local = store.library.filter((t) => isLocal(t.videoId)).length;
    const size = store.library.reduce((sum, t) => sum + (t.size || 0), 0);
    const parts = [`${songCount(store.library.length)} · ${formatBytes(size)}`];
    if (local) parts.push(`${songCount(local)} מתוכם נמצאו ${where}`);
    note.innerHTML = `<span>${parts.join(' · ')}</span>
      ${scan?.running ? `<span>${icon('retry')} מחפש מוזיקה ${where}…</span>` : `<button class="btn btn-sm btn-ghost" data-rescan>${icon('retry')} חפש שוב מוזיקה ${where}</button>`}`;
  };

  body.addEventListener('change', (e) => {
    if (e.target.matches('[data-sort]')) sort = e.target.value;
    else if (e.target.matches('[data-artist]')) artistFilter = e.target.value;
    else if (e.target.matches('[data-source]')) source = e.target.value;
    else return;
    list.setTracks(visible());
  });
  body.addEventListener('click', async (e) => {
    const context = { title: `השירים ${where}`, key: 'device' };
    if (e.target.closest('[data-play-all]')) {
      player.setShuffle(false);
      player.playTracks(visible(), 0, context);
    } else if (e.target.closest('[data-shuffle]')) {
      const tracks = visible();
      player.setShuffle(true);
      player.playTracks(tracks, Math.floor(Math.random() * tracks.length), context);
    } else if (e.target.closest('[data-usb]')) {
      exportToUsb(visible(), artistFilter || 'Shirim');
    } else if (e.target.closest('[data-open-folder]')) {
      api.post('/api/action', { name: 'open_folder' }).catch((error) => toast(friendlyError(error), 'error'));
    } else if (e.target.closest('[data-rescan]')) {
      await api.post('/api/scan');
      drawScan({ running: true });
    }
  });

  ctx.onCleanup(store.on('library', () => {
    draw();
    drawScan();
  }));
  ctx.onCleanup(store.on('scan', drawScan));
  draw();
  drawScan();
  store.loadLibrary().catch((error) => toast(friendlyError(error), 'error'));
}

export function render(el, params, ctx) {
  let tab = TABS.some(([key]) => key === params.tab) ? params.tab : 'all';
  let cleanupTab = null;

  el.innerHTML = `
    <div class="page-head">
      <h1>הספרייה שלך</h1><span class="spacer"></span>
      <button class="btn btn-sm" data-import title="פלייליסט מספוטיפיי, מיוטיוב או מרשימת שירים">${icon('import')} ייבוא</button>
      <button class="icon-btn lg" data-create title="פלייליסט חדש">${icon('plus')}</button>
    </div>
    <div class="tabs" data-tabs>${TABS.map(([key, label]) => `<button class="tab ${key === tab ? 'active' : ''}" data-tab="${key}">${label}</button>`).join('')}</div>
    <a class="downloads-hint" href="#/downloads" data-downloads-hint hidden></a>
    <div data-body></div>`;

  const body = $('[data-body]', el);
  const drawTab = () => {
    cleanupTab?.();
    cleanupTab = null;
    history.replaceState(null, '', `#/library?tab=${tab}`);
    if (tab === 'device') {
      const holder = document.createElement('div');
      body.replaceChildren(holder);
      const cleanups = [];
      deviceView(holder, { alive: true, onCleanup: (fn) => cleanups.push(fn) });
      cleanupTab = () => cleanups.forEach((fn) => fn());
    } else {
      body.innerHTML = `<div class="lib-list">${collectionRows(tab)}</div>`;
    }
  };

  const redrawCollections = () => { if (tab !== 'device') drawTab(); };
  ctx.onCleanup(store.on('playlists', redrawCollections));
  ctx.onCleanup(store.on('follows', redrawCollections));
  ctx.onCleanup(store.on('likes', redrawCollections));
  ctx.onCleanup(() => cleanupTab?.());

  const hint = $('[data-downloads-hint]', el);
  const drawHint = (snapshot) => {
    const active = snapshot ? snapshot.counts.queued + snapshot.counts.running : 0;
    hint.hidden = !active;
    hint.innerHTML = `${icon('download')} שומר ${songCount(active)} לאופליין… <span class="spacer"></span> ${icon('back')}`;
  };
  drawHint(store.queue);
  ctx.onCleanup(store.on('queue', drawHint));

  el.addEventListener('click', async (e) => {
    const tabButton = e.target.closest('[data-tab]');
    if (tabButton && tabButton.dataset.tab !== tab) {
      tab = tabButton.dataset.tab;
      for (const t of tabButton.parentElement.children) t.classList.toggle('active', t === tabButton);
      drawTab();
      return;
    }
    if (e.target.closest('[data-create]')) {
      const created = await createPlaylistFlow();
      if (created) location.hash = `#/playlist/${created.id}`;
    } else if (e.target.closest('[data-import]')) {
      importPlaylistDialog();
    }
  });

  drawTab();
}
