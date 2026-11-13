"""Song lyrics: time-synced when YouTube Music has them, otherwise plain text, or the words embedded in a file."""

import logging

import mutagen

from . import names, search

log = logging.getLogger(__name__)

_FOUND_TTL = 30 * 86400
_MISSING_TTL = 3 * 86400
_NONE = {'synced': False, 'lines': [], 'source': ''}


def _from_youtube(video_id):
    client = search._client()
    browse_id = client.get_watch_playlist(videoId=video_id, limit=1).get('lyrics')
    if not browse_id:
        return None
    data = client.get_lyrics(browse_id, timestamps=True) or {}
    raw = data.get('lyrics')
    source = str(data.get('source') or '').replace('Source:', '').strip()
    if data.get('hasTimestamps') and isinstance(raw, list):
        lines = [{'time': round(line.start_time / 1000, 2), 'text': line.text if line.text != '♪' else ''}
                 for line in raw]
        return {'synced': True, 'lines': lines, 'source': source}
    if raw is None:
        # Some songs only have plain lyrics; ask again without timestamps
        data = client.get_lyrics(browse_id) or {}
        raw = data.get('lyrics')
    if isinstance(raw, str) and raw.strip():
        return {'synced': False, 'lines': [{'text': line} for line in raw.splitlines()], 'source': source}
    return None


def _from_file(path):
    """Unsynced lyrics tagged into a music file (ID3 USLT, MP4 ©lyr, Vorbis LYRICS)."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None or not audio.tags:
        return None
    text = ''
    tags = audio.tags
    for key in list(tags.keys()):
        name = str(key)
        if name.startswith('USLT'):
            text = str(tags[key])
        elif name in ('©lyr', 'LYRICS', 'lyrics', 'UNSYNCEDLYRICS', 'unsyncedlyrics'):
            value = tags[key]
            text = str(value[0] if isinstance(value, list) and value else value)
        if text.strip():
            return {'synced': False, 'lines': [{'text': line} for line in text.splitlines()], 'source': ''}
    return None


def _find_on_youtube(title, artist, duration):
    """The YouTube Music song matching a file's title and artist, for files that aren't from YouTube."""
    if not title:
        return None
    lead = names.lead_artist(artist)
    for result in search.search(f'{title} {lead}'.strip(), 'songs', limit=5):
        if names.title_key(result['title']) != names.title_key(title):
            continue
        if duration and result['duration'] and not names.same_recording(result['duration'], duration):
            continue
        return result['videoId']
    return None


def get(db, video_id, title='', artist='', duration=0, path=None):
    key = f'lyrics:{video_id}'
    cached = db.cache_get(key)
    if cached is not None:
        return cached
    result = None
    try:
        if path:
            result = _from_file(path)
        youtube_id = video_id if not video_id.startswith('local-') else None
        if result is None and youtube_id is None:
            youtube_id = _find_on_youtube(title, artist, duration)
        if result is None and youtube_id:
            result = _from_youtube(youtube_id)
    except Exception as e:
        # Offline or YouTube Music refused; don't remember the miss for long
        log.info('Lyrics for %s unavailable: %s', video_id, e)
        return _NONE
    db.cache_set(key, result or _NONE, _FOUND_TTL if result else _MISSING_TTL)
    return result or _NONE
