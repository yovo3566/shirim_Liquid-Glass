"""Monthly listening recap: minutes, top artists and songs, and when you listen most."""

import collections
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor

from . import names, search

log = logging.getLogger(__name__)

_MONTHS = ('ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר')
_DAYS = ('שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון')  # datetime.weekday(): Monday is 0
_ARTIST_TTL = 12 * 3600
MIN_PLAYS = 5


def _month_range(month):
    year, number = (int(part) for part in month.split('-'))
    start = datetime.datetime(year, number, 1)
    end = datetime.datetime(year + (number == 12), number % 12 + 1, 1)
    return start.timestamp(), end.timestamp()


def label(month):
    year, number = month.split('-')
    return f'{_MONTHS[int(number) - 1]} {year}'


def months(db):
    """Months with enough listening for a recap, newest first."""
    counts = collections.Counter(
        datetime.datetime.fromtimestamp(play['played_at']).strftime('%Y-%m')
        for play in db.plays_between(0, datetime.datetime.now().timestamp() + 86400))
    return [{'month': m, 'label': label(m), 'plays': n} for m, n in sorted(counts.items(), reverse=True) if n >= MIN_PLAYS]


def _part_of_day(hour):
    if 5 <= hour < 12:
        return 'בבוקר'
    if 12 <= hour < 17:
        return 'בצהריים'
    if 17 <= hour < 22:
        return 'בערב'
    return 'בלילה'


def _artist_picture(db, name):
    browse_id = db.browse_id_for(name)
    if not browse_id:
        return '', ''
    info = db.cache_get(f'artist:{browse_id}')
    if info is None:
        try:
            data = search.artist(browse_id, full_releases=False)
        except Exception as e:
            log.info('Artist %s unavailable: %s', browse_id, e)
            return browse_id, ''
        info = {'thumb': data['thumb'], 'releases': data['singles'][:4] + data['albums'][:4]}
        db.cache_set(f'artist:{browse_id}', info, _ARTIST_TTL)
    return browse_id, info['thumb']


def _canonical_names(raw_names, artists, db):
    """Map each lead artist to one spelling ("Omer Adam" and "עומר אדם" become the same artist).

    Spellings are linked through the YouTube Music artist they resolve to; the Hebrew spelling is shown.
    """
    hebrew = sorted(n for n in raw_names if names.is_hebrew(n))
    others = sorted(n for n in raw_names if not names.is_hebrew(n))
    mapping = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for group in (hebrew, others):
            mapping.update(zip(group, pool.map(lambda n: artists.canonical(n) or n, group)))
    spellings = collections.defaultdict(set)
    for raw, name in mapping.items():
        alias = db.get_alias(raw) or db.get_alias(name)
        if alias and alias['browse_id']:
            spellings[alias['browse_id']].add(name)
    for group in spellings.values():
        preferred = min(group, key=lambda n: (not names.is_hebrew(n), n))
        for raw, name in mapping.items():
            if name in group:
                mapping[raw] = preferred
    return mapping


def build(db, month, artists=None):
    start, end = _month_range(month)
    plays = db.plays_between(start, end)
    known = db.known_artists()
    leads = {play['artist']: names.lead_artist(play['artist'], known) for play in plays}
    if artists is not None:
        canonical = _canonical_names({lead for lead in leads.values() if lead}, artists, db)
    else:
        canonical = {}
        for name in set(leads.values()):
            alias = db.get_alias(name) if name else None
            canonical[name] = alias['canonical'] if alias else name

    def lead(artist):
        name = leads.get(artist)
        if name is None:
            name = names.lead_artist(artist, known)
        return canonical.get(name, name)

    seconds = 0
    artist_plays = collections.Counter()
    artist_seconds = collections.Counter()
    song_plays = collections.Counter()
    songs = {}
    days = collections.Counter()
    parts = collections.Counter()
    listening_days = set()
    for play in plays:
        duration = play['duration'] or 180
        seconds += duration
        name = lead(play['artist'])
        if name:
            artist_plays[name] += 1
            artist_seconds[name] += duration
        song_plays[play['videoId']] += 1
        songs[play['videoId']] = play
        moment = datetime.datetime.fromtimestamp(play['played_at'])
        days[moment.weekday()] += duration
        parts[_part_of_day(moment.hour)] += duration
        listening_days.add(moment.date())

    top_names = [name for name, _ in artist_plays.most_common(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        pictures = list(pool.map(lambda n: _artist_picture(db, n), top_names))
    top_artists = [{'name': name, 'plays': artist_plays[name], 'minutes': round(artist_seconds[name] / 60),
                    'browseId': picture[0], 'thumb': picture[1]} for name, picture in zip(top_names, pictures)]
    top_songs = [{**{k: songs[vid][k] for k in ('videoId', 'title', 'artist', 'album', 'duration', 'thumb')},
                  'plays': count} for vid, count in song_plays.most_common(10)]

    # Artists heard for the first time this month (every one of them was played this month, so already mapped)
    first_heard = db.first_play_times_by_artist()
    new_artists = sorted({lead(artist) for artist, first in first_heard.items() if start <= first < end} - {''})
    heard_before = {lead(artist) for artist, first in first_heard.items() if first < start and artist in leads}
    new_artists = [name for name in new_artists if name not in heard_before]

    previous_month = (datetime.datetime.fromtimestamp(start) - datetime.timedelta(days=1)).strftime('%Y-%m')
    previous_seconds = sum((p['duration'] or 180) for p in db.plays_between(*_month_range(previous_month)))

    return {
        'month': month,
        'label': label(month),
        'minutes': round(seconds / 60),
        'plays': len(plays),
        'songs': len(song_plays),
        'artists': len(artist_plays),
        'days': len(listening_days),
        'topArtists': top_artists,
        'topSongs': top_songs,
        'topDay': _DAYS[days.most_common(1)[0][0]] if days else '',
        'topPart': parts.most_common(1)[0][0] if parts else '',
        'newArtists': new_artists[:8],
        'newArtistCount': len(new_artists),
        'previousMinutes': round(previous_seconds / 60),
    }
