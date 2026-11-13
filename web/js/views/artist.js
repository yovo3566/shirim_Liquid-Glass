import { api, friendlyError } from '../api.js';
import { shareArtist } from '../share.js';
import { store } from '../store.js';
import { TrackList } from '../tracklist.js';
import { addToPlaylistDialog, downloadTracks, playFrom } from '../tracks.js';
import { $, esc, icon, confirmDialog, dominantColor, openMenu, toast } from '../ui.js';
import { canExportToUsb, exportToUsb } from '../usb.js';
import { bindPlayButton, errorBox, hero, loading, shelf, tile } from './common.js';

const POPULAR_SHOWN = 5;

const releaseTile = (item) => tile({
  href: `#/album/${encodeURIComponent(item.browseId)}`, thumb: item.thumb, title: item.title, subtitle: item.year,
  play: `album|${item.browseId}|${item.title}`,
});

export async function render(el, params, ctx) {
  el.innerHTML = loading();
  let artist;
  try {
    artist = await api.get('/api/artist', { id: params.id });
  } catch (error) {
    if (ctx.alive) el.innerHTML = errorBox(error);
    return;
  }
  if (!ctx.alive) return;

  const key = `artist:${artist.browseId}`;
  const context = { title: artist.name, key };
  let allSongs = null;
  let list = null;

  const loadAll = async () => {
    if (!allSongs) allSongs = (await api.get('/api/artist/songs', { id: artist.browseId })).tracks;
    return allSongs;
  };

  el.innerHTML = `
    ${hero({ kind: 'זמר', title: artist.name, meta: artist.subscribers ? `${esc(artist.subscribers)} מנויים ביוטיוב` : '', image: artist.thumb, round: true })}
    <div class="actions-bar">
      <button class="fab play-lg" data-play title="נגן"></button>
      <button class="icon-btn lg" data-shuffle title="ערבוב">${icon('shuffle')}</button>
      <button class="btn btn-outline" data-follow></button>
      <button class="icon-btn lg" data-download title="שמור את כל השירים לאופליין">${icon('download')}</button>
      <button class="icon-btn lg" data-more title="עוד">${icon('more')}</button>
    </div>
    <h2 class="section-title">פופולריים</h2>
    <div data-songs></div>
    <button class="btn btn-ghost" data-show-all>הצג את כל השירים</button>
    ${shelf('אלבומים', artist.albums.map(releaseTile).join(''))}
    ${shelf('סינגלים ו-EP', artist.singles.map(releaseTile).join(''))}
    ${shelf('מעריצים אוהבים גם', artist.related.map((a) => tile({ href: `#/artist/${encodeURIComponent(a.browseId)}`, thumb: a.thumb, title: a.name, subtitle: 'זמר', round: true })).join(''))}`;

  dominantColor(artist.thumb).then((color) => {
    if (color && ctx.alive) el.style.setProperty('--hero-color', color);
  });

  const mount = (tracks, full) => {
    list?.destroy();
    const holder = document.createElement('div');
    $('[data-songs]', el).replaceChildren(holder);
    list = new TrackList(holder, { tracks, context, numbered: true, filter: full, selectable: full, emptyText: 'לא נמצאו שירים' });
  };
  mount(artist.preview.slice(0, POPULAR_SHOWN), false);
  ctx.onCleanup(() => list?.destroy());

  bindPlayButton($('[data-play]', el), ctx, key, () => playFrom(loadAll, context));
  $('[data-shuffle]', el).onclick = () => playFrom(loadAll, context, { shuffle: true });

  const followButton = $('[data-follow]', el);
  const renderFollow = () => {
    followButton.textContent = store.isFollowing(artist.browseId) ? 'במעקב' : 'מעקב';
    followButton.title = 'זמרים שבמעקב מופיעים בספרייה, ומקבלים הודעה כשהם מוציאים שיר חדש';
  };
  renderFollow();
  followButton.onclick = () => store.setFollow(
    { browseId: artist.browseId, name: artist.name, thumb: artist.thumb }, !store.isFollowing(artist.browseId));
  ctx.onCleanup(store.on('follows', renderFollow));

  const showAll = $('[data-show-all]', el);
  showAll.onclick = async () => {
    showAll.disabled = true;
    showAll.textContent = 'טוען…';
    try {
      const tracks = await loadAll();
      if (!ctx.alive) return;
      mount(tracks, true);
      showAll.hidden = true;
    } catch (error) {
      toast(friendlyError(error), 'error');
      showAll.disabled = false;
      showAll.textContent = 'הצג את כל השירים';
    }
  };

  $('[data-download]', el).onclick = async () => {
    const answer = await confirmDialog({
      title: `לשמור את השירים של ${artist.name} לאופליין?`,
      message: 'השירים יישמרו ברקע. שירים שכבר שמורים לא יורדו שוב.',
      checkbox: 'כולל כל השירים מהאלבומים ומהסינגלים',
      confirmText: 'שמור',
    });
    if (!answer) return;
    toast('טוען את רשימת השירים…');
    try {
      const tracks = answer.checked
        ? (await api.get('/api/artist/songs', { id: artist.browseId, releases: '1' })).tracks
        : await loadAll();
      downloadTracks(tracks, { confirmOver: Infinity });
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  };

  $('[data-more]', el).onclick = (e) => openMenu(e.currentTarget, [
    { label: 'רדיו של הזמר', icon: 'radio', action: () => playFrom(() => api.get('/api/radio', { artistId: artist.browseId }), { title: `רדיו: ${artist.name}`, key: `artist-radio:${artist.browseId}` }) },
    { label: 'הוסף את כל השירים לפלייליסט…', icon: 'plus', action: async () => addToPlaylistDialog(await loadAll()) },
    ...(canExportToUsb() ? [{ label: 'העתקה לדיסק און קי', icon: 'usb', action: async () => exportToUsb(await loadAll(), artist.name) }] : []),
    { label: 'שתף', icon: 'share', action: () => shareArtist(artist) },
  ], { title: artist.name });

  el.addEventListener('click', (e) => {
    const play = e.target.closest('[data-play^="album|"]');
    if (!play) return;
    e.preventDefault();
    const [, id, title] = play.dataset.play.split('|');
    playFrom(async () => (await api.get('/api/album', { id })).tracks, { title, key: `album:${id}` });
  });
}
