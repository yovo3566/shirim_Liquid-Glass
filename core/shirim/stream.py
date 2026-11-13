"""Instant playback: resolve a song's YouTube audio stream and relay it to the player, seeking included.

Stream addresses are tied to the IP that resolved them, so the app always relays the bytes itself; that also
lets an iPhone on the home network play through the PC.
"""

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

from . import tagging, ytdl

log = logging.getLogger(__name__)

_CHUNK = 256 * 1024
_RELAYED_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges')
_PREPARE_AHEAD = 3


class StreamError(Exception):
    def __init__(self, message, blocked=False):
        super().__init__(message)
        self.blocked = blocked


class StreamResolver:
    def __init__(self, settings, host):
        self.settings = settings
        self.host = host
        self._cache = {}
        self._locks = {}
        self._guard = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='stream-prepare')

    def _lock_for(self, video_id):
        with self._guard:
            now = time.time()
            for stale in [k for k, v in self._cache.items() if v['expires'] < now]:
                self._cache.pop(stale, None)
                self._locks.pop(stale, None)
            return self._locks.setdefault(video_id, threading.Lock())

    def resolve(self, video_id, refresh=False):
        # One resolution per song at a time: the player and the pre-loader often ask for the same one
        with self._lock_for(video_id):
            entry = self._cache.get(video_id)
            if entry and not refresh and entry['expires'] > time.time() + 120:
                return entry
            opts = ytdl.base_options(self.settings, self.host)
            opts['format'] = ytdl.stream_format(self.settings)
            try:
                with ytdl.cookie_file(self.settings, self.host) as cookies:
                    if cookies:
                        opts['cookiefile'] = cookies
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
            except Exception as e:
                raise StreamError(ytdl.clean_error(e), blocked=ytdl.is_bot_check(e)) from e

            fmt = (info.get('requested_formats') or [info])[0]
            if not fmt.get('url'):
                raise StreamError('No playable audio for this song')
            query = urllib.parse.parse_qs(urllib.parse.urlparse(fmt['url']).query)
            expire = query.get('expire', [''])[0]
            entry = {
                'url': fmt['url'],
                'headers': dict(fmt.get('http_headers') or {}),
                'expires': int(expire) if expire.isdigit() else time.time() + 3600,
                'mime': 'audio/mp4' if fmt.get('ext') == 'm4a' else f"audio/{fmt.get('ext') or 'webm'}",
            }
            self._cache[video_id] = entry
            return entry

    def prepare(self, video_ids):
        """Resolve upcoming songs in the background so they start without a wait."""
        for video_id in video_ids[:_PREPARE_AHEAD]:
            self._pool.submit(self._prepare_one, video_id)

    def _prepare_one(self, video_id):
        try:
            self.resolve(video_id)
        except StreamError as e:
            log.info('Preparing %s failed: %s', video_id, e)

    def relay(self, handler, video_id):
        """Send the song to an HTTP request handler, forwarding its Range header."""
        range_header = handler.headers.get('Range')
        response = None
        for attempt in range(2):
            entry = self.resolve(video_id, refresh=attempt > 0)
            headers = dict(entry['headers'])
            if range_header:
                headers['Range'] = range_header
            request = urllib.request.Request(entry['url'], headers=headers)
            try:
                response = urllib.request.urlopen(request, timeout=20, context=tagging._SSL)
                break
            except urllib.error.HTTPError as e:
                if e.code == 416:
                    handler.send_response(416)
                    handler.send_header('Content-Length', '0')
                    handler.end_headers()
                    return
                if attempt == 0 and e.code in (403, 404, 410):
                    continue  # the address expired or was rotated; resolve a fresh one
                raise StreamError(f'YouTube returned HTTP {e.code}') from e

        with response:
            handler.send_response(response.status)
            for name in _RELAYED_HEADERS:
                if response.headers.get(name):
                    handler.send_header(name, response.headers[name])
            if not response.headers.get('Content-Type'):
                handler.send_header('Content-Type', entry['mime'])
            handler.send_header('Cache-Control', 'no-store')
            handler.end_headers()
            while chunk := response.read(_CHUNK):
                handler.wfile.write(chunk)
