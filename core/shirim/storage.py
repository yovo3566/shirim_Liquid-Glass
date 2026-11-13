"""Storage on the device: what the library takes, songs worth removing, and optional automatic clean-up."""

import logging
import os
import shutil
import threading
import time

log = logging.getLogger(__name__)

_CLEAN_INTERVAL = 24 * 3600
_FIRST_CLEAN_DELAY = 10 * 60


def _dir_size(path):
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def _protected_ids(app):
    """Songs that are never suggested for removal: liked, in a playlist, or kept for the paired device."""
    return set(app.db.liked_ids()) | app.db.playlist_video_ids()


def candidates(app, days):
    """Saved songs nobody played (or saved) in the last `days` days, largest first."""
    cutoff = time.time() - days * 86400
    last_played = app.db.last_played_map()
    protected = _protected_ids(app)
    result = []
    for track in app.db.list_tracks():
        if track['source'] != 'download' or track['video_id'] in protected:
            continue
        last = max(track['added_at'], last_played.get(track['video_id'], 0))
        if last >= cutoff or not os.path.isfile(track['path']):
            continue
        result.append({
            'videoId': track['video_id'], 'title': track['title'], 'artist': track['artist'],
            'album': track['album'], 'duration': track['duration'], 'thumb': track['thumb'],
            'size': track['size'], 'lastPlayed': last_played.get(track['video_id'], 0), 'addedAt': track['added_at'],
        })
    result.sort(key=lambda t: t['size'], reverse=True)
    return result


def summary(app, days):
    tracks = [t for t in app.db.list_tracks() if os.path.isfile(t['path'])]
    downloads = [t for t in tracks if t['source'] == 'download']
    local = [t for t in tracks if t['source'] == 'local']
    music_dir = app.settings['music_dir'] or app.host.default_music_dir
    try:
        usage = shutil.disk_usage(music_dir if os.path.isdir(music_dir) else app.host.data_dir)
        disk = {'total': usage.total, 'free': usage.free}
    except OSError:
        disk = {'total': 0, 'free': 0}
    by_artist = {}
    for track in downloads:
        name = track['lead_artist'] or track['artist'] or 'לא ידוע'
        entry = by_artist.setdefault(name, {'name': name, 'size': 0, 'count': 0})
        entry['size'] += track['size']
        entry['count'] += 1
    old = candidates(app, days)
    return {
        'downloads': {'count': len(downloads), 'size': sum(t['size'] for t in downloads)},
        'local': {'count': len(local), 'size': sum(t['size'] for t in local)},
        'cache': _dir_size(app.host.cache_dir),
        'disk': disk,
        'artists': sorted(by_artist.values(), key=lambda a: a['size'], reverse=True)[:12],
        'candidates': old,
        'candidatesSize': sum(t['size'] for t in old),
        'days': days,
    }


def remove_songs(app, video_ids):
    """Delete saved songs (never files that were on the device before). Returns (removed, bytes freed)."""
    removed, freed = 0, 0
    for video_id in video_ids:
        track = app.db.get_track(str(video_id))
        if not track or track['source'] != 'download':
            continue
        try:
            if os.path.isfile(track['path']):
                size = os.path.getsize(track['path'])
                os.remove(track['path'])
                freed += size
                # Lets Android forget the deleted file
                if app.host.on_file_saved:
                    try:
                        app.host.on_file_saved(track['path'])
                    except Exception:
                        pass
        except OSError as e:
            log.warning('Could not delete %s: %s', track['path'], e)
            continue
        app.db.delete_track(track['video_id'])
        removed += 1
        folder = os.path.dirname(track['path'])
        try:
            if folder != app.settings['music_dir'] and not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            pass
    return removed, freed


def clear_cache(app):
    """Remove cached covers, images and temporary files. Returns bytes freed."""
    freed = 0
    for name in ('images', 'covers'):
        folder = os.path.join(app.host.cache_dir, name)
        freed += _dir_size(folder)
        shutil.rmtree(folder, ignore_errors=True)
    return freed


class AutoCleaner:
    """Once a day, removes saved songs not played for the chosen number of days (when turned on)."""

    def __init__(self, app):
        self.app = app
        self.last_run = 0
        self.last_result = None

    def start(self):
        threading.Thread(target=self._loop, name='auto-clean', daemon=True).start()

    def _loop(self):
        time.sleep(_FIRST_CLEAN_DELAY)
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception('Automatic clean-up failed')
            time.sleep(_CLEAN_INTERVAL)

    def run_once(self):
        settings = self.app.settings
        if not settings['auto_clean']:
            return None
        # Don't delete while songs are still downloading, or right after the app starts using a new library
        old = candidates(self.app, settings['auto_clean_days'])
        removed, freed = remove_songs(self.app, [t['videoId'] for t in old])
        self.last_run = time.time()
        self.last_result = {'removed': removed, 'freed': freed}
        if removed:
            log.info('Automatic clean-up removed %d songs (%d bytes)', removed, freed)
            self.app.events.bump()
        return self.last_result
