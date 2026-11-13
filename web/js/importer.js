// "Import playlist": a Spotify or YouTube link, or a pasted list of songs, becomes a playlist in the library.

import { api, friendlyError } from './api.js';
import { store } from './store.js';
import { $, customDialog, esc, icon, songCount, toast } from './ui.js';

const POLL_MS = 800;

export function importPlaylistDialog() {
  let timer = null;
  return customDialog(`
    <h3>ייבוא פלייליסט</h3>
    <p>הדביקו קישור לפלייליסט או לאלבום מספוטיפיי או מיוטיוב.
אפשר גם להדביק רשימת שירים, שיר בכל שורה, למשל: עומר אדם - תל אביב</p>
    <div data-form>
      <textarea class="input" rows="5" dir="auto" data-source placeholder="https://open.spotify.com/playlist/…"></textarea>
      <input class="input" data-name placeholder="שם לפלייליסט (לא חובה)" style="margin-top:8px">
    </div>
    <div data-progress hidden></div>
    <div class="actions">
      <button class="btn btn-ghost" data-cancel>ביטול</button>
      <button class="btn btn-primary" data-start>${icon('import')} ייבוא</button>
    </div>`, (modal, close) => {
    const dialog = $('.dialog', modal);
    const progress = $('[data-progress]', dialog);
    const finish = (value) => {
      clearTimeout(timer);
      close(value);
    };
    $('[data-cancel]', dialog).onclick = () => finish(null);
    $('[data-source]', dialog).focus();

    const showResult = (status) => {
      const missing = status.missing || [];
      $('[data-form]', dialog).hidden = true;
      progress.hidden = false;
      progress.innerHTML = `
        <div class="import-done">${icon('check')} נוצר הפלייליסט "${esc(status.name)}" עם ${songCount(status.matched)}</div>
        ${status.limited ? '<p>מספוטיפיי אפשר לקרוא רק את 100 השירים הראשונים של פלייליסט. את השאר אפשר להוסיף בהדבקת שמות השירים.</p>' : ''}
        ${missing.length ? `<p>לא נמצאו ביוטיוב (${missing.length}):</p><div class="import-missing">${missing.map((m) => `<div>${esc(m)}</div>`).join('')}</div>` : ''}`;
      const actions = $('.actions', dialog);
      actions.innerHTML = `<button class="btn btn-ghost" data-close>סגור</button>
        <button class="btn btn-primary" data-open>${icon('library')} לפלייליסט</button>`;
      $('[data-close]', actions).onclick = () => finish(null);
      $('[data-open]', actions).onclick = () => {
        finish(null);
        location.hash = `#/playlist/${status.playlistId}`;
      };
    };

    const poll = async (id) => {
      try {
        const status = await api.get('/api/import/status', { id });
        if (status.state === 'done') {
          await store.refreshPlaylists();
          showResult(status);
          return;
        }
        if (status.state === 'error') {
          toast(friendlyError({ message: status.error }), 'error', 6000);
          $('[data-form]', dialog).hidden = false;
          progress.hidden = true;
          $('[data-start]', dialog).disabled = false;
          return;
        }
        const percent = status.total ? Math.round((status.done / status.total) * 100) : 0;
        progress.innerHTML = status.state === 'reading'
          ? `<div class="muted">קורא את הפלייליסט…</div><div class="progress"><i style="width:4%"></i></div>`
          : `<div class="muted">מחפש את השירים ביוטיוב: ${status.done} מתוך ${status.total} · נמצאו ${status.matched}</div>
             <div class="progress"><i style="width:${percent}%"></i></div>`;
      } catch (error) {
        progress.innerHTML = `<div class="muted">${esc(friendlyError(error))}</div>`;
      }
      timer = setTimeout(() => poll(id), POLL_MS);
    };

    $('[data-start]', dialog).onclick = async (e) => {
      const source = $('[data-source]', dialog).value.trim();
      if (!source) {
        toast('הדביקו קישור או רשימת שירים', 'error');
        return;
      }
      e.currentTarget.disabled = true;
      try {
        const { id } = await api.post('/api/import', { source, name: $('[data-name]', dialog).value.trim() });
        $('[data-form]', dialog).hidden = true;
        progress.hidden = false;
        progress.innerHTML = '<div class="muted">מתחיל…</div>';
        poll(id);
      } catch (error) {
        e.currentTarget.disabled = false;
        toast(friendlyError(error), 'error');
      }
    };
  });
}
