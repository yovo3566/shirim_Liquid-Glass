import { api } from '../api.js';
import { player } from '../player.js';
import { TrackList } from '../tracklist.js';
import { downloadTracks, saveAsPlaylist } from '../tracks.js';
import { $, esc, icon } from '../ui.js';
import { errorBox, shelf, skeletonShelves, tile } from './common.js';

function songsSection(title, key, subtitle = '') {
  return `
    <section>
      <div class="shelf-head" style="margin-top:28px">
        <h2>${esc(title)}</h2>
        <div class="row-flex">
          <button class="fab" data-play="${key}" title="נגן">${icon('play')}</button>
          <button class="btn btn-sm btn-ghost" data-save="${key}">${icon('plus')} שמור כפלייליסט</button>
          <button class="btn btn-sm btn-ghost" data-download="${key}">${icon('download')} שמור לאופליין</button>
        </div>
      </div>
      ${subtitle ? `<div class="muted small" style="margin:-4px 0 8px">${esc(subtitle)}</div>` : ''}
      <div data-list="${key}"></div>
    </section>`;
}

export function render(el, params, ctx) {
  let lists = [];

  el.innerHTML = `
    <div class="page-head">
      <h1>גלה</h1><span class="spacer"></span>
      <button class="btn btn-sm btn-ghost" data-refresh>${icon('retry')} המלצות חדשות</button>
    </div>
    <div data-body>${skeletonShelves(2)}</div>`;
  const body = $('[data-body]', el);

  const load = async (refresh) => {
    lists.forEach((list) => list.destroy());
    lists = [];
    body.innerHTML = skeletonShelves(2);
    let data;
    try {
      data = await api.get('/api/discover', refresh ? { refresh: '1' } : undefined);
    } catch (error) {
      if (ctx.alive) body.innerHTML = errorBox(error);
      return;
    }
    if (!ctx.alive) return;

    if (!data.artists.length && !data.songs.length && !data.moreFromYourArtists.length) {
      body.innerHTML = `<div class="welcome"><h2>עוד מוקדם להמלצות</h2>
        <div class="muted">שמעו כמה שירים, סמנו לייקים ועקבו אחרי זמרים, והמסך הזה יתמלא בזמרים ובשירים שכדאי להכיר.</div>
        <a class="btn btn-primary" href="#/search" style="margin-top:16px">${icon('search')} לחיפוש</a></div>`;
      return;
    }

    const groups = { new: data.songs, more: data.moreFromYourArtists };
    body.innerHTML = `
      ${data.basedOn.length ? `<div class="muted">לפי ההאזנות שלך ל${esc(data.basedOn.join(', '))} ועוד</div>` : ''}
      ${shelf('זמרים שכדאי להכיר', data.artists.map((a) => tile({
        href: `#/artist/${encodeURIComponent(a.browseId)}`, thumb: a.thumb, title: a.name, subtitle: a.reason, round: true,
      })).join(''))}
      ${data.songs.length ? songsSection('שירים חדשים בשבילך', 'new', 'מזמרים שעוד לא שמעת, דומים למה שאת/ה אוהב/ת') : ''}
      ${data.moreFromYourArtists.length ? songsSection('עוד מהזמרים שלך', 'more', 'שירים פופולריים שעוד אין לך') : ''}`;

    for (const [key, tracks] of Object.entries(groups)) {
      const holder = $(`[data-list="${key}"]`, body);
      if (!holder) continue;
      lists.push(new TrackList(holder, { tracks, context: { title: 'גלה', key: `discover:${key}` }, selectable: true }));
    }

    body.onclick = (e) => {
      const button = e.target.closest('[data-play], [data-save], [data-download]');
      if (!button) return;
      const key = button.dataset.play || button.dataset.save || button.dataset.download;
      const tracks = groups[key];
      if (button.dataset.play) player.playTracks(tracks, 0, { title: 'גלה', key: `discover:${key}` });
      else if (button.dataset.save) saveAsPlaylist(tracks, key === 'new' ? 'גילויים חדשים' : 'עוד מהזמרים שלי');
      else downloadTracks(tracks);
    };
  };

  $('[data-refresh]', el).onclick = () => load(true);
  ctx.onCleanup(() => lists.forEach((list) => list.destroy()));
  load(false);
}
