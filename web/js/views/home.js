import { api } from '../api.js';
import { player } from '../player.js';
import { store } from '../store.js';
import { playFrom } from '../tracks.js';
import { $, esc, icon, cover, mosaic, likedCover } from '../ui.js';
import { errorBox, shelf, skeletonShelves, tile } from './common.js';

function greeting() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return 'בוקר טוב';
  if (hour >= 12 && hour < 17) return 'צהריים טובים';
  if (hour >= 17 && hour < 22) return 'ערב טוב';
  return 'לילה טוב';
}

function quickTiles() {
  const tiles = [
    `<a class="quick" href="#/liked">${likedCover('qimg')}<span class="ellipsis">שירים שאהבתי</span></a>`,
    `<a class="quick" href="#/discover"><span class="qimg liked-cover" style="background:linear-gradient(135deg,#ff4f8b,#ffb547)">${icon('compass')}</span><span class="ellipsis">גלה שירים חדשים</span></a>`,
  ];
  for (const playlist of store.playlists.slice(0, 4)) {
    tiles.push(`<a class="quick" href="#/playlist/${playlist.id}">${mosaic(playlist.thumbs, 'qimg')}<span class="ellipsis">${esc(playlist.name)}</span></a>`);
  }
  if (store.library.length) {
    tiles.push(`<a class="quick" href="#/library?tab=device"><span class="qimg saved-cover">${icon('download')}</span><span class="ellipsis">השירים שבמכשיר</span></a>`);
  }
  return tiles.join('');
}

function renderSection(section) {
  const options = { more: section.more || '' };
  switch (section.kind) {
    case 'tracks':
    case 'chart':
      return shelf(section.title, section.items.map((t, i) => `
        <a class="tile ${section.kind === 'chart' ? 'ranked' : ''}" href="#" data-song="${section.id}|${i}">
          <div class="art">${cover(t.thumb, 'card-img')}${section.kind === 'chart' ? `<b class="rank">${i + 1}</b>` : ''}<span class="fab">${icon('play')}</span></div>
          <div class="t1 ellipsis">${esc(t.title)}</div><div class="t2">${esc(t.artist)}</div>
        </a>`).join(''), options);
    case 'artists':
      return shelf(section.title, section.items.map((a) => tile({
        href: `#/artist/${encodeURIComponent(a.browseId)}`, thumb: a.thumb, title: a.name, subtitle: 'זמר', round: true,
        play: `artist|${a.browseId}|${a.name}`,
      })).join(''), options);
    case 'mixes':
      return shelf(section.title, section.items.map((m) => tile({
        href: `#/mix/${encodeURIComponent(m.id)}?title=${encodeURIComponent(m.title)}`, thumb: m.thumb, title: m.title,
        subtitle: m.subtitle, play: `mix|${m.id}|${m.title}`,
      })).join(''), options);
    case 'albums':
      return shelf(section.title, section.items.map((a) => tile({
        href: `#/album/${encodeURIComponent(a.browseId)}`, thumb: a.thumb, title: a.title,
        subtitle: [a.artist, a.year].filter(Boolean).join(' · '), play: `album|${a.browseId}|${a.title}`,
      })).join(''), options);
    default:
      return '';
  }
}

function playTarget(target) {
  const [kind, id, title] = target.split('|');
  if (kind === 'artist') {
    return playFrom(() => api.get('/api/radio', { artistId: id }), { title, key: `artist:${id}` });
  }
  if (kind === 'mix') {
    return playFrom(() => api.get('/api/mix', { id }), { title, key: `mix:${id}` });
  }
  return playFrom(async () => (await api.get('/api/album', { id })).tracks, { title, key: `album:${id}` });
}

const welcome = () => `
  <div class="welcome">
    <h2>ברוכים הבאים לשירים</h2>
    <div class="muted">חפשו זמר או שיר ותתחילו לשמוע מיד. אחרי כמה שירים המסך הזה יתמלא בזמרים שלכם, במיקסים ובשירים חדשים.</div>
    <a class="btn btn-primary" href="#/search" style="margin-top:16px">${icon('search')} לחיפוש</a>
  </div>`;

export async function render(el, params, ctx) {
  el.innerHTML = `
    <div class="page-head">
      <h1>${greeting()}</h1><span class="spacer"></span>
      <a class="icon-btn lg" href="#/new" title="יצא עכשיו" style="position:relative">${icon('bell')}<b class="badge" data-releases-badge hidden style="position:absolute;top:2px;inset-inline-end:0;margin:0;font-size:10px;min-width:16px"></b></a>
      <a class="icon-btn lg mobile-only" href="#/settings" title="הגדרות">${icon('settings')}</a>
    </div>
    <div class="quick-grid" data-quick></div>
    <div data-sections>${skeletonShelves(3)}</div>`;

  const drawQuick = () => { $('[data-quick]', el).innerHTML = quickTiles(); };
  drawQuick();
  ctx.onCleanup(store.on('playlists', drawQuick));
  ctx.onCleanup(store.on('library', drawQuick));

  const badge = $('[data-releases-badge]', el);
  const drawBadge = (snapshot) => {
    const unseen = snapshot?.releasesUnseen || 0;
    badge.hidden = !unseen;
    badge.textContent = unseen;
  };
  drawBadge(store.queue);
  ctx.onCleanup(store.on('queue', drawBadge));

  const sectionsEl = $('[data-sections]', el);
  let data;
  try {
    data = await api.get('/api/home');
  } catch (error) {
    if (ctx.alive) sectionsEl.innerHTML = errorBox(error);
    return;
  }
  if (!ctx.alive) return;
  const teaser = data.recap ? `
    <a class="recap-teaser" href="#/recap?month=${esc(data.recap.month)}">
      <span class="recap-teaser-icon">${icon('star')}</span>
      <div class="grow"><b>הסיכום של ${esc(data.recap.label)}</b><div class="small">הזמרים והשירים ששמעת הכי הרבה</div></div>
      ${icon('back')}
    </a>` : '';
  sectionsEl.innerHTML = (data.newUser ? welcome() : '') + teaser + data.sections.map(renderSection).join('');

  sectionsEl.addEventListener('click', (e) => {
    const play = e.target.closest('[data-play]');
    if (play) {
      e.preventDefault();
      e.stopPropagation();
      playTarget(play.dataset.play);
      return;
    }
    const song = e.target.closest('[data-song]');
    if (song) {
      e.preventDefault();
      const [id, index] = song.dataset.song.split('|');
      const section = data.sections.find((s) => s.id === id);
      player.playTracks(section.items, Number(index), { title: section.title, key: `home:${id}` });
    }
  });
}
