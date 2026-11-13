"""HTTP API, static web UI, audio streaming and image relay for the Shirim front-end."""

import glob
import hashlib
import hmac
import http.cookies
import http.server
import ipaddress
import json
import logging
import os
import re
import socket
import socketserver
import time
import urllib.parse
import zipfile

from . import __version__, cookies, downloader, home, scanner, search, tagging
from .content_filter import Hidden
from .settings import SECRET_SETTINGS
from .stream import StreamError
from .sync import TOKEN_HEADER
from .ytdl import clean_error

log = logging.getLogger(__name__)

PIN_COOKIE = 'shirim_pin'
_MAX_BODY = 8 * 1024 * 1024
_CHUNK = 256 * 1024
_SENT = object()
_VIDEO_ID = re.compile(r'[\w-]{11}')
# Songs are YouTube videos or music files found on the device
_TRACK_ID = re.compile(r'[\w-]{11}|local-[0-9a-f]{16}')
_LOCAL_ONLY_SETTINGS = ('remote_access', 'remote_pin', 'music_dir', 'extra_music_folders', 'auto_clean',
                        'auto_clean_days')
# Managed by the sync routes, never set directly
_SYNC_SETTINGS = ('device_id', 'sync_peer', 'sync_peer_id', 'sync_peer_name', 'sync_token', 'sync_last',
                  'sync_plays_cursor')
_SCAN_SETTINGS = ('scan_local_music', 'extra_music_folders')
_PROTECTED_PREFIXES = ('/api/', '/media/', '/stream/', '/cover/', '/img')
# Reachable from the network without the PIN: pairing is protected by its own one-time code
_PUBLIC_PATHS = ('/api/info', '/api/pin', '/api/sync/hello', '/api/sync/pair')
# What a paired phone may use with its sync token
_SYNC_DEVICE_PREFIXES = ('/api/sync/', '/media/')
# Changes a paired device should receive soon
_COLLECTION_PREFIXES = ('/api/likes/', '/api/playlists/', '/api/follows/')
# Changing these needs the filter code when one is set
_FILTER_SETTINGS = ('filter_enabled', 'filter_unknown', 'filter_pin')
_IMAGE_HOSTS = ('lh3.googleusercontent.com', 'yt3.googleusercontent.com', 'yt3.ggpht.com', 'i.ytimg.com', 'i9.ytimg.com')
_IMAGE_EXTENSIONS = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}

_AUDIO_TYPES = {
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.mp3': 'audio/mpeg',
    '.flac': 'audio/flac',
    '.wav': 'audio/wav',
    '.opus': 'audio/ogg',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.webm': 'audio/webm',
}
_STATIC_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.webp': 'image/webp',
    '.ico': 'image/x-icon',
    '.json': 'application/json',
    '.webmanifest': 'application/manifest+json',
}

ROUTES = {}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def route(method, path):
    def register(fn):
        ROUTES[method, path] = fn
        return fn
    return register


def _require(data, key):
    value = data.get(key)
    if value in (None, ''):
        raise ApiError(400, f'Missing parameter: {key}')
    return value


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _job_ids(body):
    ids = body.get('ids')
    if ids is None:
        return None
    if not isinstance(ids, list):
        raise ApiError(400, 'ids must be a list')
    return [_int(i, -1) for i in ids]


def _track(value):
    """A song sent by the UI, validated; None when unusable."""
    if not isinstance(value, dict):
        return None
    video_id = str(value.get('videoId') or value.get('video_id') or '')
    if not _TRACK_ID.fullmatch(video_id):
        return None
    return {
        'videoId': video_id,
        'title': str(value.get('title') or '')[:300],
        'artist': str(value.get('artist') or '')[:300],
        'album': str(value.get('album') or '')[:300],
        'duration': _int(value.get('duration')),
        'thumb': str(value.get('thumb') or '')[:1000],
    }


def _tracks(values):
    if not isinstance(values, list):
        raise ApiError(400, 'tracks must be a list')
    return [t for t in map(_track, values) if t]


def _is_youtube(video_id):
    return bool(_VIDEO_ID.fullmatch(video_id))


def _same_secret(given, expected):
    return hmac.compare_digest(given.encode(), expected.encode())


def _library_file(app, video_id):
    track = app.db.get_track(video_id)
    return track['path'] if track and os.path.isfile(track['path']) else None


def _localize_artists(app, cards):
    """Show artists under the library's spelling (e.g. Hebrew) instead of YouTube Music's."""
    for card in cards:
        if card and card.get('browseId'):
            aliases = app.db.aliases_for_browse_id(card['browseId'])
            if aliases:
                card['name'] = aliases[0]['canonical']
    return cards


def _cached_image(folder, stem, load):
    """Path of a cached image, fetching it with load() -> (bytes, mime) the first time."""
    cached = glob.glob(glob.escape(stem) + '.*')
    if cached:
        return cached[0]
    image = load()
    if not image or image[1] not in _IMAGE_EXTENSIONS:
        raise ApiError(404, 'Image unavailable')
    os.makedirs(folder, exist_ok=True)
    path = stem + _IMAGE_EXTENSIONS[image[1]]
    with open(path + '.tmp', 'wb') as f:
        f.write(image[0])
    os.replace(path + '.tmp', path)
    return path


def lan_addresses():
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Connecting a UDP socket sends nothing; it just picks the outgoing interface
            s.connect(('10.255.255.255', 1))
            addresses.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addresses if ipaddress.ip_address(a).is_private and not a.startswith('127.'))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Shirim/' + __version__
    app = None  # bound by make_server

    def log_message(self, fmt, *args):
        log.debug('%s %s', self.address_string(), fmt % args)

    def end_headers(self):
        self.headers_sent = True
        super().end_headers()

    @property
    def is_remote(self):
        return not ipaddress.ip_address(self.client_address[0]).is_loopback

    def cookie(self, name):
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie') or '')
        except http.cookies.CookieError:
            return ''
        return urllib.parse.unquote(jar[name].value) if name in jar else ''

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _dispatch(self, method):
        self.headers_sent = False
        self.extra_headers = []
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            body = self._read_json() if method == 'POST' else {}
            self._check_host()
            token = self.headers.get(TOKEN_HEADER)
            self.sync_device = self.app.sync_server.device_for(token) if token else None
            if self.is_remote:
                self._check_remote(path)
            if path.startswith('/api/'):
                handler = ROUTES.get((method, path))
                if handler is None:
                    raise ApiError(404, 'Not found')
                # Browsers can't add custom headers to cross-site requests without CORS approval
                if method == 'POST' and self.headers.get('X-Shirim') != '1':
                    raise ApiError(403, 'Forbidden')
                result = handler(self, query, body)
                if method == 'POST' and path.startswith(_COLLECTION_PREFIXES):
                    self.app.sync.touch()
                if result is not _SENT:
                    if self.app.filter.applies_to(path):
                        try:
                            result = self.app.filter.filter_payload(result)
                        except Hidden:
                            raise ApiError(403, 'Hidden in filtered mode') from None
                    self.send_json(result)
            elif method == 'GET' and path.startswith('/media/'):
                self._serve_media(urllib.parse.unquote(path[len('/media/'):]), query)
            elif method == 'GET' and path.startswith('/stream/'):
                self._serve_stream(urllib.parse.unquote(path[len('/stream/'):]))
            elif method == 'GET' and path.startswith('/cover/'):
                self._serve_cover(urllib.parse.unquote(path[len('/cover/'):]))
            elif method == 'GET' and path == '/img':
                self._serve_image(query.get('u', ''))
            elif method == 'GET':
                self._serve_static(path)
            else:
                raise ApiError(405, 'Method not allowed')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as e:
            if isinstance(e, ApiError):
                status, message = e.status, str(e)
            else:
                log.exception('%s %s failed', method, self.path)
                status, message = 500, clean_error(e)
            if self.headers_sent:
                self.close_connection = True
                return
            try:
                self.send_json({'error': message}, status)
            except OSError:
                self.close_connection = True

    def _check_host(self):
        host = (self.headers.get('Host') or '').strip()
        if not host.endswith(']'):
            host = host.rsplit(':', 1)[0]
        host = host.strip('[]')
        if host == 'localhost':
            return
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Arbitrary hostnames pointing at a local server are how DNS-rebinding attacks get in
            raise ApiError(403, 'Forbidden host') from None

    def _check_remote(self, path):
        settings = self.app.settings
        if not settings['remote_access']:
            raise ApiError(403, 'Remote access is disabled')
        pin = settings['remote_pin']
        if not pin or path in _PUBLIC_PATHS:
            return
        if self.sync_device and path.startswith(_SYNC_DEVICE_PREFIXES):
            return
        if not path.startswith(_PROTECTED_PREFIXES):
            return
        if not _same_secret(self.cookie(PIN_COOKIE), pin):
            raise ApiError(401, 'PIN required')

    def _read_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > _MAX_BODY:
            raise ApiError(413, 'Request too large')
        raw = self.rfile.read(length) if length else b''
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError(400, 'Invalid JSON') from None
        if not isinstance(data, dict):
            raise ApiError(400, 'Expected a JSON object')
        return data

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in self.extra_headers:
            self.send_header(name, value)
        if status >= 400:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path, content_type, download_name=None, cache='no-cache'):
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', (self.headers.get('Range') or '').strip())
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
            else:
                start = max(0, size - int(match.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            status = 206

        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', cache)
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download_name:
            ascii_name = download_name.encode('ascii', 'replace').decode().replace('?', '_').replace('"', '')
            self.send_header('Content-Disposition', f'attachment; filename="{ascii_name}"; '
                             f"filename*=UTF-8''{urllib.parse.quote(download_name)}")
        self.end_headers()

        remaining = end - start + 1
        with open(path, 'rb') as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        if remaining:
            self.close_connection = True

    def _serve_audio_file(self, path, download_name=None):
        ext = os.path.splitext(path)[1].lower()
        self.send_file(path, _AUDIO_TYPES.get(ext, 'application/octet-stream'), download_name)

    def _serve_media(self, video_id, query):
        path = _library_file(self.app, video_id)
        if not path:
            raise ApiError(404, 'File not found')
        self._serve_audio_file(path, os.path.basename(path) if query.get('download') else None)

    def _serve_stream(self, video_id):
        """Play a song: from the device when it's there, otherwise straight from YouTube."""
        if not _TRACK_ID.fullmatch(video_id):
            raise ApiError(404, 'Not found')
        if self.app.filter.active and not self.app.filter.song_allowed_by_id(video_id):
            raise ApiError(403, 'Hidden in filtered mode')
        path = _library_file(self.app, video_id)
        if path:
            self._serve_audio_file(path)
            return
        # At home, songs saved on the paired computer play from it: faster, and YouTube can't block them
        if self.app.sync.has(video_id) and self.app.sync.relay(self, video_id):
            return
        if not _is_youtube(video_id):
            raise ApiError(404, 'The file is no longer on this device')
        try:
            self.app.streams.relay(self, video_id)
        except StreamError as e:
            raise ApiError(503 if e.blocked else 502, str(e)) from None

    def _serve_cover(self, track_id):
        """Cover art embedded in a music file found on the device."""
        if not _TRACK_ID.fullmatch(track_id) or self.app.filter.active:
            raise ApiError(404, 'Not found')
        folder = os.path.join(self.app.host.cache_dir, 'covers')

        def load():
            source = _library_file(self.app, track_id)
            return scanner.extract_cover(source) if source else None

        path = _cached_image(folder, os.path.join(folder, track_id), load)
        mime = next(m for m, ext in _IMAGE_EXTENSIONS.items() if path.endswith(ext))
        self.send_file(path, mime, cache='max-age=86400')

    def _serve_image(self, url):
        if self.app.filter.active:
            raise ApiError(404, 'Pictures are hidden in filtered mode')
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in _IMAGE_HOSTS:
            raise ApiError(400, 'Unsupported image address')
        folder = os.path.join(self.app.host.cache_dir, 'images')
        path = _cached_image(folder, os.path.join(folder, hashlib.sha1(url.encode()).hexdigest()),
                             lambda: tagging.fetch_cover(url))
        mime = next(m for m, ext in _IMAGE_EXTENSIONS.items() if path.endswith(ext))
        self.send_file(path, mime, cache='max-age=604800')

    def _serve_static(self, path):
        web_dir = os.path.realpath(self.app.host.web_dir)
        relative = urllib.parse.unquote(path).lstrip('/') or 'index.html'
        full = os.path.realpath(os.path.join(web_dir, relative))
        if not full.startswith(web_dir + os.sep) or not os.path.isfile(full):
            # Single-page app: unknown paths get the UI shell
            full = os.path.join(web_dir, 'index.html')
        ext = os.path.splitext(full)[1].lower()
        self.send_file(full, _STATIC_TYPES.get(ext, 'application/octet-stream'))


# region routes: app

@route('GET', '/api/info')
def _info(req, query, body):
    app = req.app
    bind, port = req.server.server_address[:2]
    pin = app.settings['remote_pin']
    return {
        'version': __version__,
        'platform': app.host.platform,
        'remote': req.is_remote,
        'pinRequired': bool(req.is_remote and pin and not _same_secret(req.cookie(PIN_COOKIE), pin)),
        'hasFfmpeg': bool(app.host.ffmpeg_dir),
        'formats': app.host.formats,
        'folderChoices': [] if req.is_remote else app.host.folder_choices,
        'hasPot': bool(app.host.pot_args),
        'actions': [] if req.is_remote else sorted(app.actions),
        'lanUrls': [f'http://{ip}:{port}' for ip in lan_addresses()] if bind == '0.0.0.0' else [],
        'cookies': cookies.status(app.host.cookie_file),
        'musicRoots': [] if req.is_remote or app.host.local_music_provider else app.scanner.roots(),
        'scan': app.scanner.status(),
        'deviceName': app.host.device_name or socket.gethostname(),
        'filter': app.filter.status(),
    }


@route('POST', '/api/pin')
def _pin(req, query, body):
    pin = req.app.settings['remote_pin']
    if pin and not _same_secret(str(body.get('pin', '')), pin):
        time.sleep(1)  # slow down guessing
        raise ApiError(401, 'Wrong PIN')
    req.extra_headers.append((
        'Set-Cookie', f'{PIN_COOKIE}={urllib.parse.quote(pin)}; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly'))
    return {'ok': True}


def _visible_settings(req, data):
    data.pop('sync_token', None)
    data.pop('filter_pin', None)  # the UI only learns whether the filter is locked (info.filter.locked)
    if req.is_remote:
        for key in SECRET_SETTINGS:
            data.pop(key, None)
    return data


@route('GET', '/api/settings')
def _settings(req, query, body):
    return _visible_settings(req, req.app.settings.to_dict())


@route('POST', '/api/settings')
def _update_settings(req, query, body):
    app = req.app
    changes = dict(body)
    filter_code = str(changes.pop('filterPin', '') or '')
    for key in _SYNC_SETTINGS:
        changes.pop(key, None)
    if req.is_remote:
        for key in (*_LOCAL_ONLY_SETTINGS, *_FILTER_SETTINGS):
            changes.pop(key, None)
    if any(key in changes for key in _FILTER_SETTINGS):
        if app.host.filter_forced:
            raise ApiError(403, 'The filtered edition cannot change the filter')
        locked_code = app.settings['filter_pin']
        # Turning the filter on or making it stricter never needs the code
        turning_off = 'filter_enabled' in changes and str(changes['filter_enabled']).strip().lower() in ('false', '0', '')
        loosening = turning_off or changes.get('filter_unknown') == 'show' or 'filter_pin' in changes
        if locked_code and loosening and not _same_secret(filter_code, locked_code):
            time.sleep(1)  # slow down guessing
            raise ApiError(403, 'Wrong filter code')
        if 'filter_pin' in changes and not re.fullmatch(r'(\d{4,8})?', str(changes['filter_pin'])):
            raise ApiError(400, 'Filter code must be 4 to 8 digits')
    if 'remote_pin' in changes and not re.fullmatch(r'\d{0,8}', str(changes['remote_pin'])):
        raise ApiError(400, 'PIN must be up to 8 digits')
    if 'music_dir' in changes:
        path = os.path.abspath(os.path.expanduser(str(changes['music_dir'])))
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise ApiError(400, f'Cannot use folder: {e}') from None
        changes['music_dir'] = path
    if changes.get('audio_format') not in (None, *app.host.formats):
        raise ApiError(400, 'This format is not available on this device')
    if 'music_dir' in changes and app.host.folder_choices and \
            changes['music_dir'] not in {choice['path'] for choice in app.host.folder_choices}:
        raise ApiError(400, 'Cannot use folder')
    try:
        data = app.settings.update(changes)
    except (TypeError, ValueError) as e:
        raise ApiError(400, str(e)) from None
    app.downloads.settings_changed()
    if any(key in changes for key in _SCAN_SETTINGS):
        app.scanner.start()
    if 'filter_enabled' in changes:
        app.filter.warm_up()
    return _visible_settings(req, data)


@route('POST', '/api/action')
def _action(req, query, body):
    if req.is_remote:
        raise ApiError(403, 'Only available on this device')
    name = _require(body, 'name')
    action = req.app.actions.get(name)
    if action is None:
        raise ApiError(404, f'Unknown action: {name}')
    result = action(body)
    return {'ok': True} if result is None else result


@route('GET', '/api/scan')
def _scan_status(req, query, body):
    return req.app.scanner.status()


@route('POST', '/api/scan')
def _scan(req, query, body):
    return {'started': req.app.scanner.start()}

# endregion


# region routes: catalogue

@route('GET', '/api/home')
def _home(req, query, body):
    data = home.build(req.app.db)
    for section in data['sections']:
        if section['kind'] == 'artists':
            _localize_artists(req.app, section['items'])
    return data


@route('GET', '/api/mix')
def _mix(req, query, body):
    try:
        return home.mix(req.app.db, _require(query, 'id'))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route('GET', '/api/radio')
def _radio(req, query, body):
    if query.get('artistId'):
        return search.artist_radio(query['artistId'])
    video_id = _require(query, 'videoId')
    # Similar songs come from YouTube Music, which doesn't know files found on the device
    return search.radio(video_id) if _is_youtube(video_id) else []


@route('GET', '/api/search')
def _search(req, query, body):
    text = query.get('q', '').strip()
    kind = query.get('kind', 'songs')
    if kind not in ('songs', 'artists', 'albums', 'videos', 'playlists'):
        raise ApiError(400, f'Unknown kind: {kind}')
    if not text:
        return []
    results = search.search(text, kind)
    return _localize_artists(req.app, results) if kind == 'artists' else results


@route('GET', '/api/search/all')
def _search_all(req, query, body):
    text = query.get('q', '').strip()
    if not text:
        return {}
    results = search.search_all(text)
    if results['top'] and results['top']['type'] == 'artist':
        _localize_artists(req.app, [results['top']])
    _localize_artists(req.app, results['artists'])
    return results


@route('GET', '/api/suggest')
def _suggest(req, query, body):
    text = query.get('q', '').strip()
    return search.suggestions(text) if text else []


@route('GET', '/api/artist')
def _artist(req, query, body):
    artist = search.artist(_require(query, 'id'))
    _localize_artists(req.app, [artist, *artist['related']])
    return artist


@route('GET', '/api/artist/songs')
def _artist_songs(req, query, body):
    return search.artist_songs(_require(query, 'id'), query.get('releases') == '1')


@route('GET', '/api/album')
def _album(req, query, body):
    return search.album(_require(query, 'id'))


@route('GET', '/api/ytplaylist')
def _yt_playlist(req, query, body):
    return search.playlist(_require(query, 'id'))


@route('POST', '/api/resolve')
def _resolve(req, query, body):
    try:
        return downloader.resolve_url(str(_require(body, 'url')).strip())
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route('POST', '/api/stream/prepare')
def _stream_prepare(req, query, body):
    """Resolve songs ahead of playback. With wait, the first song is resolved now and its error returned."""
    app = req.app
    ids = [i for i in body.get('videoIds') or [] if isinstance(i, str) and _VIDEO_ID.fullmatch(i)]
    pending = [i for i in ids if not _library_file(app, i)]
    if body.get('wait') and pending and pending[0] == ids[0]:
        try:
            app.streams.resolve(pending.pop(0))
        except StreamError as e:
            return {'ok': False, 'error': str(e), 'blocked': e.blocked}
    app.streams.prepare(pending)
    return {'ok': True}

# endregion


# region routes: your collection

@route('GET', '/api/collection')
def _collection(req, query, body):
    db = req.app.db
    return {
        'likes': db.liked_ids(),
        'follows': db.list_follows(),
        'playlists': db.list_playlists(),
    }


@route('GET', '/api/likes')
def _likes(req, query, body):
    return req.app.db.list_likes()


@route('POST', '/api/likes/set')
def _set_like(req, query, body):
    app = req.app
    track = _track(body.get('track'))
    if not track:
        raise ApiError(400, 'Invalid track')
    liked = bool(body.get('liked'))
    app.db.set_like(track, liked)
    if (liked and app.settings['auto_download_likes'] and _is_youtube(track['videoId'])
            and not _library_file(app, track['videoId'])):
        app.downloads.add([track])
    return {'liked': liked}


@route('GET', '/api/playlists')
def _playlists(req, query, body):
    return req.app.db.list_playlists()


@route('GET', '/api/playlists/get')
def _playlist(req, query, body):
    playlist = req.app.db.get_playlist(_int(_require(query, 'id')))
    if not playlist:
        raise ApiError(404, 'Playlist not found')
    return playlist


@route('POST', '/api/playlists/create')
def _create_playlist(req, query, body):
    name = str(body.get('name') or '').strip()[:100] or 'פלייליסט חדש'
    db = req.app.db
    playlist_id = db.create_playlist(name)
    tracks = _tracks(body.get('tracks') or [])
    if tracks:
        db.add_to_playlist(playlist_id, tracks)
    return {'id': playlist_id, 'name': name}


@route('POST', '/api/playlists/rename')
def _rename_playlist(req, query, body):
    name = str(_require(body, 'name')).strip()[:100]
    if not req.app.db.rename_playlist(_int(_require(body, 'id')), name):
        raise ApiError(404, 'Playlist not found')
    return {'ok': True}


@route('POST', '/api/playlists/delete')
def _delete_playlist(req, query, body):
    req.app.db.delete_playlist(_int(_require(body, 'id')))
    return {'ok': True}


@route('POST', '/api/playlists/add')
def _add_to_playlist(req, query, body):
    return {'added': req.app.db.add_to_playlist(_int(_require(body, 'id')), _tracks(body.get('tracks')))}


@route('POST', '/api/playlists/remove')
def _remove_from_playlist(req, query, body):
    item_ids = [_int(i, -1) for i in body.get('itemIds') or []]
    req.app.db.remove_from_playlist(_int(_require(body, 'id')), item_ids)
    return {'ok': True}


@route('POST', '/api/playlists/move')
def _move_in_playlist(req, query, body):
    req.app.db.move_playlist_item(_int(_require(body, 'id')), _int(_require(body, 'itemId')), _int(body.get('index')))
    return {'ok': True}


@route('GET', '/api/follows')
def _follows(req, query, body):
    return req.app.db.list_follows()


@route('POST', '/api/follows/set')
def _set_follow(req, query, body):
    db = req.app.db
    artist = body.get('artist') if isinstance(body.get('artist'), dict) else {}
    browse_id = str(artist.get('browseId') or '')
    if not re.fullmatch(r'[\w-]{10,40}', browse_id):
        raise ApiError(400, 'Invalid artist')
    followed = bool(body.get('followed'))
    aliases = db.aliases_for_browse_id(browse_id)
    name = aliases[0]['canonical'] if aliases else str(artist.get('name') or '')[:200]
    db.set_follow(browse_id, name, str(artist.get('thumb') or '')[:1000], followed)
    return {'followed': followed}


@route('POST', '/api/plays')
def _add_play(req, query, body):
    track = _track(body.get('track'))
    if track:
        req.app.db.add_play(track)
    return {'ok': True}

# endregion


# region routes: downloads and library

@route('POST', '/api/queue/add')
def _queue_add(req, query, body):
    tracks = body.get('tracks')
    downloadable = [t for t in _tracks(tracks) if _is_youtube(t['videoId'])]
    return {'added': req.app.downloads.add(downloadable), 'requested': len(tracks)}


@route('GET', '/api/queue/state')
def _queue_state(req, query, body):
    app = req.app
    since = _int(query.get('since'), -1)
    version = app.events.wait(since) if since == app.events.version else app.events.version
    return {'version': version, **app.downloads.snapshot(), 'scan': app.scanner.status(),
            'releasesUnseen': app.db.unseen_release_count(), 'usb': app.usb.status,
            'sync': {'paired': app.sync.paired, 'syncing': app.sync.syncing, 'version': app.sync.version,
                     'error': app.sync.error, 'update': app.sync.update, 'installing': app.sync.installing}}


@route('GET', '/api/queue/jobs')
def _queue_jobs(req, query, body):
    return req.app.db.list_jobs()


@route('POST', '/api/queue/pause')
def _queue_pause(req, query, body):
    req.app.downloads.pause()
    return {'ok': True}


@route('POST', '/api/queue/resume')
def _queue_resume(req, query, body):
    req.app.downloads.resume()
    return {'ok': True}


@route('POST', '/api/queue/cancel')
def _queue_cancel(req, query, body):
    req.app.downloads.cancel(_job_ids(body))
    return {'ok': True}


@route('POST', '/api/queue/retry')
def _queue_retry(req, query, body):
    return {'retried': req.app.downloads.retry(_job_ids(body))}


@route('POST', '/api/queue/clear')
def _queue_clear(req, query, body):
    req.app.downloads.clear_finished()
    return {'ok': True}


@route('GET', '/api/library')
def _library(req, query, body):
    tracks = req.app.db.list_tracks()
    for track in tracks:
        track['exists'] = os.path.isfile(track['path'])
        track['format'] = os.path.splitext(track['path'])[1].lstrip('.').lower()
        if req.is_remote:
            del track['path']
    return tracks


@route('POST', '/api/library/delete')
def _library_delete(req, query, body):
    video_ids = body.get('videoIds') or []
    delete_files = bool(body.get('deleteFiles'))
    removed = 0
    for video_id in video_ids:
        track = req.app.db.get_track(str(video_id))
        if not track:
            continue
        if delete_files and os.path.isfile(track['path']):
            try:
                os.remove(track['path'])
            except OSError as e:
                raise ApiError(500, f'Could not delete {os.path.basename(track["path"])}: {e}') from None
        req.app.db.delete_track(track['video_id'])
        removed += 1
    return {'removed': removed}


@route('GET', '/api/library/zip')
def _library_zip(req, query, body):
    ids = [i for i in query.get('ids', '').split(',') if i]
    tracks = [t for t in map(req.app.db.get_track, ids) if t and os.path.isfile(t['path'])]
    if not tracks:
        raise ApiError(404, 'No files to download')
    req.send_response(200)
    req.send_header('Content-Type', 'application/zip')
    req.send_header('Content-Disposition', 'attachment; filename="shirim.zip"')
    req.send_header('Connection', 'close')
    req.end_headers()
    used = set()
    # The socket isn't seekable, so zipfile streams entries with data descriptors
    with zipfile.ZipFile(req.wfile, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        for track in tracks:
            name = os.path.basename(track['path'])
            stem, ext = os.path.splitext(name)
            counter = 2
            while name.lower() in used:
                name = f'{stem} ({counter}){ext}'
                counter += 1
            used.add(name.lower())
            archive.write(track['path'], name)
    return _SENT


@route('POST', '/api/cookies/import')
def _cookies_import(req, query, body):
    if req.is_remote:
        raise ApiError(403, 'Only available on this device')
    try:
        count, logged_in = cookies.save(cookies.parse(str(_require(body, 'text'))), req.app.host.cookie_file)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    req.app.settings.update({'use_cookies': True})
    return {'count': count, 'loggedIn': logged_in}


@route('POST', '/api/cookies/clear')
def _cookies_clear(req, query, body):
    if req.is_remote:
        raise ApiError(403, 'Only available on this device')
    cookies.clear(req.app.host.cookie_file)
    req.app.settings.update({'use_cookies': False})
    return {'ok': True}

# endregion


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup that can stall for seconds on Windows
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(app, bind, port):
    handler = type('BoundHandler', (Handler,), {'app': app})
    try:
        return _Server((bind, port), handler)
    except OSError:
        if port == 0:
            raise
        log.warning('Port %s is busy, using a random port', port)
        return _Server((bind, 0), handler)
