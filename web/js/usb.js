// Copying songs to a USB stick for the car (desktop app only).

import { api, friendlyError } from './api.js';
import { asTrack, isLocal, store } from './store.js';
import { downloadTracks } from './tracks.js';
import { $, esc, icon, customDialog, formatBytes, songCount, toast } from './ui.js';

const POLL_MS = 500;

export const canExportToUsb = () => store.info?.platform === 'windows' && !store.info?.remote;

const driveLabel = (d) => `${d.label || 'דיסק און קי'} (${d.root.slice(0, 2)}) · פנוי ${formatBytes(d.free)}`;

export async function exportToUsb(tracks, name) {
  tracks = tracks.map(asTrack);
  const saved = tracks.filter((t) => store.isSaved(t.videoId));
  const unsaved = tracks.filter((t) => !store.isSaved(t.videoId) && !isLocal(t.videoId));
  const size = saved.reduce((sum, t) => sum + (store.libraryById.get(t.videoId)?.size || 0), 0);
  const needsConversion = saved.some((t) => store.libraryById.get(t.videoId)?.format !== 'mp3');

  let info;
  try {
    info = await api.get('/api/usb/drives');
  } catch (error) {
    toast(friendlyError(error), 'error');
    return;
  }

  await customDialog(`
    <h3>העתקה לדיסק און קי</h3>
    ${saved.length ? `<p>${songCount(saved.length)} · ${formatBytes(size)}</p>` : '<p>אין כאן שירים ששמורים במחשב, אז אין מה להעתיק.</p>'}
    ${unsaved.length ? `<div class="card-box" style="margin-bottom:12px">
      ${songCount(unsaved.length)} עוד לא שמורים במחשב ולא יועתקו.
      <button class="btn btn-sm" data-save-first style="margin-top:8px">${icon('download')} שמור אותם לאופליין</button>
      <div class="muted small" style="margin-top:6px">כשהשמירה תסתיים, אפשר להעתיק שוב והם יצטרפו.</div></div>` : ''}
    <div data-form ${saved.length ? '' : 'hidden'}>
      <label class="muted small">דיסק און קי</label>
      <div class="row-flex" style="margin:4px 0 12px;flex-wrap:nowrap">
        <select class="input" data-drive></select>
        <button class="icon-btn" data-refresh title="רענן">${icon('retry')}</button>
      </div>
      <label class="muted small">שם התיקייה בדיסק און קי</label>
      <input class="input" data-folder value="${esc(name || 'Shirim')}" style="margin:4px 0 12px">
      <label class="row-flex" style="margin-bottom:6px"><input type="radio" name="layout" value="numbered" checked> לפי הסדר, עם מספר לפני כל שיר</label>
      <label class="row-flex" style="margin-bottom:12px"><input type="radio" name="layout" value="artists"> תיקייה נפרדת לכל זמר</label>
      ${info.canConvert && needsConversion ? '<label class="row-flex"><input type="checkbox" class="check" data-convert checked> להמיר ל-MP3 (רוב הרכבים מנגנים רק MP3)</label>' : ''}
    </div>
    <div data-progress hidden style="margin-top:14px">
      <div class="progress"><i data-bar></i></div>
      <div class="muted small" data-progress-text style="margin-top:8px"></div>
    </div>
    <div class="actions">
      <button class="btn btn-ghost" data-close>סגור</button>
      <button class="btn btn-sm btn-danger" data-cancel hidden>עצור</button>
      <button class="btn btn-primary" data-start ${saved.length ? '' : 'hidden'}>${icon('download')} העתק</button>
    </div>`, (modal, close) => {
    let timer = null;
    const dialog = $('.dialog', modal);
    const select = $('[data-drive]', dialog);

    const renderDrives = () => {
      select.innerHTML = info.drives.length
        ? info.drives.map((d) => `<option value="${esc(d.root)}">${esc(driveLabel(d))}</option>`).join('')
        : '<option value="">לא נמצא דיסק און קי. חבר אחד ולחץ על רענן</option>';
      $('[data-start]', dialog).disabled = !info.drives.length;
    };

    const renderStatus = (status) => {
      $('[data-progress]', dialog).hidden = false;
      $('[data-bar]', dialog).style.width = `${status.total ? (status.done / status.total) * 100 : 0}%`;
      const text = status.running
        ? `מעתיק ${status.done.toLocaleString('he-IL')} מתוך ${status.total.toLocaleString('he-IL')}${status.current ? `: ${status.current}` : ''}`
        : status.error
          ? `ההעתקה נכשלה: ${status.error}`
          : [`הועתקו ${songCount(status.copied)}`, status.skipped && `${status.skipped} כבר היו שם`, status.failed && `${status.failed} נכשלו`]
            .filter(Boolean).join(' · ') + '. אפשר להוציא את הדיסק און קי (לחיצה ימנית עליו ← הוצאה).';
      $('[data-progress-text]', dialog).textContent = text;
      $('[data-cancel]', dialog).hidden = !status.running;
      $('[data-start]', dialog).hidden = status.running || !saved.length;
      $('[data-form]', dialog).hidden = status.running;
    };

    const poll = async () => {
      try {
        const status = await api.get('/api/usb/status');
        if (modal.hidden) return;
        renderStatus(status);
        if (status.running) timer = setTimeout(poll, POLL_MS);
      } catch (error) {
        toast(friendlyError(error), 'error');
      }
    };

    renderDrives();
    dialog.addEventListener('click', async (e) => {
      const button = e.target.closest('button');
      if (!button) return;
      if (button.matches('[data-close]')) {
        clearTimeout(timer);
        close(null);
      } else if (button.matches('[data-save-first]')) {
        downloadTracks(unsaved);
      } else if (button.matches('[data-refresh]')) {
        info = await api.get('/api/usb/drives');
        renderDrives();
      } else if (button.matches('[data-cancel]')) {
        api.post('/api/usb/cancel').catch(() => {});
      } else if (button.matches('[data-start]')) {
        try {
          await api.post('/api/usb/export', {
            root: select.value,
            folder: $('[data-folder]', dialog).value.trim() || name || 'Shirim',
            videoIds: saved.map((t) => t.videoId),
            layout: $('input[name=layout]:checked', dialog).value,
            convert: $('[data-convert]', dialog)?.checked ?? false,
          });
          poll();
        } catch (error) {
          toast(friendlyError(error), 'error');
        }
      }
    });
  });
}
