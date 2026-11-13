// Building blocks shared by the screens: tiles, shelves, headers and the big play button.

import { friendlyError } from '../api.js';
import { player } from '../player.js';
import { esc, icon, cover, mosaic, likedCover } from '../ui.js';

export const errorBox = (error) => `<div class="empty">${icon('warning')}<div>${esc(friendlyError(error))}</div></div>`;
export const loading = () => '<div class="spinner"></div>';

/** A square (or round) tile linking somewhere, with an optional play button that carries `play` as data. */
export function tile({ href = '#', thumb = '', thumbs = null, liked = false, title, subtitle = '', round = false, play = '' }) {
  const art = liked ? likedCover('card-img') : thumbs ? mosaic(thumbs, 'card-img') : cover(thumb, 'card-img');
  return `
    <a class="tile ${round ? 'round' : ''}" href="${esc(href)}">
      <div class="art">${art}${play ? `<button class="fab" data-play="${esc(play)}" title="נגן">${icon('play')}</button>` : ''}</div>
      <div class="t1 ellipsis">${esc(title)}</div>
      ${subtitle ? `<div class="t2">${esc(subtitle)}</div>` : ''}
    </a>`;
}

export function shelf(title, tilesHtml, { more = '' } = {}) {
  if (!tilesHtml) return '';
  return `
    <section class="shelf">
      <div class="shelf-head"><h2>${esc(title)}</h2>${more ? `<a class="muted small link" href="${esc(more)}">הצג הכל</a>` : ''}</div>
      <div class="shelf-row">${tilesHtml}</div>
    </section>`;
}

export function skeletonShelves(count = 2) {
  const cards = Array.from({ length: 6 }, () => '<div><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-line"></div></div>').join('');
  return Array.from({ length: count }, () => `
    <section class="shelf"><div class="skeleton skeleton-line" style="width:180px;height:22px;margin:0 0 12px"></div>
    <div class="shelf-row">${cards}</div></section>`).join('');
}

export function hero({ kind, title, meta = '', image = '', thumbs = null, liked = false, round = false }) {
  const art = liked ? likedCover('cover-lg') : thumbs ? mosaic(thumbs, 'cover-lg') : cover(image, 'cover-lg');
  return `
    <header class="hero ${round ? 'round' : ''}">
      ${art}
      <div class="hero-text">
        <div class="kind">${esc(kind)}</div>
        <h1 data-hero-title>${esc(title)}</h1>
        <div class="meta" data-hero-meta>${meta}</div>
      </div>
    </header>`;
}

/** Big play button that turns into pause while this screen's songs are the ones playing. */
export function bindPlayButton(button, ctx, key, onPlay) {
  const update = () => {
    button.innerHTML = icon(player.isContext(key) && player.playing ? 'pause' : 'play');
  };
  button.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (player.isContext(key)) player.toggle();
    else onPlay();
  });
  update();
  ctx.onCleanup(player.onChange(update));
}
