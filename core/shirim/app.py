"""Wires settings, storage, streaming, downloads, the library scanner and background watchers behind the HTTP server."""

import os
import threading

# features registers the discovery, release, USB, lyrics, storage, import, recap and sync routes on the server
from . import features, names  # noqa: F401
from .artists import ArtistNames
from .content_filter import ContentFilter
from .db import Database
from .downloader import DownloadManager
from .events import Events
from .importer import Importer
from .releases import ReleaseWatcher
from .scanner import LibraryScanner
from .server import make_server
from .settings import Settings
from .storage import AutoCleaner
from .stream import StreamResolver
from .sync import SyncClient, SyncServer
from .usb import UsbExporter

DEFAULT_PORT = 8765
_SCAN_DELAY = 5  # let the UI load before walking the disk


class App:
    def __init__(self, host):
        self.host = host
        self.settings = Settings(os.path.join(host.data_dir, 'settings.json'),
                                 {'music_dir': host.default_music_dir})
        if self.settings['audio_format'] not in host.formats:
            self.settings.update({'audio_format': 'm4a'})
        self.db = Database(os.path.join(host.data_dir, 'library.db'))
        self._backfill_song_keys()
        self.artists = ArtistNames(self.db)
        self.events = Events()
        self.filter = ContentFilter(self.db, self.settings, host)
        self.downloads = DownloadManager(self.db, self.settings, host, self.events, self.artists)
        self.downloads.content_filter = self.filter
        self.streams = StreamResolver(self.settings, host)
        self.scanner = LibraryScanner(self.db, self.settings, host, self.artists, self.events)
        self.releases = ReleaseWatcher(self.db, self.settings, self.downloads, self.events)
        self.usb = UsbExporter(self.db, host, self.events)
        self.importer = Importer(self.db, self.events)
        self.cleaner = AutoCleaner(self)
        self.sync_server = SyncServer(self)
        self.sync = SyncClient(self)
        self.downloads.peer = self.sync
        # Host-specific callbacks exposed to the UI, e.g. 'login' or 'open_folder'
        self.actions = {}
        self.server = None

    def _backfill_song_keys(self):
        """Tracks downloaded before duplicate detection existed lack its lookup keys."""
        tracks = [t for t in self.db.list_tracks() if not t['title_key']]
        if not tracks:
            return
        known = {names.lead_artist(t['artist']) for t in tracks}
        self.db.update_tracks([
            (t['video_id'], {'lead_artist': names.lead_artist(t['artist'], known),
                             'title_key': names.title_key(t['title'])})
            for t in tracks])

    def start(self, port=DEFAULT_PORT, bind=None):
        if bind is None:
            remote = self.settings['remote_access'] and self.host.platform != 'android'
            bind = '0.0.0.0' if remote else '127.0.0.1'
        self.downloads.start()
        self.server = make_server(self, bind, port)
        threading.Thread(target=self.server.serve_forever, name='http', daemon=True).start()
        self.scanner.start(delay=_SCAN_DELAY)
        self.releases.start()
        self.cleaner.start()
        self.sync.start()
        self.filter.warm_up()
        return self.server.server_address[1]

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
