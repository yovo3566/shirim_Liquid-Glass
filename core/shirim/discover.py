"""Discovery: artists similar to the ones you listen to, their songs, and songs of your artists you don't have."""

import collections
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from . import home, names, search

log = logging.getLogger(__name__)

_TTL = 6 * 3600
_ARTIST_TTL = 12 * 3600
_SEED_ARTISTS = 10
_NEW_ARTISTS = 12
_SONGS_PER_NEW_ARTIST = 3


def _artist_page(db, browse_id):
    cached = db.cache_get(f'artist-page:{browse_id}')
    if cached is None:
        data = search.artist(browse_id, full_releases=False)
        cached = {k: data[k] for k in ('browseId', 'name', 'thumb', 'preview', 'related')}
        db.cache_set(f'artist-page:{browse_id}', cached, _ARTIST_TTL)
    return cached


def _safe_page(db, browse_id):
    try:
        return _artist_page(db, browse_id)
    except Exception as e:
        log.info('Artist %s unavailable: %s', browse_id, e)
        return None


def _in_library(db, track, library_ids):
    if track['videoId'] in library_ids:
        return True
    lead = names.lead_artist(track['artist'])
    alias = db.get_alias(lead)
    canonical = alias['canonical'] if alias else lead
    return any(names.same_recording(t['duration'], track['duration'])
               for t in db.find_same_song(canonical, names.title_key(track['title'])))


def build(db, refresh=False):
    cached = None if refresh else db.cache_get('discover')
    if cached is not None:
        return cached

    seeds = home.favorite_artists(db)[:_SEED_ARTISTS]
    yours = {a['browseId'] for a in seeds} | {f['browseId'] for f in db.list_follows()}
    library_ids = db.track_ids()

    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(lambda a: (a, _safe_page(db, a['browseId'])), seeds))

    related = collections.Counter()
    cards, reasons = {}, {}
    more_from_yours = []
    for seed, page in pages:
        if not page:
            continue
        for artist in page['related']:
            if artist['browseId'] in yours:
                continue
            related[artist['browseId']] += 1
            cards.setdefault(artist['browseId'], artist)
            reasons.setdefault(artist['browseId'], seed['name'])
        more_from_yours.extend(t for t in page['preview'] if not _in_library(db, t, library_ids))

    new_artists = [{**cards[i], 'reason': f'דומה ל{reasons[i]}'} for i, _ in related.most_common(_NEW_ARTISTS)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        new_pages = list(pool.map(lambda a: _safe_page(db, a['browseId']), new_artists))

    songs, seen = [], set()
    for page in new_pages:
        if not page:
            continue
        for track in page['preview'][:_SONGS_PER_NEW_ARTIST]:
            if track['videoId'] not in seen and not _in_library(db, track, library_ids):
                seen.add(track['videoId'])
                songs.append(track)

    unique_more = list({t['videoId']: t for t in more_from_yours}.values())
    result = {
        'artists': new_artists,
        'songs': songs,
        'moreFromYourArtists': unique_more[:30],
        'basedOn': [a['name'] for a in seeds[:3]],
        'generatedAt': time.time(),
    }
    db.cache_set('discover', result, _TTL)
    return result
