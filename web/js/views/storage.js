// Storage: what the saved songs take, songs you haven't played in a long time, and automatic clean-up.

import { api, friendlyError, native } from '../api.js';
import { store } from '../store.js';
import { $, esc, icon, cover, confirmDialog, formatBytes, songCount, timeAgo, toast } from '../ui.js';
import { errorBox, loading } from './common.js';

const DAY_CHOICES = [30, 60, 90, 180, 365];

export async function render(el, params, ctx) {
  let days = DAY_CHOICES.includes(Number(params.days)) ? Number(params.days) : (store.settings?.auto_clean_days || 90);
  const where = native ? 'בטלפון' : 'במחשב';
  const remote = store.info?.remote;
  const selected = new Set();
  let data = null;

  const draw = () => {
    const used = data.disk.total - data.disk.free;
    const percent = data.disk.total ? Math.round((used / data.disk.total) * 100) : 0;
    const candidates = data.candidates;
    const selectedSize = candidates.filter((t) => selected.has(t.videoId)).reduce((sum, t) => sum + t.size, 0);
    const biggest = data.artists[0]?.size || 1;
    el.innerHTML = `
      <div class="page-head"><h1>ניהול מקום</h1></div>
      <div class="stats">
        <div class="stat"><b>${formatBytes(data.downloads.size)}</b><span>${songCount(data.downloads.count)} שנשמרו באפליקציה</span></div>
        <div class="stat"><b>${formatBytes(data.local.size)}</b><span>${songCount(data.local.count)} שהיו ${where} קודם (לא נמחקים מכאן)</span></div>
        <div class="stat"><b>${formatBytes(data.cache)}</b><span>תמונות ונתונים זמניים</span>
          ${remote ? '' : `<button class="btn btn-sm btn-ghost" data-clear-cache style="margin-top:6px">ניקוי</button>`}</div>
      </div>
      ${data.disk.total ? `
      <div class="card">
        <div class="row-flex"><b>מקום פנוי ${where}</b><span class="spacer"></span><span class="muted">${formatBytes(data.disk.free)} פנויים מתוך ${formatBytes(data.disk.total)}</span></div>
        <div class="progress storage-bar ${percent > 90 ? 'full' : ''}" style="margin-top:10px"><i style="width:${percent}%"></i></div>
      </div>` : ''}

      <h2 class="section-title">שירים שלא שמעת הרבה זמן</h2>
      <div class="row-flex" style="margin-bottom:10px">
        <span class="muted small">לא הושמעו ב-</span>
        <select class="input" data-days style="width:auto">
          ${DAY_CHOICES.map((d) => `<option value="${d}" ${d === days ? 'selected' : ''}>${d} הימים האחרונים</option>`).join('')}
        </select>
        <span class="spacer"></span>
        ${candidates.length && !remote ? `
          <button class="btn btn-sm btn-ghost" data-select-all>${selected.size === candidates.length ? 'בטל בחירה' : 'בחר הכל'}</button>
          <button class="btn btn-sm btn-danger" data-remove ${selected.size ? '' : 'disabled'}>${icon('trash')} מחיקה${selected.size ? ` (${selected.size} · ${formatBytes(selectedSize)})` : ''}</button>` : ''}
      </div>
      <p class="muted small" style="margin-top:0">שירים שאהבת ושירים שנמצאים בפלייליסט לא מוצעים למחיקה. אפשר תמיד לשמור אותם שוב.</p>
      ${candidates.length ? `<div class="storage-list">${candidates.map((t) => `
        <label class="row-track storage-row">
          ${remote ? '' : `<input type="checkbox" class="check" data-pick="${esc(t.videoId)}" ${selected.has(t.videoId) ? 'checked' : ''}>`}
          ${cover(t.thumb, 't-img')}
          <div class="t-main"><div class="t-title ellipsis">${esc(t.title)}</div>
            <div class="t-sub ellipsis">${esc(t.artist)} · ${t.lastPlayed ? `הושמע ${timeAgo(t.lastPlayed)}` : `נשמר ${timeAgo(t.addedAt)}, לא הושמע`}</div></div>
          <span class="muted small">${formatBytes(t.size)}</span>
        </label>`).join('')}</div>`
    : `<div class="empty small">${icon('check')}<div>אין שירים ישנים שלא שמעת. הכל בשימוש.</div></div>`}

      ${data.artists.length ? `
      <h2 class="section-title">מה תופס הכי הרבה מקום</h2>
      <div class="card storage-artists">${data.artists.map((a) => `
        <div class="storage-artist">
          <div class="row-flex"><span class="grow ellipsis">${esc(a.name)}</span><span class="muted small">${songCount(a.count)} · ${formatBytes(a.size)}</span></div>
          <div class="progress"><i style="width:${Math.max(2, Math.round((a.size / biggest) * 100))}%"></i></div>
        </div>`).join('')}</div>` : ''}

      ${remote ? '' : `
      <h2 class="section-title">ניקוי אוטומטי</h2>
      <div class="card">
        <div class="setting">
          <div class="grow"><div>למחוק לבד שירים שלא שמעתי הרבה זמן</div><div>פעם ביום. שירים שאהבת ושירים בפלייליסטים לא יימחקו.</div></div>
          <label class="switch"><input type="checkbox" data-auto-clean ${store.settings?.auto_clean ? 'checked' : ''}><span></span></label>
        </div>
        <div class="setting">
          <div class="grow"><div>אחרי כמה זמן בלי השמעה</div></div>
          <select class="input" data-auto-days>
            ${DAY_CHOICES.map((d) => `<option value="${d}" ${d === store.settings?.auto_clean_days ? 'selected' : ''}>${d} ימים</option>`).join('')}
          </select>
        </div>
        <div class="setting">
          <div class="grow"><div>איכות השמירה וההשמעה</div><div>חיסכון: קבצים קטנים בערך בחצי ופחות גלישה, באיכות קצת נמוכה יותר. חל על שירים חדשים.</div></div>
          <select class="input" data-quality>
            <option value="high" ${store.settings?.audio_quality !== 'saver' ? 'selected' : ''}>גבוהה</option>
            <option value="saver" ${store.settings?.audio_quality === 'saver' ? 'selected' : ''}>חיסכון במקום</option>
          </select>
        </div>
      </div>`}`;
  };

  const load = async () => {
    try {
      data = await api.get('/api/storage', { days });
    } catch (error) {
      if (ctx.alive) el.innerHTML = errorBox(error);
      return;
    }
    if (!ctx.alive) return;
    for (const id of [...selected]) {
      if (!data.candidates.some((t) => t.videoId === id)) selected.delete(id);
    }
    draw();
  };

  el.innerHTML = loading();
  await load();

  el.addEventListener('change', async (e) => {
    const target = e.target;
    try {
      if (target.matches('[data-days]')) {
        days = Number(target.value);
        selected.clear();
        el.innerHTML = loading();
        await load();
      } else if (target.matches('[data-pick]')) {
        if (target.checked) selected.add(target.dataset.pick);
        else selected.delete(target.dataset.pick);
        draw();
      } else if (target.matches('[data-auto-clean]')) {
        await store.saveSettings({ auto_clean: target.checked });
        toast(target.checked ? 'הניקוי האוטומטי הופעל' : 'הניקוי האוטומטי כבוי');
      } else if (target.matches('[data-auto-days]')) {
        await store.saveSettings({ auto_clean_days: Number(target.value) });
        toast('נשמר', 'info', 1500);
      } else if (target.matches('[data-quality]')) {
        await store.saveSettings({ audio_quality: target.value });
        toast('נשמר', 'info', 1500);
      }
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  });

  el.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    try {
      if (button.matches('[data-select-all]')) {
        if (selected.size === data.candidates.length) selected.clear();
        else data.candidates.forEach((t) => selected.add(t.videoId));
        draw();
      } else if (button.matches('[data-remove]')) {
        const ok = await confirmDialog({
          title: `למחוק ${songCount(selected.size)}?`,
          message: `הקבצים יימחקו ${where}. אפשר לשמור אותם שוב מתי שתרצו.`,
          confirmText: 'מחיקה',
          danger: true,
        });
        if (!ok) return;
        const result = await api.post('/api/storage/remove', { videoIds: [...selected] });
        selected.clear();
        toast(`נמחקו ${songCount(result.removed)}, התפנו ${formatBytes(result.freed)}`);
        await store.loadLibrary();
        await load();
      } else if (button.matches('[data-clear-cache]')) {
        const result = await api.post('/api/storage/clear-cache');
        toast(`התפנו ${formatBytes(result.freed)}`);
        await load();
      }
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  });
}
