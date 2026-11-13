"""Finds music on the device that isn't in the library yet (including files in the app's own folder) and adds it."""

import base64
import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Cover

from . import names

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.aac', '.flac', '.ogg', '.oga', '.opus', '.wav', '.webm'}
LOCAL_PREFIX = 'local-'
_MIN_BYTES = 150 * 1024
_MIN_SECONDS = 30  # ringtones, notification sounds and voice clips aren't music
_MAX_FILES = 50_000
_SKIP_DIRS = {'appdata', 'node_modules', '$recycle.bin', 'system volume information', 'windows', 'program files',
              'program files (x86)', 'programdata', 'shirim-duplicates', 'android', '.thumbnails'}
# Cloud placeholders (OneDrive "files on demand"): reading them would download the file
_CLOUD_ATTRIBUTES = 0x400000 | 0x1000


def local_id(path):
    return LOCAL_PREFIX + hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode('utf-8')).hexdigest()[:16]


def _first(values):
    return str(values[0]).strip() if values else ''


def _inside(path, folder):
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def read_tags(path):
    title = artist = album = ''
    duration = 0
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    if audio is not None:
        tags = audio.tags or {}
        try:
            title = _first(tags.get('title'))
            artist = ', '.join(str(v).strip() for v in tags.get('artist') or [] if str(v).strip())
            album = _first(tags.get('album'))
        except (AttributeError, TypeError, ValueError):
            pass
        duration = int(getattr(audio.info, 'length', 0) or 0)
    if not title:
        # Untagged files are usually named "Artist - Title"
        stem = os.path.splitext(os.path.basename(path))[0]
        guessed_artist, separator, guessed_title = stem.partition(' - ')
        if separator and not artist:
            artist, title = guessed_artist.strip(), guessed_title.strip()
        else:
            title = stem
    return {'title': title[:300], 'artist': artist[:300], 'album': album[:300], 'duration': duration}


def extract_cover(path):
    """Embedded cover art as (bytes, mime), or None."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    tags = audio.tags
    try:
        if isinstance(tags, ID3):
            frames = tags.getall('APIC')
            return (frames[0].data, frames[0].mime or 'image/jpeg') if frames else None
        if tags is not None and 'covr' in tags:
            art = tags['covr'][0]
            png = getattr(art, 'imageformat', None) == MP4Cover.FORMAT_PNG
            return bytes(art), 'image/png' if png else 'image/jpeg'
        pictures = getattr(audio, 'pictures', None)
        if pictures:
            return pictures[0].data, pictures[0].mime or 'image/jpeg'
        if tags is not None and 'metadata_block_picture' in tags:
            picture = Picture(base64.b64decode(tags['metadata_block_picture'][0]))
            return picture.data, picture.mime or 'image/jpeg'
    except Exception:
        return None
    return None


class LibraryScanner:
    def __init__(self, db, settings, host, artists, events):
        self.db = db
        self.settings = settings
        self.host = host
        self.artists = artists
        self.events = events
        self.running = False
        self.last = {'finished_at': 0, 'found': 0, 'added': 0, 'removed': 0, 'error': ''}
        self._lock = threading.Lock()

    def status(self):
        return {'running': self.running, **self.last}

    def roots(self):
        """Folders to search: the usual music places, the app's own music folder and folders the user added."""
        extra = [line.strip() for line in self.settings['extra_music_folders'].splitlines() if line.strip()]
        music_dir = self.settings['music_dir'] or self.host.default_music_dir
        folders = list(dict.fromkeys([*self.host.music_roots, music_dir, *extra]))
        # A folder inside another listed folder is already covered by walking the outer one
        return [f for f in folders if not any(other != f and _inside(f, other) for other in folders)]

    def start(self, delay=0):
        with self._lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._run, args=(delay,), name='library-scan', daemon=True).start()
        return True

    def _run(self, delay):
        if delay:
            time.sleep(delay)
        self.events.bump()
        try:
            self.last = {**self._scan(), 'error': ''}
        except Exception as e:
            log.exception('Library scan failed')
            self.last = {**self.last, 'error': str(e)[:200]}
        finally:
            self.last['finished_at'] = time.time()
            self.running = False
            self.events.bump()

    def _scan(self):
        if not self.settings['scan_local_music']:
            return {'found': 0, 'added': 0, 'removed': self.db.delete_local_tracks_except(set())}

        library = {os.path.normcase(t['path']): t for t in self.db.list_tracks()}
        visited, seen, changed = set(), set(), []
        for path, size, mtime, meta in self._candidates():
            key = os.path.normcase(os.path.abspath(path))
            if key in visited:
                continue
            visited.add(key)
            existing = library.get(os.path.normcase(path))
            if existing and existing['source'] != 'local':
                continue  # a song the app downloaded; the library already knows it
            track_id = local_id(path)
            if existing and existing['size'] == size and abs(existing['mtime'] - mtime) < 2:
                seen.add(track_id)
                continue
            meta = meta or read_tags(path)
            if meta['duration'] and meta['duration'] < _MIN_SECONDS:
                continue
            seen.add(track_id)
            changed.append((track_id, path, size, mtime, meta))

        known = self.db.known_artists()
        raw_leads = {names.lead_artist(meta['artist'], known) for *_, meta in changed}
        raw_leads.discard('')
        with ThreadPoolExecutor(max_workers=4) as pool:
            canonical = dict(zip(raw_leads, pool.map(self._canonical, raw_leads)))

        rows = []
        for track_id, path, size, mtime, meta in changed:
            raw = names.lead_artist(meta['artist'], known)
            lead = canonical.get(raw, raw)
            rows.append({
                'video_id': track_id,
                'title': meta['title'],
                'artist': names.credit(meta['artist'], raw, lead),
                'album': meta['album'],
                'duration': meta['duration'],
                'thumb': f'/cover/{track_id}',
                'path': path,
                'size': size,
                'mtime': mtime,
                'lead_artist': lead,
                'title_key': names.title_key(meta['title']),
            })
        self.db.upsert_local_tracks(rows)
        removed = self.db.delete_local_tracks_except(seen)
        log.info('Library scan: %d local songs, %d new or changed, %d removed', len(seen), len(rows), removed)
        return {'found': len(seen), 'added': len(rows), 'removed': removed}

    def _canonical(self, name):
        try:
            return self.artists.canonical(name)
        except Exception:
            return name

    def _candidates(self):
        """(path, size, mtime, metadata or None) for every music file found."""
        if self.host.local_music_provider:
            for item in self.host.local_music_provider():
                path = item.get('path') or ''
                if not path or os.path.splitext(path)[1].lower() not in AUDIO_EXTENSIONS or not os.path.isfile(path):
                    continue
                artist = item.get('artist') or ''
                meta = {
                    'title': (item.get('title') or '')[:300],
                    'artist': '' if artist == '<unknown>' else artist[:300],
                    'album': (item.get('album') or '')[:300],
                    'duration': int((item.get('duration') or 0) / 1000),
                }
                yield path, int(item.get('size') or 0), float(item.get('mtime') or 0), meta if meta['title'] else None
            return

        count = 0
        for root in self.roots():
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and d.lower() not in _SKIP_DIRS]
                for name in filenames:
                    if os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    path = os.path.join(dirpath, name)
                    try:
                        info = os.stat(path)
                    except OSError:
                        continue
                    if info.st_size < _MIN_BYTES or getattr(info, 'st_file_attributes', 0) & _CLOUD_ATTRIBUTES:
                        continue
                    yield path, info.st_size, info.st_mtime, None
                    count += 1
                    if count >= _MAX_FILES:
                        return
