"""Sync between a phone and the computer on the same network.

The computer side (SyncServer) hands out one-time pairing codes and answers sync requests from paired devices.
The phone side (SyncClient) finds the computer, keeps likes, playlists, followed artists and listening history
identical on both, copies songs from the computer instead of downloading them again, and plays songs that
are only on the computer while both are home.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import __version__

log = logging.getLogger(__name__)

TOKEN_HEADER = 'X-Shirim-Sync'
DEFAULT_PORT = 8765
FILTERED_PORT = 8766  # the computer's filtered edition, which can run next to the regular app
DISCOVERY_PORTS = (DEFAULT_PORT, FILTERED_PORT)
_CODE_TTL = 10 * 60
_MAX_CODE_ATTEMPTS = 5
_SYNC_INTERVAL = 5 * 60
_CHANGE_DELAY = 15
_FILES_TTL = 5 * 60
_REACHABLE_TTL = 10 * 60
_CHUNK = 256 * 1024
_RELAYED_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges')

# Requests to devices on the home network must not go through a system proxy
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SyncError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _request(method, url, token='', body=None, timeout=20):
    headers = {'X-Shirim': '1'}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers[TOKEN_HEADER] = token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e).get('error') or str(e)
        except (ValueError, AttributeError):
            message = str(e)
        raise SyncError(message, e.code) from None
    except (OSError, ValueError) as e:
        raise SyncError(f'Computer unreachable: {e}') from None


def local_ipv4s():
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            addresses.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addresses if a.startswith(('192.168.', '10.')) or
                  (a.startswith('172.') and 16 <= int(a.split('.')[1]) <= 31))


def discover(own_id='', ports=DISCOVERY_PORTS):
    """Shirim computers on the home network: [{url, id, name, filtered}] (both editions of the computer app)."""
    subnets = {ip.rsplit('.', 1)[0] for ip in local_ipv4s()}
    own = set(local_ipv4s())
    hosts = [f'{subnet}.{i}' for subnet in sorted(subnets) for i in range(1, 255) if f'{subnet}.{i}' not in own]
    targets = [(host, port) for host in hosts for port in ports]

    def probe(target):
        host, port = target
        url = f'http://{host}:{port}'
        try:
            hello = _request('GET', f'{url}/api/sync/hello', timeout=1.2)
        except SyncError:
            return None
        if hello.get('app') != 'shirim' or hello.get('id') == own_id:
            return None
        return {'url': url, 'id': hello.get('id') or '', 'name': hello.get('name') or host,
                'platform': hello.get('platform') or '', 'filtered': bool(hello.get('filtered'))}

    with ThreadPoolExecutor(max_workers=96, thread_name_prefix='discover') as pool:
        found = [result for result in pool.map(probe, targets) if result]
    # A computer can be reachable on two addresses (Wi-Fi and cable); list it once
    unique = {}
    for item in found:
        unique.setdefault(item['id'] or item['url'], item)
    return list(unique.values())


# region computer side

class SyncServer:
    def __init__(self, app):
        self.app = app
        self._code = None
        self._lock = threading.Lock()

    def hello(self):
        host = self.app.host
        return {'app': 'shirim', 'id': self.app.settings['device_id'], 'name': host.device_name or socket.gethostname(),
                'platform': host.platform, 'version': __version__, 'filtered': bool(host.filter_forced)}

    def new_code(self):
        with self._lock:
            self._code = {'code': f'{secrets.randbelow(1_000_000):06d}', 'expires': time.time() + _CODE_TTL, 'attempts': 0}
            return dict(self._code)

    def pair(self, code, device_name):
        with self._lock:
            current = self._code
            if not current or current['expires'] < time.time():
                raise SyncError('No pairing code is active', 410)
            if not hmac.compare_digest(str(code).strip(), current['code']):
                current['attempts'] += 1
                if current['attempts'] >= _MAX_CODE_ATTEMPTS:
                    self._code = None
                raise SyncError('Wrong code', 401)
            self._code = None
        token = secrets.token_urlsafe(32)
        self.app.db.add_sync_device(hash_token(token), str(device_name or 'טלפון')[:80])
        self.app.events.bump()
        return {'token': token, **self.hello()}

    def device_for(self, token):
        if not token:
            return None
        device = self.app.db.sync_device(hash_token(token))
        if device:
            self.app.db.touch_sync_device(device['id'])
        return device

    def exchange(self, device, payload):
        db = self.app.db
        cursor = int(payload.get('playsCursor') or 0)
        before = db.max_play_id()
        changes = db.sync_merge(payload)
        # Plays that just arrived from this device don't need to go back to it
        state = db.sync_state(plays_after_id=max(cursor, 0))
        state['plays'] = [p for p in state['plays'] if p['id'] <= before]
        state['playsCursor'] = db.max_play_id()
        state['changes'] = changes
        if changes:
            self.app.sync.changed_remotely()
        log.info('Synced with %s: %d changes here', device['name'], changes)
        return state

    def app_update(self, channel='standard'):
        """The newest phone app, bundled with this computer's app: {versionCode, versionName, size} or {}.

        channel: 'standard', or 'filtered' for the filtered edition (a separate app, updated with its own APK).
        """
        folder = self.app.host.app_update_dir
        apk_name = 'shirim-filtered.apk' if channel == 'filtered' else 'shirim.apk'
        apk = os.path.join(folder, apk_name) if folder else ''
        if not apk or not os.path.isfile(apk):
            return {}
        try:
            with open(os.path.join(folder, 'android.json'), encoding='utf-8') as f:
                info = json.load(f)
        except (OSError, ValueError):
            return {}
        return {'versionCode': int(info.get('versionCode') or 0), 'versionName': str(info.get('versionName') or ''),
                'size': os.path.getsize(apk), 'path': apk}

    def files(self):
        tracks = {}
        for track in self.app.db.list_tracks():
            if not os.path.isfile(track['path']):
                continue
            tracks[track['video_id']] = {
                'title': track['title'], 'artist': track['artist'], 'album': track['album'],
                'duration': track['duration'], 'thumb': track['thumb'], 'size': track['size'],
                'ext': os.path.splitext(track['path'])[1].lower(), 'lead_artist': track['lead_artist'],
            }
        return {'tracks': tracks}

# endregion


# region phone side

class SyncClient:
    def __init__(self, app):
        self.app = app
        self.version = 0  # grows whenever a sync changed this device's likes, playlists or follows
        self.update = None  # a newer version of this phone app waiting on the computer
        self.installing = False
        self.syncing = False
        self.error = ''
        self.last_result = None
        self._wake = threading.Event()
        self._due = 0
        self._files = {}
        self._files_at = 0
        self._reached_at = 0
        self._queued = set()
        self._lock = threading.Lock()

    @property
    def paired(self):
        return bool(self.app.settings['sync_token'] and self.app.settings['sync_peer'])

    def status(self):
        s = self.app.settings
        return {
            'paired': self.paired,
            'peerName': s['sync_peer_name'],
            'peerUrl': s['sync_peer'],
            'lastSync': s['sync_last'],
            'syncing': self.syncing,
            'error': self.error,
            'reachable': self._reachable(),
            'keepLiked': s['sync_keep_liked'],
            'keepPlaylists': [uid for uid in s['sync_keep_playlists'].split(',') if uid],
            'lastResult': self.last_result,
            'version': self.version,
            'update': self.update,
        }

    def start(self):
        threading.Thread(target=self._loop, name='sync', daemon=True).start()

    def touch(self, delay=_CHANGE_DELAY):
        """Something changed here; sync soon."""
        if self.paired:
            self._due = min(self._due or float('inf'), time.time() + delay)
            self._wake.set()

    def changed_remotely(self):
        self.version += 1
        self.app.events.bump()

    def _loop(self):
        self._due = time.time() + 8
        while True:
            wait = max(0.0, self._due - time.time()) if self._due else _SYNC_INTERVAL
            self._wake.wait(timeout=min(wait, _SYNC_INTERVAL))
            self._wake.clear()
            if self._due and time.time() < self._due:
                continue
            self._due = 0
            if self.paired:
                try:
                    self.run_once()
                except Exception:
                    log.exception('Sync failed')
            self._due = self._due or time.time() + _SYNC_INTERVAL

    # region pairing

    def connect(self, url, code):
        url = url.rstrip('/')
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'http' or not parsed.hostname:
            raise SyncError('Invalid computer address', 400)
        result = _request('POST', f'{url}/api/sync/pair', body={'code': code, 'device': self.app.host.device_name})
        self.app.settings.update({
            'sync_peer': url, 'sync_peer_id': result.get('id') or '', 'sync_peer_name': result.get('name') or '',
            'sync_token': result['token'], 'sync_last': 0, 'sync_plays_cursor': 0,
        })
        self.error = ''
        self._files, self._files_at, self._queued = {}, 0, set()
        self.touch(0)
        return self.status()

    def unpair(self):
        self.app.settings.update({'sync_peer': '', 'sync_peer_id': '', 'sync_peer_name': '', 'sync_token': '',
                                  'sync_last': 0, 'sync_plays_cursor': 0})
        self._files, self._files_at, self._reached_at = {}, 0, 0
        self.error = ''
        self.app.events.bump()

    def set_keep(self, liked=None, playlists=None):
        changes = {}
        if liked is not None:
            changes['sync_keep_liked'] = bool(liked)
        if playlists is not None:
            changes['sync_keep_playlists'] = ','.join(str(uid) for uid in playlists if str(uid).isalnum())
        self.app.settings.update(changes)
        self._queued = set()
        self.touch(1)

    # endregion

    # region syncing

    def _peer(self):
        """The computer's current address; finds it again on the network when its address changed."""
        s = self.app.settings
        try:
            hello = _request('GET', f"{s['sync_peer']}/api/sync/hello", timeout=4)
            if not s['sync_peer_id'] or hello.get('id') == s['sync_peer_id']:
                return s['sync_peer']
        except SyncError:
            pass
        port = urllib.parse.urlparse(s['sync_peer']).port or DEFAULT_PORT
        for computer in discover(s['device_id'], (port,)):
            if computer['id'] == s['sync_peer_id']:
                self.app.settings.update({'sync_peer': computer['url'], 'sync_peer_name': computer['name']})
                return computer['url']
        raise SyncError('The computer is off, the app on it is closed, or it is on a different network')

    def run_once(self):
        # A sync already under way (e.g. the automatic one) is waited for rather than skipped
        with self._lock:
            if not self.paired:
                return None
            return self._sync()

    def _sync(self):
        self.syncing = True
        self.app.events.bump()
        db, settings = self.app.db, self.app.settings
        try:
            peer = self._peer()
            state = db.sync_state(unsynced_plays=True)
            sent_plays = [p['id'] for p in state['plays']]
            state['playsCursor'] = settings['sync_plays_cursor']
            reply = _request('POST', f'{peer}/api/sync/exchange', settings['sync_token'], state, timeout=60)
            changes = db.sync_merge(reply)
            db.mark_plays_synced(sent_plays)
            settings.update({'sync_last': int(time.time()), 'sync_plays_cursor': int(reply.get('playsCursor') or 0)})
            self._reached_at = time.time()
            self.error = ''
            self.last_result = {'here': changes, 'there': int(reply.get('changes') or 0), 'plays': len(sent_plays)}
            if changes:
                self.changed_remotely()
            self._refresh_files(peer, force=True)
            self._queue_kept_songs()
            self._check_app_update(peer)
            return self.last_result
        except SyncError as e:
            if e.status == 401:
                # The computer removed this phone from its paired devices
                self.app.settings.update({'sync_token': ''})
                self.error = 'unpaired'
            else:
                self.error = str(e)
            log.info('Sync failed: %s', e)
            return None
        finally:
            self.syncing = False
            self.app.events.bump()

    def _refresh_files(self, peer=None, force=False):
        if not force and time.time() - self._files_at < _FILES_TTL:
            return self._files
        peer = peer or self.app.settings['sync_peer']
        try:
            self._files = _request('GET', f'{peer}/api/sync/files', self.app.settings['sync_token'], timeout=30)['tracks']
            self._files_at = self._reached_at = time.time()
        except SyncError as e:
            log.info('Listing the computer\'s songs failed: %s', e)
        return self._files

    # region app updates from the computer

    def _check_app_update(self, peer):
        """Every update to the computer's app carries the matching phone app; offer it when it's newer."""
        installed = self.app.host.app_version_code
        if not installed or self.app.host.app_installer is None:
            return
        try:
            info = _request('GET', f'{peer}/api/sync/app?channel={self.app.host.app_update_channel}',
                            self.app.settings['sync_token'], timeout=10)
        except SyncError as e:
            log.info('Checking for an app update failed: %s', e)
            return
        newer = int(info.get('versionCode') or 0) > installed
        update = {'versionName': info.get('versionName') or '', 'size': info.get('size') or 0} if newer else None
        if update != self.update:
            self.update = update
            self.app.events.bump()

    def install_update(self):
        """Copy the newer phone app from the computer and open Android's installer for it."""
        if not self.update:
            raise SyncError('No update available', 404)
        settings = self.app.settings
        folder = os.path.join(self.app.host.cache_dir, 'updates')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'shirim.apk')
        request = urllib.request.Request(f"{settings['sync_peer']}/api/sync/app/apk?channel={self.app.host.app_update_channel}",
                                         headers={TOKEN_HEADER: settings['sync_token']})
        self.installing = True
        self.app.events.bump()
        try:
            with _opener.open(request, timeout=30) as response, open(path + '.part', 'wb') as out:
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
            if self.update.get('size') and os.path.getsize(path + '.part') != self.update['size']:
                raise SyncError('The update was not copied completely')
            os.replace(path + '.part', path)
        except OSError as e:
            raise SyncError(f'Computer unreachable: {e}') from None
        finally:
            self.installing = False
            self.app.events.bump()
        self.app.host.app_installer.install(path)

    # endregion

    def _queue_kept_songs(self):
        """Save the songs chosen to stay on this phone; the download queue copies them from the computer."""
        settings, db = self.app.settings, self.app.db
        wanted = []
        if settings['sync_keep_liked']:
            wanted += db.list_likes()
        for uid in filter(None, settings['sync_keep_playlists'].split(',')):
            playlist_id = db.playlist_id_for_uid(uid)
            if playlist_id:
                wanted += db.get_playlist(playlist_id)['tracks']
        library = {t['video_id'] for t in db.list_tracks() if os.path.isfile(t['path'])}
        fresh = []
        for track in wanted:
            video_id = track['videoId']
            if video_id in library or video_id in self._queued:
                continue
            # Songs found only on the computer can only be copied from it
            if video_id.startswith('local-') and video_id not in self._files:
                continue
            self._queued.add(video_id)
            fresh.append(track)
        if fresh:
            self.app.downloads.add(fresh)

    # endregion

    # region songs on the computer

    def _reachable(self):
        return self.paired and time.time() - self._reached_at < _REACHABLE_TTL

    def has(self, video_id):
        return self._reachable() and video_id in self._refresh_files()

    def info(self, video_id):
        return self._files.get(video_id)

    def fetch(self, video_id, stem, progress=None):
        """Copy a song from the computer to stem + its extension. Returns the file path."""
        settings = self.app.settings
        info = self._files[video_id]
        path = stem + info['ext']
        part = path + '.part'
        request = urllib.request.Request(f"{settings['sync_peer']}/media/{urllib.parse.quote(video_id)}",
                                         headers={TOKEN_HEADER: settings['sync_token']})
        try:
            with _opener.open(request, timeout=30) as response, open(part, 'wb') as out:
                total = int(response.headers.get('Content-Length') or info['size'] or 0)
                done = 0
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            os.replace(part, path)
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        self._reached_at = time.time()
        return path

    def relay(self, handler, video_id):
        """Play a song that's on the computer. Returns False (nothing sent) when the computer can't be reached."""
        settings = self.app.settings
        headers = {TOKEN_HEADER: settings['sync_token']}
        if handler.headers.get('Range'):
            headers['Range'] = handler.headers['Range']
        request = urllib.request.Request(f"{settings['sync_peer']}/media/{urllib.parse.quote(video_id)}", headers=headers)
        try:
            response = _opener.open(request, timeout=6)
        except urllib.error.HTTPError as e:
            if e.code == 416:
                handler.send_response(416)
                handler.send_header('Content-Length', '0')
                handler.end_headers()
                return True
            return False
        except OSError:
            self._reached_at = 0
            return False
        with response:
            handler.send_response(response.status)
            for name in _RELAYED_HEADERS:
                if response.headers.get(name):
                    handler.send_header(name, response.headers[name])
            handler.send_header('Cache-Control', 'no-store')
            handler.end_headers()
            while chunk := response.read(_CHUNK):
                handler.wfile.write(chunk)
        return True

    # endregion

# endregion
