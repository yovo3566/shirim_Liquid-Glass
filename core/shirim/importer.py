"""Import playlists from Spotify links, YouTube links or a pasted list of songs into a new playlist."""

import html
import json
import logging
import re
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import downloader, names, search, tagging

log = logging.getLogger(__name__)

_SPOTIFY = re.compile(r'(?:open\.spotify\.com/(?:intl-[\w-]+/)?|spotify:)(playlist|album|track)[/:]([A-Za-z0-9]{10,40})')
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', re.S)
_MAX_LINES = 1000
_FINISHED_KEEP = 3600


class ImportError_(Exception):
    pass


def _fetch(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/130.0 Safari/537.36',
        'Accept-Language': 'he,en;q=0.8',
    })
    with urllib.request.urlopen(request, timeout=25, context=tagging._SSL) as response:
        return response.read().decode('utf-8', 'replace')


def spotify(kind, spotify_id):
    """Name, cover and songs of a public Spotify playlist, album or track (Spotify's embed shows up to 100)."""
    page = _fetch(f'https://open.spotify.com/embed/{kind}/{spotify_id}')
    match = _NEXT_DATA.search(page)
    if not match:
        raise ImportError_('Spotify page could not be read')
    try:
        entity = json.loads(match.group(1))['props']['pageProps']['state']['data']['entity']
    except (KeyError, TypeError, ValueError):
        raise ImportError_('Spotify playlist not found or private') from None
    cover = ''
    sources = ((entity.get('coverArt') or {}).get('sources') or [])
    if sources:
        cover = sources[0].get('url') or ''
    if kind == 'track':
        songs = [{'title': entity.get('title') or entity.get('name') or '',
                  'artist': ', '.join(a.get('name', '') for a in entity.get('artists') or []) or entity.get('subtitle') or '',
                  'duration': round((entity.get('duration') or 0) / 1000)}]
    else:
        songs = [{'title': t.get('title') or '', 'artist': t.get('subtitle') or '',
                  'duration': round((t.get('duration') or 0) / 1000)} for t in entity.get('trackList') or []]
    return {'name': html.unescape(entity.get('name') or entity.get('title') or 'מספוטיפיי'), 'cover': cover,
            'songs': [s for s in songs if s['title']], 'limited': kind == 'playlist' and len(songs) >= 100}


def parse_text(text):
    """Songs from pasted lines like "Artist - Title", "Title by Artist" or just a title."""
    songs = []
    for line in text.splitlines()[:_MAX_LINES]:
        line = re.sub(r'^\s*(\d+[.)]\s*|[-*•]\s*)', '', line).strip()
        if not line or line.startswith(('http://', 'https://')):
            continue
        if ' - ' in line:
            artist, title = line.split(' - ', 1)
        elif ' – ' in line:
            artist, title = line.split(' – ', 1)
        elif ' by ' in line.lower():
            index = line.lower().rindex(' by ')
            title, artist = line[:index], line[index + 4:]
        else:
            artist, title = '', line
        songs.append({'title': title.strip(), 'artist': artist.strip(), 'duration': 0})
    return songs


def match(song):
    """The YouTube Music song that best matches a song from another service, or None."""
    lead = names.lead_artist(song['artist'])
    query = f"{song['title']} {lead}".strip()
    try:
        results = search.search(query, 'songs', limit=8)
    except Exception as e:
        log.info('Search for %r failed: %s', query, e)
        return None
    wanted_title = names.title_key(song['title'])
    wanted_artist = names.title_key(lead)
    best, best_score = None, 0
    for rank, result in enumerate(results):
        score = 10 - rank
        title = names.title_key(result['title'])
        if title == wanted_title:
            score += 40
        elif wanted_title and (wanted_title in title or title in wanted_title):
            score += 20
        artist_key = names.title_key(result['artist'])
        if wanted_artist and wanted_artist in artist_key:
            score += 25
        if song['duration'] and result['duration']:
            difference = abs(song['duration'] - result['duration'])
            score += 20 if difference <= 3 else 8 if difference <= 10 else -15
        if score > best_score:
            best, best_score = result, score
    # A song with neither a matching title nor artist is more likely wrong than right
    return best if best_score >= 35 else None


class Importer:
    def __init__(self, db, events):
        self.db = db
        self.events = events
        self._jobs = {}
        self._lock = threading.Lock()

    def status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def start(self, source, name=''):
        source = (source or '').strip()
        if not source:
            raise ValueError('Nothing to import')
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            now = time.time()
            for stale in [k for k, j in self._jobs.items() if j['finishedAt'] and now - j['finishedAt'] > _FINISHED_KEEP]:
                del self._jobs[stale]
            self._jobs[job_id] = {'id': job_id, 'state': 'reading', 'name': name, 'total': 0, 'done': 0, 'matched': 0,
                                  'missing': [], 'playlistId': None, 'limited': False, 'error': '', 'finishedAt': 0}
        threading.Thread(target=self._run, args=(job_id, source, name), name='import', daemon=True).start()
        return job_id

    def _set(self, job_id, **fields):
        with self._lock:
            self._jobs[job_id].update(fields)
        self.events.bump()

    def _run(self, job_id, source, name):
        try:
            spotify_link = _SPOTIFY.search(source)
            if spotify_link:
                data = spotify(*spotify_link.groups())
                playlist_name, songs, limited = name or data['name'], data['songs'], data['limited']
                tracks = self._match_all(job_id, songs)
            elif re.search(r'(youtube\.com|youtu\.be)/', source):
                self._set(job_id, state='matching')
                tracks = downloader.resolve_url(source.split()[0])
                playlist_name, limited = name or 'מיוטיוב', False
                self._set(job_id, total=len(tracks), done=len(tracks), matched=len(tracks))
            else:
                songs = parse_text(source)
                if not songs:
                    raise ImportError_('No songs found in the text')
                playlist_name, limited = name or 'פלייליסט מיובא', False
                tracks = self._match_all(job_id, songs)
            if not tracks:
                raise ImportError_('None of the songs were found')
            playlist_id = self.db.create_playlist(playlist_name[:100])
            self.db.add_to_playlist(playlist_id, tracks)
            self._set(job_id, state='done', name=playlist_name, playlistId=playlist_id, limited=limited,
                      finishedAt=time.time())
        except Exception as e:
            log.warning('Import failed: %s', e)
            self._set(job_id, state='error', error=str(e) or 'Import failed', finishedAt=time.time())

    def _match_all(self, job_id, songs):
        self._set(job_id, state='matching', total=len(songs))
        results = [None] * len(songs)
        missing = []

        def work(index):
            results[index] = match(songs[index])
            with self._lock:
                job = self._jobs[job_id]
                job['done'] += 1
                if results[index]:
                    job['matched'] += 1
                else:
                    missing.append(f"{songs[index]['artist']} - {songs[index]['title']}".strip(' -'))
                    job['missing'] = missing[:200]
            self.events.bump()

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(work, range(len(songs))))
        seen, tracks = set(), []
        for track in results:
            if track and track['videoId'] not in seen:
                seen.add(track['videoId'])
                tracks.append(track)
        return tracks
