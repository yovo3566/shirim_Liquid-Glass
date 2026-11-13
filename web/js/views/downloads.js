import { api, friendlyError } from '../api.js';
import { store } from '../store.js';
import { $, esc, icon, cover, formatBytes, toast, confirmDialog } from '../ui.js';

const STATUS = {
  queued: ['בתור', ''],
  running: ['מוריד', 'run'],
  done: ['הושלם', 'ok'],
  skipped: ['כבר קיים', 'ok'],
  error: ['נכשל', 'err'],
  canceled: ['בוטל', ''],
};
const FILTERS = [
  ['all', 'הכל', () => true],
  ['active', 'פעילים', (j) => j.status === 'queued' || j.status === 'running'],
  ['error', 'נכשלו', (j) => j.status === 'error' || j.status === 'canceled'],
  ['done', 'הושלמו', (j) => j.status === 'done' || j.status === 'skipped'],
];
const PAGE = 300;

function stageText(progress) {
  if (!progress || progress.stage === 'starting') return 'מתחיל…';
  if (progress.stage === 'processing') return 'מעבד ומוסיף תגיות…';
  if (progress.stage === 'converting') return 'ממיר את הקובץ…';
  return `${Math.round(progress.percent)}%${progress.speed ? ` · ${formatBytes(progress.speed)}/s` : ''}`;
}

function jobRow(job, progress) {
  const [label, cls] = STATUS[job.status] || [job.status, ''];
  const running = job.status === 'running';
  const p = progress[job.id];
  const actions = [];
  if (running || job.status === 'queued') actions.push(`<button class="icon-btn" data-cancel-job title="בטל">${icon('close')}</button>`);
  if (job.status === 'error' || job.status === 'canceled') actions.push(`<button class="icon-btn" data-retry-job title="נסה שוב">${icon('retry')}</button>`);
  return `
    <div class="track job" data-job="${job.id}">
      ${cover(job.thumb)}
      <div class="grow">
        <div class="t-title ellipsis">${esc(job.title)}</div>
        <div class="t-sub ellipsis">${esc(job.artist)}</div>
      </div>
      <div class="job-progress">
        <div class="row-flex" style="gap:10px;flex-wrap:nowrap"><span class="pill ${cls}">${label}</span>
          <span class="ellipsis" data-detail>${running ? stageText(p) : ''}</span></div>
        ${running ? `<div class="progress"><i data-bar style="width:${p ? p.percent : 0}%"></i></div>` : ''}
        ${job.status === 'error' ? `<div class="ellipsis" style="color:var(--danger)" title="${esc(job.error)}">${esc(friendlyError({ message: job.error }))}</div>` : ''}
      </div>
      <div class="t-actions">${actions.join('')}</div>
    </div>`;
}

export function render(el, params, ctx) {
  let jobs = [];
  let loadedVersion = -1;
  let loading = false;
  let reloadPending = false;
  let filter = 'all';
  let limit = PAGE;

  el.innerHTML = `
    <div class="page-head">
      <h1>הורדות</h1><span class="spacer"></span>
      <div class="row-flex">
        <button class="btn" data-pause>${icon('pause')} <span>השהה</span></button>
        <button class="btn" data-retry>${icon('retry')} נסה שוב את הנכשלים</button>
        <button class="btn btn-danger" data-cancel>${icon('close')} בטל הכל</button>
        <button class="btn" data-clear>${icon('trash')} נקה רשימה</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:20px; padding:24px">
      <div class="row-flex" style="margin-bottom:16px"><b data-summary></b><span class="spacer"></span><span class="muted" data-speed></span></div>
      <div class="progress"><i data-overall></i></div>
      <div class="row-flex muted" style="margin-top:16px;font-size:13px">
        הורדות במקביל:
        <input type="range" min="1" max="16" dir="ltr" data-concurrency style="width:200px">
        <b data-concurrency-value></b>
      </div>
    </div>
    <div class="stats" data-stats></div>
    <div class="tabs" data-filters>${FILTERS.map(([key, label]) => `<button class="tab ${key === filter ? 'active' : ''}" data-filter="${key}">${label}</button>`).join('')}</div>
    <div class="tracks" data-jobs><div class="spinner"></div></div>`;

  const jobsEl = $('[data-jobs]', el);

  const renderSummary = (snapshot) => {
    const { counts, progress, paused } = snapshot;
    const finished = counts.done + counts.skipped + counts.error;
    const total = finished + counts.queued + counts.running;
    const partial = Object.values(progress).reduce((sum, p) => sum + p.percent / 100, 0);
    const active = counts.queued + counts.running;
    const speed = Object.values(progress).reduce((sum, p) => sum + (p.speed || 0), 0);

    let summary = 'אין הורדות פעילות';
    if (active) {
      summary = `${paused ? 'מושהה · ' : ''}${counts.running} מורידים עכשיו · ${counts.queued.toLocaleString('he-IL')} בתור · ${(counts.done + counts.skipped).toLocaleString('he-IL')} מתוך ${total.toLocaleString('he-IL')} הושלמו`;
    } else if (total) {
      summary = counts.error ? `הסתיים. ${counts.error} שירים נכשלו` : 'כל ההורדות הושלמו';
    }
    $('[data-summary]', el).textContent = summary;
    $('[data-speed]', el).textContent = speed ? `${formatBytes(speed)}/s` : '';
    $('[data-overall]', el).style.width = `${total ? ((finished + partial) / total) * 100 : 0}%`;

    const pauseBtn = $('[data-pause]', el);
    pauseBtn.innerHTML = paused ? `${icon('play')} <span>המשך</span>` : `${icon('pause')} <span>השהה</span>`;
    pauseBtn.disabled = !active && !paused;

    $('[data-stats]', el).innerHTML = [
      ['מורידים', counts.running],
      ['בתור', counts.queued],
      ['הושלמו', counts.done + counts.skipped],
      ['נכשלו', counts.error],
    ].map(([label, value]) => `<div class="stat"><b>${value.toLocaleString('he-IL')}</b><span>${label}</span></div>`).join('');
  };

  const renderJobs = () => {
    const match = FILTERS.find(([key]) => key === filter)[2];
    const visible = jobs.filter(match);
    const progress = store.queue?.progress || {};
    if (!visible.length) {
      jobsEl.innerHTML = `<div class="empty">${icon('download')}<div>${jobs.length ? 'אין פריטים בסינון הזה' : 'עוד לא הורדו שירים. חפשו זמר והתחילו להוריד.'}</div></div>`;
      return;
    }
    const more = visible.length - limit;
    jobsEl.innerHTML = visible.slice(0, limit).map((job) => jobRow(job, progress)).join('')
      + (more > 0 ? `<button class="btn" data-more style="margin:14px auto;display:flex">הצג עוד ${more.toLocaleString('he-IL')}</button>` : '');
  };

  const updateProgress = (snapshot) => {
    for (const [id, p] of Object.entries(snapshot.progress)) {
      const row = jobsEl.querySelector(`[data-job="${id}"]`);
      if (!row) continue;
      const bar = row.querySelector('[data-bar]');
      if (bar) bar.style.width = `${p.percent}%`;
      const detail = row.querySelector('[data-detail]');
      if (detail) detail.textContent = stageText(p);
    }
  };

  const loadJobs = async () => {
    if (loading) {
      reloadPending = true;
      return;
    }
    loading = true;
    try {
      const version = store.queue?.listVersion ?? -1;
      jobs = await api.get('/api/queue/jobs');
      loadedVersion = version;
      if (ctx.alive) renderJobs();
    } catch (error) {
      if (ctx.alive) toast(friendlyError(error), 'error');
    } finally {
      loading = false;
      // Coalesce bursts of list changes into one reload per second
      if (reloadPending && ctx.alive) {
        reloadPending = false;
        setTimeout(loadJobs, 1000);
      }
    }
  };

  const onQueue = (snapshot) => {
    renderSummary(snapshot);
    if (snapshot.listVersion !== loadedVersion) loadJobs();
    else updateProgress(snapshot);
  };

  const act = async (path, body, message) => {
    try {
      await api.post(path, body);
      if (message) toast(message, 'ok');
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  };

  el.addEventListener('click', async (e) => {
    const target = e.target.closest('button');
    if (!target) return;
    const jobId = Number(target.closest('[data-job]')?.dataset.job);
    if (target.matches('[data-pause]')) act(store.queue?.paused ? '/api/queue/resume' : '/api/queue/pause');
    else if (target.matches('[data-retry]')) act('/api/queue/retry', {}, 'השירים שנכשלו חזרו לתור');
    else if (target.matches('[data-clear]')) act('/api/queue/clear');
    else if (target.matches('[data-cancel]')) {
      const ok = await confirmDialog({ title: 'לבטל את כל ההורדות?', message: 'שירים שכבר הורדו יישארו בספרייה.', confirmText: 'בטל הכל', danger: true });
      if (ok) act('/api/queue/cancel', {});
    } else if (target.matches('[data-cancel-job]')) act('/api/queue/cancel', { ids: [jobId] });
    else if (target.matches('[data-retry-job]')) act('/api/queue/retry', { ids: [jobId] });
    else if (target.matches('[data-more]')) {
      limit += PAGE;
      renderJobs();
    } else if (target.matches('[data-filter]')) {
      filter = target.dataset.filter;
      limit = PAGE;
      for (const tab of target.parentElement.children) tab.classList.toggle('active', tab === target);
      renderJobs();
    }
  });

  const slider = $('[data-concurrency]', el);
  const sliderValue = $('[data-concurrency-value]', el);
  const showConcurrency = (settings) => {
    slider.value = settings.concurrency;
    sliderValue.textContent = settings.concurrency;
  };
  slider.addEventListener('input', () => { sliderValue.textContent = slider.value; });
  slider.addEventListener('change', async () => {
    try {
      showConcurrency(await store.saveSettings({ concurrency: Number(slider.value) }));
    } catch (error) {
      toast(friendlyError(error), 'error');
    }
  });
  if (store.settings) showConcurrency(store.settings);
  else store.loadSettings().then(showConcurrency).catch(() => {});

  ctx.onCleanup(store.on('queue', onQueue));
  if (store.queue) renderSummary(store.queue);
  loadJobs();
}
