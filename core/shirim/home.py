"""The personal home screen: new releases, recent listening, your artists, mixes and releases this year."""

import collections
import datetime
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import names, search

log = logging.getLogger(__name__)

_ARTIST_TTL = 12 * 3600
_MIX_TTL = 3600
_ARTISTS_SHOWN = 12
_CHART_TTL = 6 * 3600
_CHART_RETRY = 10 * 60
_CHART_SONGS = 50
CHART_COUNTRY = 'IL'


def _canonical_name(db, artist, known):
    lead = names.lead_artist(artist, known)
    alias = db.get_alias(lead) if lead else None
    return alias['canonical'] if alias else lead


def favorite_artists(db, limit=_ARTISTS_SHOWN):
    """Artists ranked by following, listening, likes and saved songs, with YouTube Music ids and pictures."""
    known = db.known_artists()
    scores = collections.Counter()
    for row in db.play_counts_by_artist():
        scores[_canonical_name(db, row['artist'], known)] += 3 * row['n']
    for like in db.list_likes():
        scores[_canonical_name(db, like['artist'], known)] += 2
    for row in db.library_counts_by_artist():
        scores[row['lead_artist']] += row['n']
    followed = {}
    for follow in db.list_follows():
        # Key followed artists by the library spelling so they merge with listening and download counts
        aliases = db.aliases_for_browse_id(follow['browseId'])
        followed[aliases[0]['canonical'] if aliases else follow['name']] = follow
    for name in followed:
        scores[name] += 10_000
    scores.pop('', None)

    def card(name):
        if name in followed:
            browse_id = followed[name]['browseId']
        else:
            browse_id = db.browse_id_for(name)
            if not browse_id:
                try:
                    browse_id = search.artist_id(name)
                except Exception:
                    return None
                if not browse_id:
                    return None
                db.set_alias(name, browse_id, name)
        info = db.cache_get(f'artist:{browse_id}')
        if info is None:
            try:
                data = search.artist(browse_id, full_releases=False)
            except Exception as e:
                log.info('Artist %s unavailable: %s', browse_id, e)
                return None
            info = {'thumb': data['thumb'], 'releases': data['singles'][:4] + data['albums'][:4]}
            db.cache_set(f'artist:{browse_id}', info, _ARTIST_TTL)
        # Our spelling (usually Hebrew) rather than YouTube Music's English artist name
        return {'browseId': browse_id, 'name': name, 'thumb': info['thumb'], 'releases': info['releases']}

    ranked = [name for name, _ in scores.most_common(limit * 2)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = [c for c in pool.map(card, ranked) if c]
    return cards[:limit]


def _releases_this_year(artists):
    this_year = datetime.date.today().year
    recent_years = {str(this_year), str(this_year - 1)}
    releases = []
    for artist in artists:
        fresh = [r for r in artist['releases'] if str(r.get('year')) in recent_years][:2]
        releases.extend({**r, 'artist': artist['name']} for r in fresh)
    releases.sort(key=lambda r: str(r.get('year')), reverse=True)
    return releases[:12]


def charts(db):
    """This week's most played songs and artists in Israel on YouTube Music."""
    key = f'charts:{CHART_COUNTRY}'
    cached = db.cache_get(key)
    if cached is not None:
        return cached
    result = {'playlistId': '', 'tracks': [], 'artists': []}
    try:
        data = search._client().get_charts(country=CHART_COUNTRY)
        playlists = data.get('videos') or []
        top = (next((p for p in playlists if 'Top 100' in (p.get('title') or '')), None)
               or next((p for p in playlists if p.get('playlistId')), None))
        if top:
            result['playlistId'] = top['playlistId']
            result['tracks'] = search.playlist(top['playlistId'], limit=_CHART_SONGS)['tracks'][:_CHART_SONGS]
        result['artists'] = [card for card in (search._artist_card(a, 300) for a in (data.get('artists') or [])[:15])
                             if card['browseId']]
    except Exception as e:
        log.info('Charts unavailable: %s', e)
        db.cache_set(key, result, _CHART_RETRY)
        return result
    db.cache_set(key, result, _CHART_TTL)
    return result


def recap_teaser(db):
    """The latest month worth a recap, to link from the home screen."""
    from . import recap  # recap imports search; imported here to keep module loading light

    months = recap.months(db)
    return months[0] if months else None


def build(db):
    sections = []
    out_now = db.list_new_releases(12)
    if out_now:
        sections.append({'id': 'new', 'title': 'יצא עכשיו מזמרים שבמעקב', 'kind': 'albums', 'items': out_now,
                         'more': '#/new'})

    recent = db.recent_plays(16)
    if recent:
        sections.append({'id': 'recent', 'title': 'ניגנת לאחרונה', 'kind': 'tracks', 'items': recent})

    chart = charts(db)
    if chart['tracks']:
        sections.append({'id': 'chart', 'title': 'המצעד בישראל', 'kind': 'chart', 'items': chart['tracks'][:20],
                         'more': f"#/ytplaylist/{chart['playlistId']}?title={urllib.parse.quote('המצעד בישראל')}"})

    artists = favorite_artists(db)
    if artists:
        sections.append({'id': 'artists', 'title': 'הזמרים שלך', 'kind': 'artists',
                         'items': [{k: a[k] for k in ('browseId', 'name', 'thumb')} for a in artists]})
        mixes = [{'id': f"artist:{a['browseId']}", 'title': f"המיקס של {a['name']}",
                  'subtitle': f"{a['name']} ועוד זמרים דומים", 'thumb': a['thumb']} for a in artists[:8]]
        # Radio needs a YouTube song to start from, so files found on the device can't seed one
        youtube_recent = [t for t in recent if not t['videoId'].startswith('local-')]
        mixes += [{'id': f"song:{t['videoId']}", 'title': f"רדיו: {t['title']}", 'subtitle': 'שירים דומים',
                   'thumb': t['thumb']} for t in youtube_recent[:3]]
        sections.append({'id': 'mixes', 'title': 'מיקסים בשבילך', 'kind': 'mixes', 'items': mixes})
        releases = _releases_this_year(artists)
        if releases:
            sections.append({'id': 'releases', 'title': 'חדש מהזמרים שלך', 'kind': 'albums', 'items': releases})
    if chart['artists']:
        sections.append({'id': 'chart-artists', 'title': 'הזמרים הכי מושמעים בישראל', 'kind': 'artists',
                         'items': chart['artists']})
    return {'sections': sections, 'recap': recap_teaser(db), 'newUser': not recent and not artists}


def mix(db, mix_id):
    """Tracks of a home-screen mix: 'artist:<browseId>' or 'song:<videoId>'."""
    cached = db.cache_get(f'mix:{mix_id}')
    if cached is not None:
        return cached
    kind, _, value = mix_id.partition(':')
    if kind == 'artist':
        tracks = search.artist_radio(value)
    elif kind == 'song':
        tracks = search.radio(value)
    else:
        raise ValueError(f'Unknown mix: {mix_id}')
    db.cache_set(f'mix:{mix_id}', tracks, _MIX_TTL)
    return tracks
