import { api, friendlyError, native } from '../api.js';
import { EQ_BANDS, EQ_PRESETS, parseBands } from '../player.js';
import { installUpdate, store } from '../store.js';
import { $, esc, icon, toast, confirmDialog, paintRange, promptDialog, timeAgo } from '../ui.js';

const FORMATS = [
  ['m4a', 'M4A (AAC): האיכות המקורית, בלי המרה'],
  ['mp3', 'MP3: עובד בכל מכשיר, כולל רכב ודיסק און קי'],
  ['opus', 'Opus: קבצים קטנים באיכות גבוהה'],
];

function formatNote(info, isAndroid) {
  if (!isAndroid) return '';
  const opus = info.formats.includes('opus') ? ' שמירה ב-Opus מהירה, כי השיר לא מקודד מחדש.' : '';
  return `בטלפון, שמירה ב-MP3 מוסיפה כמה שניות של המרה לכל שיר.${opus}`;
}
const NUMERIC_KEYS = new Set(['concurrency', 'mp3_bitrate', 'crossfade']);
const TAILSCALE_DOWNLOAD = 'https://tailscale.com/download/windows';

const toggle = (key, value) =>
  `<label class="switch"><input type="checkbox" data-key="${key}" ${value ? 'checked' : ''}><span></span></label>`;

const setting = (title, description, control) => `
  <div class="setting">
    <div class="grow"><div>${title}</div>${description ? `<div>${description}</div>` : ''}</div>
    ${control}
  </div>`;

const qr = (url) => `<img class="qr" src="/api/qr?text=${encodeURIComponent(url)}" alt="קוד QR לכתובת" width="148" height="148">`;

function scanText(scan, where) {
  if (!scan) return '';
  if (scan.running) return `מחפש מוזיקה ${where}…`;
  if (scan.error) return `החיפוש נכשל: ${esc(scan.error)}`;
  if (!scan.finished_at) return 'החיפוש יתחיל בעוד כמה שניות';
  return `נמצאו ${Number(scan.found).toLocaleString('he-IL')} שירים ${where}`;
}

const crossfadeText = (seconds) => (Number(seconds) ? `${seconds} שניות` : 'כבוי');

function soundCard(settings, info) {
  const effects = native || !info.remote;
  const gains = parseBands(settings.eq_bands);
  return `
    <h2 class="section-title">${icon('wave')} סאונד</h2>
    <div class="card">
      ${effects ? '' : '<p class="muted" style="margin-top:0">האקולייזר, העוצמה האחידה והמעבר החלק עובדים באפליקציה במחשב ובטלפון אנדרואיד.</p>'}
      ${setting('עוצמה אחידה', 'שירים חזקים וחלשים יתנגנו בעוצמה דומה, בלי לגעת בווליום כל שיר', toggle('normalize_volume', settings.normalize_volume))}
      ${setting('מעבר חלק בין שירים', 'השיר הבא מתחיל להתנגן בזמן שהקודם נחלש', `
        <div class="row-flex">
          <input type="range" min="0" max="12" dir="ltr" data-key="crossfade" value="${settings.crossfade}" style="width:140px">
          <b data-crossfade-value style="min-width:64px">${crossfadeText(settings.crossfade)}</b>
        </div>`)}
      ${setting('אקולייזר', 'חיזוק בס, שירה ברורה, או כיוון ידני', toggle('eq_enabled', settings.eq_enabled))}
      <div data-eq ${settings.eq_enabled ? '' : 'hidden'}>
        <div class="chips" style="margin:6px 0 4px">
          ${Object.entries(EQ_PRESETS).map(([key, preset]) => `
            <button class="tab ${settings.eq_preset === key ? 'active' : ''}" data-preset="${key}">${preset.label}</button>`).join('')}
        </div>
        <div class="eq" dir="ltr">
          ${EQ_BANDS.map((band, i) => `
            <label>
              <span class="eq-value" data-eq-value="${i}">${gains[i] > 0 ? '+' : ''}${gains[i]}</span>
              <input type="range" min="-12" max="12" step="1" value="${gains[i]}" data-band="${i}" aria-label="${band.label}">
              <span>${band.label}</span>
            </label>`).join('')}
        </div>
      </div>
    </div>`;
}

function syncCard(info) {
  const isAndroid = info.platform === 'android';
  return `
    <h2 class="section-title">${icon('sync')} סנכרון בין הטלפון למחשב</h2>
    <div class="card" data-sync-card>
      <p class="muted" style="margin-top:0">
        הלייקים, הפלייליסטים, הזמרים שבמעקב וההיסטוריה יהיו זהים בטלפון ובמחשב.
        ${isAndroid ? 'שירים שבחרתם לשמור בטלפון יועתקו מהמחשב דרך ה-WiFi בבית, ושירים שנמצאים רק במחשב יתנגנו ממנו כשאתם בבית.' : ''}
      </p>
      <div data-sync-body><div class="spinner" style="margin:10px auto"></div></div>
    </div>`;
}

export async function render(el, params, ctx) {
  el.innerHTML = '<div class="spinner"></div>';
  let settings;
  let info;
  try {
    [settings, info] = await Promise.all([store.loadSettings(), store.loadInfo()]);
  } catch (error) {
    if (ctx.alive) el.innerHTML = `<div class="empty">${icon('warning')}<div>${esc(friendlyError(error))}</div></div>`;
    return;
  }
  if (!ctx.alive) return;

  const local = !info.remote;
  const actions = new Set(info.actions);
  const canLogin = local && (actions.has('login') || typeof native?.login === 'function');
  const isAndroid = info.platform === 'android';
  const where = isAndroid ? 'בטלפון' : 'במחשב';

  el.innerHTML = `
    <div class="page-head"><h1>הגדרות</h1></div>

    <h2 class="section-title">${icon('eye-off')} מצב מסונן</h2>
    <div class="card" data-filter-card>${local ? '<div class="spinner" style="margin:10px auto"></div>' : `
      <div class="muted">${info.filter?.active ? 'מצב מסונן פועל באפליקציה במחשב.' : 'מצב מסונן כבוי. אפשר להפעיל אותו באפליקציה במחשב.'}</div>`}
    </div>

    <h2 class="section-title">ניגון</h2>
    <div class="card">
      ${setting('להמשיך לנגן שירים דומים', 'כשהתור נגמר, ממשיכים עם שירים דומים לאחרונים שהתנגנו', toggle('autoplay', settings.autoplay))}
      ${setting('לשמור לאופליין שירים שאהבתי', 'כל שיר שתסמנו בלב יישמר גם במכשיר', toggle('auto_download_likes', settings.auto_download_likes))}
    </div>

    ${soundCard(settings, info)}

    ${local ? syncCard(info) : ''}

    <h2 class="section-title">שירים חדשים מזמרים שבמעקב</h2>
    <div class="card">
      ${setting(isAndroid ? 'התראה כשיוצא שיר חדש' : 'הודעה כשיוצא שיר חדש', 'בודקים כל כמה שעות. הרשימה נמצאת בפעמון שבמסך הבית.', toggle('notify_new_releases', settings.notify_new_releases))}
      ${setting('לשמור שירים חדשים אוטומטית לאופליין', '', toggle('auto_download_new_releases', settings.auto_download_new_releases))}
      <div class="setting"><div class="grow"><div>זמרים שבמעקב</div><div>${store.followList.length} זמרים</div></div>
        <a class="btn btn-sm" href="#/new">${icon('bell')} יצא עכשיו</a></div>
    </div>

    <h2 class="section-title">${icon('disk')} מקום אחסון</h2>
    <div class="card">
      <div class="setting"><div class="grow"><div>ניהול מקום</div><div>כמה מקום השירים תופסים, ומחיקת שירים שלא שמעתם הרבה זמן</div></div>
        <a class="btn btn-sm" href="#/storage">${icon('disk')} לניהול</a></div>
      ${setting('איכות השמירה וההשמעה', 'חיסכון: קבצים קטנים בערך בחצי ופחות גלישה, באיכות קצת נמוכה יותר', `
        <select class="input" data-key="audio_quality">
          <option value="high" ${settings.audio_quality !== 'saver' ? 'selected' : ''}>גבוהה</option>
          <option value="saver" ${settings.audio_quality === 'saver' ? 'selected' : ''}>חיסכון במקום</option>
        </select>`)}
    </div>

    ${local ? `
    <h2 class="section-title">מוזיקה ${where}</h2>
    <div class="card">
      ${setting(`להציג שירים שכבר יש ${where}`, isAndroid
        ? 'כל המוזיקה שאנדרואיד מכיר בטלפון תופיע בספרייה'
        : 'קבצי מוזיקה מהתיקיות למטה יופיעו בספרייה. שירים שכבר יש לכם לא יורדו שוב.', toggle('scan_local_music', settings.scan_local_music))}
      ${!isAndroid ? `
      <div class="setting" style="display:block">
        <div style="font-weight:600">תיקיות שנסרקות</div>
        <div class="folder-list" data-folders></div>
        ${actions.has('add_music_folder') ? `<button class="btn btn-sm" style="margin-top:10px" data-add-folder>${icon('plus')} הוספת תיקייה</button>` : ''}
      </div>` : ''}
      <div class="setting">
        <div class="grow"><div>מצב</div><div data-scan-status>${scanText(info.scan, where)}</div></div>
        <button class="btn btn-sm" data-rescan>${icon('retry')} חפש עכשיו</button>
      </div>
    </div>` : ''}

    ${local ? `
    <h2 class="section-title">חשבון יוטיוב</h2>
    <div class="card">
      <div data-account-status></div>
      <p class="muted" style="margin:10px 0 0">
        אם יוטיוב חוסם את ההשמעה או את השמירה לאופליין, התחברות לחשבון בדרך כלל פותרת את זה. ההתחברות נעשית ישירות מול גוגל,
        והאפליקציה שומרת את פרטי ההתחברות (עוגיות) רק במכשיר הזה.
        שמירה של מאות שירים עם החשבון עלולה לגרום לגוגל להגביל אותו, לכן עדיף להשתמש בחשבון משני.
      </p>
      <div class="row-flex" style="margin-top:14px">
        ${canLogin ? `<button class="btn btn-primary" data-login>${icon('user')} התחברות לחשבון יוטיוב</button>` : ''}
        <button class="btn" data-import>${icon('link')} ייבוא קובץ cookies.txt</button>
        <button class="btn btn-danger" data-logout hidden>התנתקות</button>
        <input type="file" accept=".txt,.json,text/plain,application/json" data-import-file hidden>
      </div>
      ${setting('להשתמש בחשבון', 'אפשר לכבות זמנית בלי למחוק את ההתחברות', toggle('use_cookies', settings.use_cookies))}
    </div>` : ''}

    <h2 class="section-title">שמירה לאופליין</h2>
    <div class="card">
      ${local && info.folderChoices?.length ? setting('תיקיית השירים', 'שירים חדשים יישמרו בתיקייה שתבחרו. שירים שכבר שמורים נשארים במקומם.', `
        <select class="input" data-key="music_dir">
          ${info.folderChoices.map((choice) => `<option value="${esc(choice.path)}" ${choice.path === settings.music_dir ? 'selected' : ''}>${esc(choice.label)}</option>`).join('')}
        </select>`) : local ? setting('תיקיית השירים', `<span class="code" data-music-dir>${esc(settings.music_dir)}</span>`, `
        <div class="row-flex">
          ${actions.has('choose_folder') ? `<button class="btn btn-sm" data-choose-folder>${icon('folder')} שינוי</button>` : ''}
          ${actions.has('open_folder') ? '<button class="btn btn-sm" data-open-folder>פתח</button>' : ''}
        </div>`) : ''}
      ${setting('פורמט', formatNote(info, isAndroid), `
        <select class="input" data-key="audio_format" ${info.formats.length > 1 ? '' : 'disabled'}>
          ${FORMATS.filter(([value]) => info.formats.includes(value)).map(([value, label]) => `<option value="${value}" ${value === settings.audio_format ? 'selected' : ''}>${label}</option>`).join('')}
        </select>`)}
      <div data-mp3-row ${settings.audio_format === 'mp3' ? '' : 'hidden'}>
        ${setting('איכות MP3', 'המקור ביוטיוב הוא בערך 130–160 kbps, אז מעל 192 הקבצים רק גדלים', `
          <select class="input" data-key="mp3_bitrate">
            ${[128, 192, 256, 320].map((rate) => `<option value="${rate}" ${rate === settings.mp3_bitrate ? 'selected' : ''}>${rate} kbps</option>`).join('')}
          </select>`)}
      </div>
      ${setting('שמירות במקביל', 'יותר במקביל זה מהיר יותר, אבל יוטיוב עלול לחסום מהר יותר. מומלץ 3 עד 6.', `
        <div class="row-flex">
          <input type="range" min="1" max="16" dir="ltr" data-key="concurrency" value="${settings.concurrency}" style="width:150px">
          <b data-concurrency-value>${settings.concurrency}</b>
        </div>`)}
      ${setting('תיקייה נפרדת לכל זמר', '', toggle('folder_per_artist', settings.folder_per_artist))}
      ${setting('להוסיף תמונת עטיפה לקובץ', 'התמונה תופיע בנגנים אחרים ובטלפון', toggle('embed_cover', settings.embed_cover))}
      ${setting('לדלג על שירים שכבר שמורים', '', toggle('skip_existing', settings.skip_existing))}
    </div>

    ${local && !isAndroid ? `
    <h2 class="section-title">גישה מהאייפון וממכשירים אחרים בבית</h2>
    <div class="card">
      ${setting('לאפשר גישה מהרשת הביתית', 'השינוי ייכנס לתוקף אחרי שתפתחו את האפליקציה מחדש', toggle('remote_access', settings.remote_access))}
      ${setting('הכתובת לפתיחה בטלפון', info.lanUrls.length
        ? `${info.lanUrls.map((url) => `<span class="code">${esc(url)}</span>`).join(' ')}<br>סרקו את הקוד במצלמה של האייפון:`
        : 'הגישה כבויה, או שהמחשב לא מחובר לרשת', info.lanUrls.length ? qr(info.lanUrls[0]) : '')}
      ${setting('קוד גישה', 'מומלץ. בלי קוד, כל מי שמחובר ל-WiFi בבית יכול להיכנס.', `
        <div class="row-flex">
          <input class="input" data-pin inputmode="numeric" maxlength="8" placeholder="ללא קוד" value="${esc(settings.remote_pin)}" style="width:130px" dir="ltr">
          <button class="btn btn-sm" data-save-pin>שמירה</button>
        </div>`)}
      <p class="muted" style="margin:12px 0 0">
        באייפון: פתחו את Safari, הקלידו את הכתובת (או סרקו את הקוד), ואז לחצו על כפתור השיתוף ← "הוספה למסך הבית".
        האייפון והמחשב צריכים להיות מחוברים לאותה רשת, והאפליקציה במחשב צריכה להיות פתוחה.
      </p>
    </div>

    <h2 class="section-title">${icon('globe')} גישה מהאייפון מחוץ לבית</h2>
    <div class="card" data-remote-card><div class="spinner" style="margin:10px auto"></div></div>` : ''}

    <h2 class="section-title">אודות</h2>
    <div class="card muted">
      ${isAndroid && local ? `<div class="setting" data-update-row>
        <div class="grow"><div>עדכונים</div><div data-update-text>${store.queue?.sync?.update
          ? `גרסה ${esc(store.queue.sync.update.versionName)} מחכה במחשב`
          : 'הטלפון מקבל עדכונים מהמחשב המחובר, בזמן הסנכרון'}</div></div>
        <button class="btn btn-sm btn-primary" data-install-update ${store.queue?.sync?.update ? '' : 'hidden'}>${icon('download')} עדכון</button>
      </div>` : ''}
      שירים ${esc(info.version)}. הורדה והשמעה מיוטיוב עלולות להפר את תנאי השימוש של יוטיוב ואת זכויות היוצרים על השירים. השימוש באחריותכם.
    </div>`;

  for (const range of el.querySelectorAll('input[type=range]')) paintRange(range);

  // region music folders & scanning

  const renderFolders = (roots) => {
    const target = $('[data-folders]', el);
    if (!target) return;
    const extra = new Set(store.settings.extra_music_folders.split('\n').filter(Boolean));
    target.innerHTML = roots.length
      ? roots.map((path) => `<div><span class="code ellipsis">${esc(path)}</span>
          ${extra.has(path) ? `<button class="icon-btn" data-remove-folder="${esc(path)}" title="הסר">${icon('close')}</button>` : ''}</div>`).join('')
      : '<div class="muted small">אין תיקיות</div>';
  };
  renderFolders(info.musicRoots || []);

  const renderScan = (scan) => {
    const status = $('[data-scan-status]', el);
    if (status) status.innerHTML = scanText(scan, where);
  };
  ctx.onCleanup(store.on('scan', renderScan));

  // endregion

  // region account

  const renderAccount = (status) => {
    const target = $('[data-account-status]', el);
    if (!target) return;
    target.innerHTML = status.loggedIn
      ? '<span class="pill ok">מחובר לחשבון יוטיוב</span>'
      : status.present
        ? '<span class="pill">נטען קובץ עוגיות, אבל לא נמצאה בו התחברות</span>'
        : '<span class="pill">לא מחובר</span>';
    $('[data-logout]', el).hidden = !status.present;
  };

  const refreshAccount = async () => {
    try {
      renderAccount((await api.get('/api/info')).cookies);
    } catch {
      // try again on the next tick
    }
  };

  if (local) {
    renderAccount(info.cookies);
    // Sign-in happens in a separate window, so keep the status fresh while this page is open
    const timer = setInterval(refreshAccount, 3000);
    ctx.onCleanup(() => clearInterval(timer));
  }

  // endregion

  // region sync

  let codeTimer = null;
  ctx.onCleanup(() => clearInterval(codeTimer));

  const renderComputerSync = async (code = null) => {
    const body = $('[data-sync-body]', el);
    if (!body) return;
    let devices = [];
    try {
      devices = await api.get('/api/sync/devices');
    } catch {
      // shown as no devices
    }
    if (!ctx.alive) return;
    clearInterval(codeTimer);
    body.innerHTML = `
      ${code ? `
        <div class="sync-code">
          <div class="muted">בטלפון: הגדרות ← סנכרון ← "חיפוש מחשב", בחרו את <b>${esc(code.name)}${info.filter?.forced ? ' (מסונן)' : ''}</b> והקלידו את הקוד:</div>
          <b class="sync-digits" dir="ltr">${esc(code.code.replace(/(\d{3})(\d{3})/, '$1 $2'))}</b>
          <div class="muted small" data-code-left></div>
          ${code.listening ? '' : `<div class="pill err">הגישה מהרשת הביתית כבויה. הפעילו אותה למטה ופתחו את האפליקציה מחדש.</div>`}
        </div>` : `<button class="btn btn-primary" data-sync-code>${icon('phone')} חיבור טלפון</button>`}
      ${devices.length ? `<div class="sync-devices">${devices.map((d) => `
        <div class="setting">
          <div class="grow"><div>${icon('phone')} ${esc(d.name)}</div><div>סונכרן ${timeAgo(d.last_seen)}</div></div>
          <button class="btn btn-sm btn-ghost" data-sync-remove="${d.id}">ניתוק</button>
        </div>`).join('')}</div>` : ''}`;
    if (code) {
      const left = $('[data-code-left]', body);
      const tick = () => {
        const seconds = Math.max(0, Math.round(code.expires - Date.now() / 1000));
        left.textContent = seconds ? `הקוד בתוקף עוד ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} דקות` : 'הקוד פג תוקף';
        if (!seconds) renderComputerSync();
      };
      tick();
      codeTimer = setInterval(tick, 1000);
    }
  };

  const renderPhoneSync = async () => {
    const body = $('[data-sync-body]', el);
    if (!body) return;
    let status;
    try {
      status = await api.get('/api/sync/status');
    } catch (error) {
      body.innerHTML = `<div class="muted">${esc(friendlyError(error))}</div>`;
      return;
    }
    if (!ctx.alive) return;
    if (!status.paired) {
      body.innerHTML = `
        ${status.error === 'unpaired' ? '<div class="pill err" style="margin-bottom:10px">המחשב ביטל את החיבור. אפשר לחבר מחדש.</div>' : ''}
        <ol class="steps">
          <li>במחשב: פתחו את שירים ← הגדרות ← סנכרון ← "חיבור טלפון"</li>
          <li>כאן: לחצו "חיפוש מחשב" (הטלפון והמחשב באותו WiFi)</li>
        </ol>
        <div class="row-flex">
          <button class="btn btn-primary" data-sync-find>${icon('search')} חיפוש מחשב</button>
          <button class="btn btn-ghost btn-sm" data-sync-manual>הזנת כתובת ידנית</button>
        </div>
        <div data-sync-found></div>`;
      return;
    }
    const keep = new Set(status.keepPlaylists);
    body.innerHTML = `
      <div class="setting">
        <div class="grow"><div>${icon('laptop')} מחובר ל-${esc(status.peerName || 'מחשב')}</div>
          <div>${status.syncing ? 'מסנכרן…' : status.error ? `הסנכרון האחרון נכשל: ${esc(friendlyError({ message: status.error }))}` : `סונכרן ${timeAgo(status.lastSync)}`}</div></div>
        <button class="btn btn-sm" data-sync-now ${status.syncing ? 'disabled' : ''}>${icon('sync')} סנכרן עכשיו</button>
      </div>
      <div class="setting" style="display:block">
        <div style="font-weight:600">לשמור בטלפון (יועתקו מהמחשב כשאתם בבית)</div>
        <div class="keep-list">
          <label class="row-flex"><input type="checkbox" class="check" data-keep-liked ${status.keepLiked ? 'checked' : ''}> שירים שאהבתי (${store.likes.size})</label>
          ${store.playlists.map((p) => `
            <label class="row-flex"><input type="checkbox" class="check" data-keep-playlist="${esc(p.uid)}" ${keep.has(p.uid) ? 'checked' : ''}> ${esc(p.name)} (${p.count})</label>`).join('')}
        </div>
      </div>
      <div class="row-flex" style="margin-top:10px"><button class="btn btn-sm btn-ghost btn-danger" data-sync-unpair>ניתוק מהמחשב</button></div>`;
  };

  const renderSync = () => (isAndroid ? renderPhoneSync() : renderComputerSync());
  if (local) {
    renderSync();
    let lastSync = null;
    ctx.onCleanup(store.on('queue', (snapshot) => {
      const key = JSON.stringify(snapshot.sync);
      if (lastSync !== null && key !== lastSync && isAndroid) renderPhoneSync();
      lastSync = key;
    }));
  }

  const connectTo = async (url, name) => {
    const code = await promptDialog({
      title: `חיבור ל-${name}`,
      message: 'הקלידו את הקוד שמופיע עכשיו במחשב (הגדרות ← סנכרון).',
      inputType: 'tel',
      placeholder: '123 456',
      confirmText: 'חיבור',
    });
    if (code === null) return;
    toast('מתחבר…', 'info', 1500);
    await api.post('/api/sync/connect', { url, code: code.replace(/\D/g, '') });
    toast('הטלפון מחובר למחשב. מסנכרן…');
    await renderPhoneSync();
  };

  // endregion

  // region filtered mode

  const askFilterCode = async (status) => {
    if (!status.locked) return '';
    const code = await promptDialog({ title: 'קוד הסינון', message: 'השינוי הזה מחליש את הסינון, ולכן צריך את הקוד.', inputType: 'password', confirmText: 'אישור' });
    return code === null ? null : code.trim();
  };

  let filterStatus = null;
  const renderFilter = async () => {
    const card = $('[data-filter-card]', el);
    if (!card || !local) return;
    try {
      filterStatus = await api.get('/api/filter');
    } catch (error) {
      card.innerHTML = `<div class="muted">${esc(friendlyError(error))}</div>`;
      return;
    }
    if (!ctx.alive) return;
    const s = filterStatus;
    const allowed = s.rules.filter((r) => r.kind === 'artist' && r.verdict === 'allow');
    const blocked = s.rules.filter((r) => r.verdict === 'block');
    const row = (label, buttons) => `<div class="filter-row"><span class="grow ellipsis">${esc(label)}</span>${buttons}</div>`;
    card.innerHTML = `
      ${s.forced
        ? `<p style="margin-top:0"><b>זו הגרסה המסוננת של שירים.</b> הסינון תמיד פועל, ואי אפשר לכבות אותו או להחליש אותו.</p>`
        : setting('מצב מסונן', 'מסתיר שירים של זמרות ודואטים עם זמרות, שירים עם מילים בוטות, וכל התמונות. חל על המכשיר הזה, כולל Android Auto.', `
          <label class="switch"><input type="checkbox" data-filter-toggle ${s.active ? 'checked' : ''}><span></span></label>`)}
      ${s.active || s.forced ? `
        ${s.forced ? '' : setting('זמרים שלא הצלחנו לזהות', 'לפעמים אי אפשר לדעת מי שר. במצב זהיר הם מוסתרים עד שתאשרו אותם.', `
          <select class="input" data-filter-unknown>
            <option value="hide" ${s.strict ? 'selected' : ''}>להסתיר (זהיר)</option>
            <option value="show" ${s.strict ? '' : 'selected'}>להציג</option>
          </select>`)}
        ${s.forced ? '' : setting('קוד נעילה', s.locked ? 'הסינון נעול: כדי לכבות אותו או לאשר זמרים צריך את הקוד.' : 'עם קוד, אי אפשר לכבות את הסינון או לאשר זמרים בלי הקוד.', `
          <div class="row-flex">
            <input class="input" data-filter-pin inputmode="numeric" maxlength="8" placeholder="${s.locked ? 'קוד חדש' : '4 עד 8 ספרות'}" style="width:130px" dir="ltr" type="password">
            <button class="btn btn-sm" data-filter-save-pin>${s.locked ? 'החלפה' : 'נעילה'}</button>
            ${s.locked ? '<button class="btn btn-sm btn-ghost" data-filter-clear-pin>ביטול הנעילה</button>' : ''}
          </div>`)}
        ${s.checking ? `<p class="muted small">בודקים עכשיו ${s.checking} זמרים. שירים שלהם יופיעו אחרי הבדיקה.</p>` : ''}
        <p class="muted small">שמעתם שירת נשים בשיר שלא הוסתר? בתפריט ⋯ של השיר יש "לא מתאים: הסתר".
        הזיהוי לפי הזמרים הרשומים בשיר, ולכן קולות רקע או מקהלה בשיר של זמר לא תמיד מזוהים.</p>
        ${s.unknown.length ? `<details class="filter-list"><summary>זמרים שלא זוהו (${s.unknown.length})</summary>
          ${s.unknown.map((u) => row(u.name, `
            ${s.forced ? '' : `<button class="btn btn-sm btn-ghost" data-filter-allow="${esc(u.name)}">להציג</button>`}
            <button class="btn btn-sm btn-ghost" data-filter-block="${esc(u.name)}">להסתיר תמיד</button>`)).join('')}</details>` : ''}
        ${allowed.length ? `<details class="filter-list"><summary>זמרים שאישרתם (${allowed.length})</summary>
          ${allowed.map((r) => row(r.label || r.key, `<button class="btn btn-sm btn-ghost" data-filter-remove="${esc(r.kind)}|${esc(r.key)}">ביטול האישור</button>`)).join('')}</details>` : ''}
        ${blocked.length ? `<details class="filter-list"><summary>הסתרתם (${blocked.length})</summary>
          ${blocked.map((r) => row(`${r.kind === 'song' ? 'שיר: ' : ''}${r.label || r.key}`, s.forced ? '' : `<button class="btn btn-sm btn-ghost" data-filter-remove="${esc(r.kind)}|${esc(r.key)}">להציג שוב</button>`)).join('')}</details>` : ''}
      ` : ''}`;
  };
  renderFilter();

  const changeFilter = async (changes, needsCode) => {
    const body = { ...changes };
    if (needsCode) {
      const code = await askFilterCode(filterStatus);
      if (code === null) {
        renderFilter();
        return false;
      }
      body.filterPin = code;
    }
    try {
      await store.saveSettings(body);
      toast('נשמר. האפליקציה נטענת מחדש…', 'info', 1500);
      setTimeout(() => location.reload(), 600);
      return true;
    } catch (error) {
      toast(friendlyError(error), 'error');
      renderFilter();
      return false;
    }
  };

  el.addEventListener('change', async (e) => {
    if (e.target.matches('[data-filter-toggle]')) {
      e.stopImmediatePropagation();
      await changeFilter({ filter_enabled: e.target.checked }, !e.target.checked);
    } else if (e.target.matches('[data-filter-unknown]')) {
      e.stopImmediatePropagation();
      await changeFilter({ filter_unknown: e.target.value }, e.target.value === 'show');
    }
  }, true);

  el.addEventListener('click', async (e) => {
    const button = e.target.closest('[data-filter-save-pin], [data-filter-clear-pin], [data-filter-allow], [data-filter-block], [data-filter-remove]');
    if (!button) return;
    e.stopImmediatePropagation();
    try {
      if (button.matches('[data-filter-save-pin]')) {
        const pin = $('[data-filter-pin]', el).value.trim();
        if (!/^\d{4,8}$/.test(pin)) {
          toast('קוד הסינון צריך להיות 4 עד 8 ספרות.', 'error');
          return;
        }
        await changeFilter({ filter_pin: pin }, true);
      } else if (button.matches('[data-filter-clear-pin]')) {
        await changeFilter({ filter_pin: '' }, true);
      } else if (button.matches('[data-filter-allow]')) {
        const code = await askFilterCode(filterStatus);
        if (code === null) return;
        await api.post('/api/filter/rule', { kind: 'artist', name: button.dataset.filterAllow, verdict: 'allow', filterPin: code });
        toast('אושר');
        renderFilter();
      } else if (button.matches('[data-filter-block]')) {
        await api.post('/api/filter/rule', { kind: 'artist', name: button.dataset.filterBlock, verdict: 'block' });
        toast('הוסתר');
        renderFilter();
      } else if (button.matches('[data-filter-remove]')) {
        const [kind, key] = button.dataset.filterRemove.split('|');
        const rule = filterStatus.rules.find((r) => r.kind === kind && r.key === key);
        const code = rule?.verdict === 'block' ? await askFilterCode(filterStatus) : '';
        if (code === null) return;
        await api.post('/api/filter/rule/remove', { kind, key, filterPin: code });
        renderFilter();
      }
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  }, true);

  // endregion

  // region access from outside the home

  const renderRemote = async () => {
    const card = $('[data-remote-card]', el);
    if (!card) return;
    let remote;
    try {
      remote = await api.get('/api/remote');
    } catch (error) {
      card.innerHTML = `<div class="muted">${esc(friendlyError(error))}</div>`;
      return;
    }
    if (!ctx.alive) return;
    if (remote.tailscaleUrls.length) {
      const url = remote.tailscaleUrls[0];
      card.innerHTML = `
        <div class="setting">
          <div class="grow"><div>${icon('check')} Tailscale מחובר</div>
            <div>באייפון, כשגם בו Tailscale פועל, פתחו את הכתובת בכל מקום: <span class="code">${esc(url)}</span></div></div>
          ${qr(url)}
        </div>
        ${remote.pinSet ? '' : '<div class="pill err">מומלץ להגדיר קוד גישה למעלה</div>'}
        <p class="muted small">המחשב צריך להיות דלוק והאפליקציה פתוחה.</p>`;
    } else {
      card.innerHTML = `
        <p class="muted" style="margin-top:0">האייפון יכול לשמוע את כל השירים מהמחשב גם מחוץ לבית, דרך Tailscale: אפליקציה חינמית שמחברת את המכשירים שלכם בצורה מאובטחת.</p>
        ${remote.tailscaleInstalled && remote.listening ? '<div class="pill err" style="margin-bottom:10px">Tailscale מותקן במחשב אבל לא מחובר. פתחו אותו והתחברו לחשבון.</div>' : ''}
        ${remote.listening ? '' : '<div class="pill err" style="margin-bottom:10px">קודם הפעילו את "לאפשר גישה מהרשת הביתית" ופתחו את האפליקציה מחדש.</div>'}
        <ol class="steps">
          <li>התקינו Tailscale במחשב והתחברו (למשל עם חשבון גוגל)</li>
          <li>התקינו Tailscale באייפון מה-App Store והתחברו לאותו חשבון</li>
          <li>חזרו למסך הזה: תופיע כתובת וקוד לסריקה</li>
        </ol>
        <div class="row-flex">
          <button class="btn btn-primary" data-open-url="${TAILSCALE_DOWNLOAD}">${icon('download')} להורדת Tailscale</button>
          <button class="btn btn-ghost btn-sm" data-remote-refresh>${icon('retry')} בדוק שוב</button>
        </div>`;
    }
  };
  if (local && !isAndroid) renderRemote();

  // endregion

  const save = async (changes) => {
    try {
      const updated = await store.saveSettings(changes);
      toast('נשמר', 'info', 1500);
      return updated;
    } catch (error) {
      toast(friendlyError(error), 'error');
      return null;
    }
  };

  const drawBands = (gains) => {
    gains.forEach((gain, i) => {
      const slider = $(`[data-band="${i}"]`, el);
      if (slider) slider.value = gain;
      const value = $(`[data-eq-value="${i}"]`, el);
      if (value) value.textContent = `${gain > 0 ? '+' : ''}${gain}`;
    });
  };

  el.addEventListener('input', (e) => {
    if (e.target.matches('[data-key="concurrency"]')) $('[data-concurrency-value]', el).textContent = e.target.value;
    if (e.target.matches('[data-key="crossfade"]')) $('[data-crossfade-value]', el).textContent = crossfadeText(e.target.value);
    if (e.target.matches('[data-band]')) {
      const value = Number(e.target.value);
      $(`[data-eq-value="${e.target.dataset.band}"]`, el).textContent = `${value > 0 ? '+' : ''}${value}`;
    }
  });

  el.addEventListener('change', async (e) => {
    if (e.target.matches('[data-band]')) {
      const gains = [...el.querySelectorAll('[data-band]')].map((input) => Number(input.value));
      for (const tab of el.querySelectorAll('[data-preset]')) tab.classList.toggle('active', tab.dataset.preset === 'custom');
      await save({ eq_bands: gains.join(','), eq_preset: 'custom' });
      return;
    }
    if (e.target.matches('[data-keep-liked], [data-keep-playlist]')) {
      try {
        await api.post('/api/sync/keep', {
          liked: $('[data-keep-liked]', el).checked,
          playlists: [...el.querySelectorAll('[data-keep-playlist]:checked')].map((input) => input.dataset.keepPlaylist),
        });
        toast('נשמר. השירים יועתקו מהמחשב ברקע.', 'info', 2500);
      } catch (error) {
        toast(friendlyError(error), 'error');
      }
      return;
    }
    const input = e.target.closest('[data-key]');
    if (!input) return;
    const key = input.dataset.key;
    let value = input.type === 'checkbox' ? input.checked : input.value;
    if (NUMERIC_KEYS.has(key)) value = Number(value);
    await save({ [key]: value });
    if (key === 'audio_format') $('[data-mp3-row]', el).hidden = value !== 'mp3';
    if (key === 'eq_enabled') $('[data-eq]', el).hidden = !value;
  });

  el.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    try {
      if (button.matches('[data-preset]')) {
        const preset = EQ_PRESETS[button.dataset.preset];
        for (const tab of el.querySelectorAll('[data-preset]')) tab.classList.toggle('active', tab === button);
        const gains = preset.gains || parseBands(store.settings.eq_bands);
        drawBands(gains);
        await save({ eq_preset: button.dataset.preset, eq_bands: gains.join(',') });
      } else if (button.matches('[data-login]')) {
        if (typeof native?.login === 'function') native.login();
        else await api.post('/api/action', { name: 'login' });
        toast('נפתח חלון התחברות. אחרי שתתחברו הוא ייסגר לבד.', 'info', 6000);
      } else if (button.matches('[data-import]')) {
        $('[data-import-file]', el).click();
      } else if (button.matches('[data-logout]')) {
        const ok = await confirmDialog({ title: 'להתנתק מחשבון יוטיוב?', message: 'פרטי ההתחברות יימחקו מהמכשיר הזה.', confirmText: 'התנתקות', danger: true });
        if (!ok) return;
        await api.post('/api/cookies/clear');
        await refreshAccount();
      } else if (button.matches('[data-choose-folder]')) {
        const result = await api.post('/api/action', { name: 'choose_folder' });
        if (result.music_dir) {
          $('[data-music-dir]', el).textContent = result.music_dir;
          await store.loadSettings();
          toast('התיקייה עודכנה');
        }
      } else if (button.matches('[data-open-folder]')) {
        await api.post('/api/action', { name: 'open_folder' });
      } else if (button.matches('[data-save-pin]')) {
        await save({ remote_pin: $('[data-pin]', el).value.trim() });
      } else if (button.matches('[data-add-folder]')) {
        const result = await api.post('/api/action', { name: 'add_music_folder' });
        await store.loadSettings();
        renderFolders(result.folders);
      } else if (button.matches('[data-remove-folder]')) {
        const result = await api.post('/api/action', { name: 'remove_music_folder', path: button.dataset.removeFolder });
        await store.loadSettings();
        renderFolders(result.folders);
      } else if (button.matches('[data-rescan]')) {
        await api.post('/api/scan');
        renderScan({ running: true });
      } else if (button.matches('[data-sync-code]')) {
        renderComputerSync(await api.post('/api/sync/code'));
      } else if (button.matches('[data-sync-remove]')) {
        const ok = await confirmDialog({ title: 'לנתק את הטלפון?', message: 'הטלפון יפסיק להסתנכרן עם המחשב. השירים והפלייליסטים יישארו בשניהם.', confirmText: 'ניתוק', danger: true });
        if (!ok) return;
        await api.post('/api/sync/devices/remove', { id: Number(button.dataset.syncRemove) });
        renderComputerSync();
      } else if (button.matches('[data-sync-find]')) {
        const found = $('[data-sync-found]', el);
        button.disabled = true;
        found.innerHTML = '<div class="muted" style="margin-top:10px">מחפש מחשבים ברשת… (כמה שניות)</div>';
        try {
          const computers = await api.post('/api/sync/discover');
          if (!ctx.alive) return;
          found.innerHTML = computers.length
            ? `<div class="picker" style="margin-top:10px">${computers.map((c) => `
                <button class="picker-item" data-sync-pick="${esc(c.url)}" data-name="${esc(c.name)}${c.filtered ? ' (מסונן)' : ''}">
                  <span class="picker-img new">${icon('laptop')}</span><span class="grow">${esc(c.name)}${c.filtered ? ' <span class="pill ok">מסונן</span>' : ''}<br><span class="muted small">${esc(c.url)}</span></span>
                </button>`).join('')}</div>`
            : '<div class="pill err" style="margin-top:10px">לא נמצא מחשב. ודאו שהאפליקציה פתוחה במחשב, שהגישה מהרשת הביתית מופעלת בו, ושהטלפון מחובר לאותו WiFi.</div>';
        } finally {
          button.disabled = false;
        }
      } else if (button.matches('[data-sync-pick]')) {
        await connectTo(button.dataset.syncPick, button.dataset.name);
      } else if (button.matches('[data-sync-manual]')) {
        const address = await promptDialog({ title: 'כתובת המחשב', message: 'הכתובת מופיעה במחשב בהגדרות, בגישה מהרשת הביתית.', placeholder: '192.168.1.20', inputType: 'url' });
        if (!address?.trim()) return;
        let url = address.trim().replace(/\/+$/, '');
        if (!/^https?:\/\//.test(url)) url = `http://${url}`;
        if (!/:\d+$/.test(url)) url += ':8765';
        await connectTo(url, url.replace(/^https?:\/\//, ''));
      } else if (button.matches('[data-sync-now]')) {
        button.disabled = true;
        toast('מסנכרן…', 'info', 1500);
        const status = await api.post('/api/sync/now');
        toast(status.error ? friendlyError({ message: status.error }) : 'הסנכרון הושלם', status.error ? 'error' : 'info');
        await store.loadCollection();
        renderPhoneSync();
      } else if (button.matches('[data-sync-unpair]')) {
        const ok = await confirmDialog({ title: 'לנתק מהמחשב?', message: 'הסנכרון ייפסק. השירים והפלייליסטים יישארו בטלפון.', confirmText: 'ניתוק', danger: true });
        if (!ok) return;
        await api.post('/api/sync/unpair');
        renderPhoneSync();
      } else if (button.matches('[data-install-update]')) {
        await installUpdate(button);
      } else if (button.matches('[data-open-url]')) {
        await api.post('/api/action', { name: 'open_url', url: button.dataset.openUrl });
      } else if (button.matches('[data-remote-refresh]')) {
        renderRemote();
      }
    } catch (error) {
      toast(friendlyError(error), 'error', 5000);
    }
  });

  $('[data-import-file]', el)?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    try {
      const result = await api.post('/api/cookies/import', { text: await file.text() });
      toast(result.loggedIn ? 'החשבון חובר בהצלחה' : 'הקובץ נטען, אבל לא נמצאה בו התחברות ליוטיוב', result.loggedIn ? 'info' : 'error', 5000);
      await refreshAccount();
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  });
}
