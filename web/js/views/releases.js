// "Out now": new singles and albums of followed artists.

import { api, friendlyError, native } from '../api.js';
import { store } from '../store.js';
import { playFrom } from '../tracks.js';
import { $, esc, icon, toast } from '../ui.js';
import { errorBox, loading, tile } from './common.js';

function lastCheckText(timestamp) {
  if (!timestamp) return 'הבדיקה הראשונה תתבצע בדקות הקרובות';
  const minutes = Math.round((Date.now() / 1000 - timestamp) / 60);
  return minutes < 1 ? 'נבדק עכשיו' : minutes < 60 ? `נבדק לפני ${minutes} דק׳` : `נבדק לפני ${Math.round(minutes / 60)} שע׳`;
}

export async function render(el, params, ctx) {
  el.innerHTML = `
    <div class="page-head">
      <h1>יצא עכשיו</h1><span class="spacer"></span>
      <button class="btn btn-sm btn-ghost" data-check>${icon('retry')} בדוק עכשיו</button>
    </div>
    <div class="card-box" style="margin-bottom:18px">
      <label class="row-flex" style="justify-content:space-between;flex-wrap:nowrap">
        <span><b>${native ? 'התראה' : 'הודעה'} על שירים חדשים</b><br><span class="muted small">בודקים כל כמה שעות אם זמר שבמעקב הוציא משהו חדש</span></span>
        <span class="switch"><input type="checkbox" data-key="notify_new_releases"><span></span></span>
      </label>
      <label class="row-flex" style="justify-content:space-between;flex-wrap:nowrap;margin-top:14px">
        <span><b>לשמור אותם אוטומטית לאופליין</b><br><span class="muted small">שירים חדשים יישמרו במכשיר ברגע שהם יוצאים</span></span>
        <span class="switch"><input type="checkbox" data-key="auto_download_new_releases"><span></span></span>
      </label>
    </div>
    <div data-body>${loading()}</div>`;
  const body = $('[data-body]', el);

  for (const input of el.querySelectorAll('[data-key]')) {
    input.checked = !!store.settings?.[input.dataset.key];
    input.addEventListener('change', async () => {
      try {
        await store.saveSettings({ [input.dataset.key]: input.checked });
      } catch (error) {
        input.checked = !input.checked;
        toast(friendlyError(error), 'error');
      }
    });
  }

  const load = async () => {
    let data;
    try {
      data = await api.get('/api/releases');
    } catch (error) {
      if (ctx.alive) body.innerHTML = errorBox(error);
      return;
    }
    if (!ctx.alive) return;

    if (!data.follows) {
      body.innerHTML = `<div class="welcome"><h2>עוד לא עוקבים אחרי זמרים</h2>
        <div class="muted">כדי לדעת כשיוצא שיר חדש, צריך לעקוב אחרי הזמרים. אפשר ללחוץ "מעקב" בעמוד של כל זמר, או לעקוב בבת אחת אחרי הזמרים ששומעים הכי הרבה.</div>
        <button class="btn btn-primary" data-follow-top style="margin-top:16px">${icon('plus')} לעקוב אחרי 20 הזמרים שלי</button></div>`;
      return;
    }
    body.innerHTML = `
      <div class="muted small" style="margin-bottom:8px">עוקבים אחרי ${data.follows} זמרים · ${lastCheckText(data.lastCheck)}</div>
      ${data.items.length
        ? `<div class="grid">${data.items.map((r) => tile({
          href: `#/album/${encodeURIComponent(r.browseId)}`, thumb: r.thumb, title: `${r.seen ? '' : '● '}${r.title}`,
          subtitle: [r.artist, r.year].filter(Boolean).join(' · '), play: `${r.browseId}|${r.title}`,
        })).join('')}</div>`
        : '<div class="empty">עוד לא יצא שום דבר חדש מאז שהתחלנו לעקוב. ברגע שיצא, זה יופיע כאן.</div>'}`;
    if (data.unseen) {
      api.post('/api/releases/seen').catch(() => {});
    }
  };

  el.addEventListener('click', async (e) => {
    const play = e.target.closest('[data-play]');
    if (play) {
      e.preventDefault();
      const [id, title] = play.dataset.play.split('|');
      playFrom(async () => (await api.get('/api/album', { id })).tracks, { title, key: `album:${id}` });
      return;
    }
    if (e.target.closest('[data-follow-top]')) {
      try {
        const { added } = await api.post('/api/follows/top', { limit: 20 });
        toast(added ? `עכשיו במעקב אחרי ${added} זמרים` : 'כבר עוקבים אחרי כולם');
        await store.loadCollection();
        load();
      } catch (error) {
        toast(friendlyError(error), 'error');
      }
      return;
    }
    const check = e.target.closest('[data-check]');
    if (check) {
      check.disabled = true;
      try {
        const { found } = await api.post('/api/releases/check');
        toast(found ? `נמצאו ${found} חדשים` : 'אין חדש כרגע');
        load();
      } catch (error) {
        toast(friendlyError(error), 'error');
      } finally {
        check.disabled = false;
      }
    }
  });

  load();
}
