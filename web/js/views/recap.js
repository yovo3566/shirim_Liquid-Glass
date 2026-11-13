// The monthly recap: how much you listened, your top artists and songs, and when you listen most.

import { api } from '../api.js';
import { player } from '../player.js';
import { share } from '../share.js';
import { saveAsPlaylist } from '../tracks.js';
import { $, esc, icon, cover } from '../ui.js';
import { errorBox, loading } from './common.js';

const number = (n) => Number(n || 0).toLocaleString('he-IL');

function comparison(data) {
  if (!data.previousMinutes) return '';
  const change = Math.round(((data.minutes - data.previousMinutes) / data.previousMinutes) * 100);
  if (Math.abs(change) < 5) return '<div class="recap-note">בערך כמו בחודש הקודם</div>';
  return `<div class="recap-note">${Math.abs(change)}% ${change > 0 ? 'יותר' : 'פחות'} מהחודש הקודם</div>`;
}

function shareText(data) {
  const lines = [`הסיכום שלי ל${data.label} באפליקציית שירים 🎧`, `${number(data.minutes)} דקות של מוזיקה`];
  if (data.topArtists[0]) lines.push(`הזמר שלי: ${data.topArtists[0].name}`);
  if (data.topSongs[0]) lines.push(`השיר שלי: ${data.topSongs[0].title}${data.topSongs[0].artist ? ` – ${data.topSongs[0].artist}` : ''}`);
  if (data.topSongs.length > 1) {
    lines.push('', 'השירים המובילים:', ...data.topSongs.slice(0, 5).map((s, i) => `${i + 1}. ${s.title} – ${s.artist}`));
  }
  return lines.join('\n');
}

export async function render(el, params, ctx) {
  el.innerHTML = loading();
  let data;
  try {
    data = await api.get('/api/recap', params.month ? { month: params.month } : undefined);
  } catch (error) {
    if (ctx.alive) el.innerHTML = errorBox(error);
    return;
  }
  if (!ctx.alive) return;
  if (!data.month) {
    el.innerHTML = `
      <div class="page-head"><h1>הסיכום החודשי</h1></div>
      <div class="empty">${icon('star')}<div>עוד אין מספיק האזנות לסיכום. אחרי כמה ימים של שמיעה יופיעו כאן הזמרים והשירים שלך.</div></div>`;
    return;
  }

  const [topArtist] = data.topArtists;
  const context = { title: `השירים שלי ב${data.label}`, key: `recap:${data.month}` };
  el.innerHTML = `
    <div class="page-head">
      <h1>הסיכום של ${esc(data.label)}</h1><span class="spacer"></span>
      ${data.months.length > 1 ? `<select class="input" data-month style="width:auto">
        ${data.months.map((m) => `<option value="${m.month}" ${m.month === data.month ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}
      </select>` : ''}
      <button class="btn btn-sm" data-share>${icon('share')} שיתוף</button>
    </div>
    <div class="recap">
      <section class="recap-card recap-minutes">
        <small>ב${esc(data.label)} שמעת</small>
        <b class="recap-big">${number(data.minutes)}</b>
        <span class="recap-unit">דקות של מוזיקה</span>
        <div class="recap-note">${number(data.plays)} השמעות · ${number(data.songs)} שירים שונים · ${number(data.artists)} זמרים · ${number(data.days)} ימים</div>
        ${comparison(data)}
      </section>

      ${topArtist ? `
      <a class="recap-card recap-artist" ${topArtist.browseId ? `href="#/artist/${encodeURIComponent(topArtist.browseId)}"` : ''}>
        ${cover(topArtist.thumb, 'recap-artist-img')}
        <div>
          <small>הזמר הכי שלך החודש</small>
          <b class="recap-name">${esc(topArtist.name)}</b>
          <div class="recap-note">${number(topArtist.plays)} השמעות · ${number(topArtist.minutes)} דקות</div>
        </div>
      </a>` : ''}

      ${data.topArtists.length > 1 ? `
      <section class="recap-card recap-list">
        <h2>הזמרים המובילים</h2>
        <ol>${data.topArtists.map((a) => `
          <li><a ${a.browseId ? `href="#/artist/${encodeURIComponent(a.browseId)}"` : ''}>
            ${cover(a.thumb, 'recap-thumb round')}<span class="grow ellipsis">${esc(a.name)}</span><span class="muted small">${number(a.plays)}</span>
          </a></li>`).join('')}</ol>
      </section>` : ''}

      <section class="recap-card recap-list recap-songs">
        <h2>השירים המובילים</h2>
        <ol>${data.topSongs.map((s, i) => `
          <li><button data-song="${i}">
            ${cover(s.thumb, 'recap-thumb')}<span class="grow ellipsis"><b>${esc(s.title)}</b><br><span class="muted small">${esc(s.artist)}</span></span>
            <span class="muted small">${number(s.plays)}×</span>
          </button></li>`).join('')}</ol>
        <div class="row-flex">
          <button class="btn btn-primary btn-sm" data-play-all>${icon('play')} נגן את כולם</button>
          <button class="btn btn-sm" data-save>${icon('library')} שמור כפלייליסט</button>
        </div>
      </section>

      <section class="recap-card recap-habits">
        ${data.topDay ? `<small>הכי הרבה מוזיקה</small><b class="recap-name">ביום ${esc(data.topDay)} ${esc(data.topPart)}</b>` : ''}
        ${data.newArtistCount ? `<div class="recap-note">${data.newArtistCount === 1 ? 'גילית זמר חדש' : `גילית ${number(data.newArtistCount)} זמרים חדשים`}${data.newArtists.length ? `: ${data.newArtists.map(esc).join(', ')}` : ''}</div>` : ''}
      </section>
    </div>`;

  $('[data-month]', el)?.addEventListener('change', (e) => { location.hash = `#/recap?month=${e.target.value}`; });
  $('[data-share]', el).onclick = () => share({ title: `הסיכום של ${data.label}`, text: shareText(data) });
  $('[data-play-all]', el).onclick = () => player.playTracks(data.topSongs, 0, context);
  $('[data-save]', el).onclick = () => saveAsPlaylist(data.topSongs, `הלהיטים שלי – ${data.label}`);
  el.querySelector('.recap-songs ol').addEventListener('click', (e) => {
    const song = e.target.closest('[data-song]');
    if (song) player.playTracks(data.topSongs, Number(song.dataset.song), context);
  });
}
