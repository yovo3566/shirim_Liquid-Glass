"""YouTube Music catalogue lookups: search, artists, albums, playlists and radio."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor

from ytmusicapi import YTMusic

from . import names

_local = threading.local()


def _client():
    # YTMusic keeps a requests session, so give every thread its own instance
    if not hasattr(_local, 'client'):
        _local.client = YTMusic()
    return _local.client


def _thumb(thumbnails, size=544):
    if not thumbnails:
        return ''
    url = max(thumbnails, key=lambda t: t.get('width') or 0).get('url') or ''
    # Google image URLs accept a size suffix, so ask for a larger square cover
    return re.sub(r'=w\d+-h\d+', f'=w{size}-h{size}', url)


def _parse_duration(text):
    if not text:
        return 0
    seconds = 0
    for part in str(text).split(':'):
        if not part.isdigit():
            return 0
        seconds = seconds * 60 + int(part)
    return seconds


def _artists(item):
    return ', '.join(a['name'] for a in item.get('artists') or [] if a.get('name'))


def _album_name(item):
    album = item.get('album')
    if isinstance(album, dict):
        return album.get('name') or ''
    return album or ''


def _track(item, album=None, thumb=None):
    video_id = item.get('videoId')
    track = {
        'videoId': video_id,
        'title': item.get('title') or '',
        'artist': _artists(item),
        'album': _album_name(item) if album is None else album,
        'duration': item.get('duration_seconds') or _parse_duration(item.get('duration') or item.get('length')),
        'thumb': (thumb or _thumb(item.get('thumbnails') or item.get('thumbnail'))
                  or f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'),
    }
    if item.get('isExplicit'):
        track['explicit'] = True  # hidden in filtered mode
    return track


def _artist_card(item, size=300):
    browse_id = item.get('browseId') or next((a['id'] for a in item.get('artists') or [] if a.get('id')), None)
    return {
        'browseId': browse_id,
        'name': item.get('artist') or item.get('title') or _artists(item),
        'thumb': _thumb(item.get('thumbnails'), size),
    }


def _album_card(item):
    return {
        'browseId': item['browseId'],
        'title': item.get('title') or '',
        'artist': _artists(item),
        'year': item.get('year') or '',
        'type': item.get('type') or '',
        'thumb': _thumb(item.get('thumbnails'), 300),
    }


def _playlist_card(item):
    author = item.get('author')
    if isinstance(author, list):
        author = ', '.join(a.get('name', '') for a in author)
    return {
        'browseId': item['browseId'],
        'title': item.get('title') or '',
        'author': author or '',
        'count': item.get('itemCount') or '',
        'thumb': _thumb(item.get('thumbnails'), 300),
    }


def search(query, kind='songs', limit=40):
    results = _client().search(query, filter=kind, limit=limit)
    if kind in ('songs', 'videos'):
        return [_track(r) for r in results if r.get('videoId')]
    if kind == 'artists':
        return [c for c in map(_artist_card, results) if c['browseId']]
    if kind == 'albums':
        return [_album_card(r) for r in results if r.get('browseId')]
    if kind == 'playlists':
        return [_playlist_card(r) for r in results if r.get('browseId')]
    raise ValueError(f'Unknown search kind: {kind}')


def search_all(query):
    """One search, grouped the way a results page shows it: top result, songs, artists, albums, playlists."""
    grouped = {'top': None, 'songs': [], 'artists': [], 'albums': [], 'videos': [], 'playlists': []}
    for item in _client().search(query, limit=20):
        kind = item.get('resultType')
        if kind in ('song', 'video') and item.get('videoId'):
            card, bucket = {'type': 'song', **_track(item)}, 'songs' if kind == 'song' else 'videos'
        elif kind == 'artist':
            card, bucket = {'type': 'artist', **_artist_card(item)}, 'artists'
            if not card['browseId']:
                continue
        elif kind == 'album' and item.get('browseId'):
            card, bucket = {'type': 'album', **_album_card(item)}, 'albums'
        elif kind == 'playlist' and item.get('browseId'):
            card, bucket = {'type': 'playlist', **_playlist_card(item)}, 'playlists'
        else:
            continue
        if item.get('category') == 'Top result' and grouped['top'] is None:
            grouped['top'] = card
        else:
            grouped[bucket].append(card)
    return grouped


def suggestions(query):
    return list(_client().get_search_suggestions(query))[:8]


def artist_id(name):
    """The YouTube Music artist a name most likely refers to, or None."""
    for result in _client().search(name, filter='artists', limit=5):
        if result.get('browseId'):
            return result['browseId']
    return None


def artist(browse_id, full_releases=True):
    client = _client()
    data = client.get_artist(browse_id)

    def releases(section_name):
        section = data.get(section_name) or {}
        items = section.get('results') or []
        # The artist page only previews releases; params unlock the full list
        if full_releases and section.get('params') and section.get('browseId'):
            try:
                items = client.get_artist_albums(section['browseId'], section['params'], limit=None)
            except Exception:
                pass
        return [{
            'browseId': a['browseId'],
            'title': a.get('title') or '',
            'year': a.get('year') or '',
            'thumb': _thumb(a.get('thumbnails'), 300),
        } for a in items if a.get('browseId')]

    songs = data.get('songs') or {}
    related = (data.get('related') or {}).get('results') or []
    return {
        'browseId': browse_id,
        'name': data.get('name') or '',
        'thumb': _thumb(data.get('thumbnails'), 600),
        'subscribers': data.get('subscribers') or '',
        'songsId': songs.get('browseId') or '',
        'radioId': data.get('radioId') or '',
        'shuffleId': data.get('shuffleId') or '',
        'preview': [_track(t) for t in songs.get('results') or [] if t.get('videoId')],
        'albums': releases('albums'),
        'singles': releases('singles'),
        'related': [c for c in map(_artist_card, related) if c['browseId']],
    }


def album(browse_id):
    data = _client().get_album(browse_id)
    cover = _thumb(data.get('thumbnails'))
    title = data.get('title') or ''
    album_artist = _artists(data)
    tracks = []
    for item in data.get('tracks') or []:
        if not item.get('videoId'):
            continue
        track = _track(item, album=title, thumb=cover)
        track['artist'] = track['artist'] or album_artist
        tracks.append(track)
    return {
        'browseId': browse_id,
        'title': title,
        'artist': album_artist,
        'year': data.get('year') or '',
        'thumb': cover,
        'tracks': tracks,
    }


def playlist(browse_id, limit=None):
    """A YouTube Music playlist (community, charts, mixes)."""
    data = _client().get_playlist(browse_id, limit=limit)
    author = data.get('author')
    return {
        'browseId': browse_id,
        'title': data.get('title') or '',
        'author': (author.get('name') if isinstance(author, dict) else author) or '',
        'thumb': _thumb(data.get('thumbnails')),
        'tracks': [_track(t) for t in data.get('tracks') or [] if t.get('videoId') and t.get('isAvailable', True)],
    }


def artist_songs(browse_id, include_releases=False):
    """All songs of an artist: the artist's songs list, optionally merged with every album and single.

    The same recording often appears several times (single, album track, music video); only the first is kept.
    """
    info = artist(browse_id)
    tracks, seen_ids, seen_songs = [], set(), {}

    def add(items):
        for track in items:
            if track['videoId'] in seen_ids:
                continue
            durations = seen_songs.setdefault(names.title_key(track['title']), [])
            if any(names.same_recording(d, track['duration']) for d in durations):
                continue
            seen_ids.add(track['videoId'])
            durations.append(track['duration'])
            tracks.append(track)

    if info['songsId']:
        playlist_data = _client().get_playlist(info['songsId'], limit=None)
        add(_track(t) for t in playlist_data.get('tracks') or []
            if t.get('videoId') and t.get('isAvailable', True))

    if include_releases or not tracks:
        release_ids = [r['browseId'] for r in info['albums'] + info['singles']]
        with ThreadPoolExecutor(max_workers=6) as pool:
            for result in pool.map(_safe_album, release_ids):
                if result:
                    add(result['tracks'])

    return {'artist': info['name'], 'thumb': info['thumb'], 'tracks': tracks}


def radio(video_id, limit=50):
    """Songs similar to one song, like YouTube Music's radio."""
    data = _client().get_watch_playlist(videoId=video_id, radio=True, limit=limit)
    return [_track(t) for t in data.get('tracks') or [] if t.get('videoId') and t['videoId'] != video_id]


def artist_radio(browse_id, limit=50):
    """An artist's songs mixed with similar artists."""
    data = _client().get_artist(browse_id)
    playlist_id = data.get('radioId') or data.get('shuffleId')
    if not playlist_id:
        return []
    watch = _client().get_watch_playlist(playlistId=playlist_id, limit=limit)
    return [_track(t) for t in watch.get('tracks') or [] if t.get('videoId')]


def _safe_album(browse_id):
    try:
        return album(browse_id)
    except Exception:
        return None
