"""Watches followed artists for new singles and albums."""

import datetime
import logging
import threading
import time

from . import search

log = logging.getLogger(__name__)

CHECK_INTERVAL = 6 * 3600
_STARTUP_DELAY = 90


def check(db):
    """Record each followed artist's releases and return the ones that appeared since the previous check."""
    this_year = datetime.date.today().year
    recent_years = {str(this_year), str(this_year - 1)}
    found = []
    for follow in db.list_follows():
        try:
            artist = search.artist(follow['browseId'], full_releases=False)
        except Exception as e:
            log.info('Checking %s for new releases failed: %s', follow['name'], e)
            continue
        known = db.known_release_ids(follow['browseId'])
        fresh = [r for r in artist['singles'] + artist['albums'] if r['browseId'] not in known]
        if not fresh:
            continue
        # The first check of an artist only records what already exists. Later checks announce new releases,
        # but only recent ones: YouTube Music sometimes reshuffles an artist's page and surfaces old albums.
        announce = bool(known)
        rows = [{**r, 'is_new': announce and str(r['year']) in recent_years} for r in fresh]
        db.add_releases(follow['browseId'], follow['name'], rows)
        found.extend({**r, 'artist': follow['name'], 'artistId': follow['browseId']} for r in rows if r['is_new'])
    if found:
        log.info('New releases: %s', ', '.join(f"{r['artist']} - {r['title']}" for r in found))
    return found


class ReleaseWatcher:
    def __init__(self, db, settings, downloads, events):
        self.db = db
        self.settings = settings
        self.downloads = downloads
        self.events = events
        self.last_check = 0
        self.checking = False
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, name='release-watcher', daemon=True).start()

    def _loop(self):
        time.sleep(_STARTUP_DELAY)
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception('Release check failed')
            time.sleep(CHECK_INTERVAL)

    def run_once(self):
        with self._lock:
            if self.checking:
                return []
            self.checking = True
        try:
            found = check(self.db)
            self.save_pending()
            if found:
                self.events.bump()
            return found
        finally:
            self.checking = False
            self.last_check = time.time()

    def save_pending(self):
        """Save new releases for offline when that's switched on, including ones found while the app was closed."""
        if not self.settings['auto_download_new_releases']:
            return
        for release in self.db.releases_to_save():
            try:
                tracks = search.album(release['release_id'])['tracks']
            except Exception as e:
                log.info('Loading release %s failed: %s', release['release_id'], e)
                continue
            self.downloads.add(tracks)
            self.db.mark_release_saved(release['release_id'])
