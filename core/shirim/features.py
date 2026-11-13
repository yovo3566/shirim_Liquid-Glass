"""HTTP routes for discovery, new releases, USB export, lyrics, storage, imports, the monthly recap, sync and
access from outside the home."""

import re
import threading
import time

from . import content_filter, discover, home, lyrics, recap, remote, storage, usb
from .server import _SENT, ApiError, _int, _library_file, _localize_artists, _require, _same_secret, route
from .sync import SyncError, discover as discover_computers


def _local_only(req):
    if req.is_remote:
        raise ApiError(403, 'Only available on this device')


@route('GET', '/api/discover')
def _discover(req, query, body):
    result = discover.build(req.app.db, refresh=query.get('refresh') == '1')
    _localize_artists(req.app, result['artists'])
    return result


# region new releases

@route('GET', '/api/releases')
def _releases(req, query, body):
    app = req.app
    return {
        'items': app.db.list_new_releases(),
        'unseen': app.db.unseen_release_count(),
        'follows': len(app.db.list_follows()),
        'lastCheck': app.releases.last_check,
        'checking': app.releases.checking,
    }


@route('POST', '/api/releases/seen')
def _releases_seen(req, query, body):
    req.app.db.mark_releases_seen()
    req.app.events.bump()
    return {'ok': True}


@route('POST', '/api/releases/check')
def _releases_check(req, query, body):
    return {'found': len(req.app.releases.run_once())}


@route('POST', '/api/follows/top')
def _follow_top(req, query, body):
    """Follow the artists you listen to most, so their new releases get announced."""
    app = req.app
    following = {f['browseId'] for f in app.db.list_follows()}
    added = 0
    for artist in home.favorite_artists(app.db, limit=max(1, min(_int(body.get('limit'), 20), 50))):
        if artist['browseId'] not in following:
            app.db.set_follow(artist['browseId'], artist['name'], artist['thumb'], True)
            added += 1
    if added:
        # Record their current releases right away, so only later ones count as new
        threading.Thread(target=app.releases.run_once, name='release-baseline', daemon=True).start()
    return {'added': added}

# endregion


@route('GET', '/api/recent')
def _recent(req, query, body):
    return req.app.db.recent_plays(max(1, min(_int(query.get('limit'), 50), 200)))


# region USB export

@route('GET', '/api/usb/drives')
def _usb_drives(req, query, body):
    available = req.app.host.platform == 'windows' and not req.is_remote
    return {
        'available': available,
        'drives': usb.removable_drives() if available else [],
        'canConvert': bool(req.app.host.ffmpeg_dir),
    }


@route('POST', '/api/usb/export')
def _usb_export(req, query, body):
    _local_only(req)
    video_ids = [str(i) for i in body.get('videoIds') or []]
    if not video_ids:
        raise ApiError(400, 'Nothing to copy')
    try:
        req.app.usb.start(
            str(_require(body, 'root')), str(body.get('folder') or 'Shirim'), video_ids,
            layout='artists' if body.get('layout') == 'artists' else 'numbered',
            convert=bool(body.get('convert', True)))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return req.app.usb.status


@route('GET', '/api/usb/status')
def _usb_status(req, query, body):
    return req.app.usb.status


@route('POST', '/api/usb/cancel')
def _usb_cancel(req, query, body):
    req.app.usb.cancel()
    return {'ok': True}

# endregion


# region lyrics

@route('GET', '/api/lyrics')
def _lyrics(req, query, body):
    video_id = str(_require(query, 'videoId'))
    if not re.fullmatch(r'[\w-]{11}|local-[0-9a-f]{16}', video_id):
        raise ApiError(400, 'Invalid song')
    return lyrics.get(req.app.db, video_id, query.get('title', ''), query.get('artist', ''),
                      _int(query.get('duration')), path=_library_file(req.app, video_id))

# endregion


# region storage

@route('GET', '/api/storage')
def _storage(req, query, body):
    days = max(7, min(_int(query.get('days'), req.app.settings['auto_clean_days']), 730))
    data = storage.summary(req.app, days)
    data['autoClean'] = req.app.cleaner.last_result
    return data


@route('POST', '/api/storage/remove')
def _storage_remove(req, query, body):
    _local_only(req)
    removed, freed = storage.remove_songs(req.app, body.get('videoIds') or [])
    req.app.events.bump()
    return {'removed': removed, 'freed': freed}


@route('POST', '/api/storage/clear-cache')
def _storage_clear_cache(req, query, body):
    _local_only(req)
    return {'freed': storage.clear_cache(req.app)}

# endregion


# region import

@route('POST', '/api/import')
def _import(req, query, body):
    try:
        job_id = req.app.importer.start(str(body.get('source') or ''), str(body.get('name') or '').strip()[:100])
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {'id': job_id}


@route('GET', '/api/import/status')
def _import_status(req, query, body):
    status = req.app.importer.status(str(_require(query, 'id')))
    if status is None:
        raise ApiError(404, 'Import not found')
    return status

# endregion


# region monthly recap

@route('GET', '/api/recap/months')
def _recap_months(req, query, body):
    return recap.months(req.app.db)


@route('GET', '/api/recap')
def _recap(req, query, body):
    months = recap.months(req.app.db)
    month = query.get('month') or (months[0]['month'] if months else '')
    if not re.fullmatch(r'\d{4}-\d{2}', month or ''):
        return {'month': '', 'months': months}
    data = recap.build(req.app.db, month, req.app.artists)
    _localize_artists(req.app, [a for a in data['topArtists'] if a.get('browseId')])
    data['months'] = months
    return data

# endregion


# region sync between devices

@route('GET', '/api/sync/hello')
def _sync_hello(req, query, body):
    return req.app.sync_server.hello()


@route('POST', '/api/sync/code')
def _sync_code(req, query, body):
    _local_only(req)
    app = req.app
    bind = req.server.server_address[0]
    code = app.sync_server.new_code()
    return {**code, 'listening': bind == '0.0.0.0', 'name': app.sync_server.hello()['name']}


@route('POST', '/api/sync/pair')
def _sync_pair(req, query, body):
    try:
        return req.app.sync_server.pair(str(_require(body, 'code')), str(body.get('device') or ''))
    except SyncError as e:
        raise ApiError(e.status or 400, str(e)) from None


@route('POST', '/api/sync/exchange')
def _sync_exchange(req, query, body):
    if not req.sync_device:
        raise ApiError(401, 'Not paired')
    return req.app.sync_server.exchange(req.sync_device, body)


@route('GET', '/api/sync/files')
def _sync_files(req, query, body):
    if not req.sync_device:
        raise ApiError(401, 'Not paired')
    return req.app.sync_server.files()


@route('GET', '/api/sync/app')
def _sync_app(req, query, body):
    if not req.sync_device:
        raise ApiError(401, 'Not paired')
    info = req.app.sync_server.app_update(query.get('channel', 'standard'))
    info.pop('path', None)
    return info


@route('GET', '/api/sync/app/apk')
def _sync_app_apk(req, query, body):
    if not req.sync_device:
        raise ApiError(401, 'Not paired')
    info = req.app.sync_server.app_update(query.get('channel', 'standard'))
    if not info:
        raise ApiError(404, 'No update available')
    req.send_file(info['path'], 'application/vnd.android.package-archive', download_name='shirim.apk')
    return _SENT


@route('POST', '/api/app/update')
def _app_update_install(req, query, body):
    _local_only(req)
    try:
        req.app.sync.install_update()
    except SyncError as e:
        raise ApiError(e.status or 502, str(e)) from None
    return {'ok': True}


@route('GET', '/api/charts')
def _charts(req, query, body):
    return home.charts(req.app.db)


@route('GET', '/api/sync/devices')
def _sync_devices(req, query, body):
    _local_only(req)
    return req.app.db.list_sync_devices()


@route('POST', '/api/sync/devices/remove')
def _sync_device_remove(req, query, body):
    _local_only(req)
    req.app.db.remove_sync_device(_int(_require(body, 'id')))
    return {'ok': True}


@route('GET', '/api/sync/status')
def _sync_status(req, query, body):
    _local_only(req)
    return req.app.sync.status()


@route('POST', '/api/sync/discover')
def _sync_discover(req, query, body):
    _local_only(req)
    return discover_computers(req.app.settings['device_id'])


@route('POST', '/api/sync/connect')
def _sync_connect(req, query, body):
    _local_only(req)
    try:
        return req.app.sync.connect(str(_require(body, 'url')), str(_require(body, 'code')))
    except SyncError as e:
        raise ApiError(400 if e.status in (0, 404) else e.status, str(e)) from None


@route('POST', '/api/sync/now')
def _sync_now(req, query, body):
    _local_only(req)
    if not req.app.sync.paired:
        raise ApiError(400, 'Not paired')
    req.app.sync.run_once()
    return req.app.sync.status()


@route('POST', '/api/sync/unpair')
def _sync_unpair(req, query, body):
    _local_only(req)
    req.app.sync.unpair()
    return {'ok': True}


@route('POST', '/api/sync/keep')
def _sync_keep(req, query, body):
    _local_only(req)
    playlists = body.get('playlists')
    req.app.sync.set_keep(liked=body.get('liked'), playlists=playlists if isinstance(playlists, list) else None)
    return req.app.sync.status()

# endregion


# region filtered mode

def _require_filter_code(req, body):
    """Loosening the filter needs the lock code when one is set, and is impossible in the filtered edition."""
    app = req.app
    if app.host.filter_forced:
        raise ApiError(403, 'The filtered edition cannot change the filter')
    code = app.settings['filter_pin']
    if code and not _same_secret(str(body.get('filterPin') or ''), code):
        time.sleep(1)
        raise ApiError(403, 'Wrong filter code')


@route('GET', '/api/filter')
def _filter(req, query, body):
    _local_only(req)
    app = req.app
    return {
        **app.filter.status(),
        'rules': app.db.list_filter_rules(),
        'unknown': app.db.unknown_artists(80),
    }


@route('POST', '/api/filter/rule')
def _filter_rule(req, query, body):
    _local_only(req)
    kind = body.get('kind')
    verdict = body.get('verdict')
    if kind not in ('artist', 'song') or verdict not in ('allow', 'block'):
        raise ApiError(400, 'Invalid rule')
    label = str(body.get('name') or body.get('label') or '')[:300]
    key = content_filter.artist_key(label) if kind == 'artist' else str(_require(body, 'key'))
    if not key:
        raise ApiError(400, 'Invalid rule')
    if verdict == 'allow':
        _require_filter_code(req, body)
    req.app.db.set_filter_rule(kind, key, verdict, label)
    return {'ok': True}


@route('POST', '/api/filter/rule/remove')
def _filter_rule_remove(req, query, body):
    _local_only(req)
    kind, key = str(_require(body, 'kind')), str(_require(body, 'key'))
    rule = req.app.db.filter_rule(kind, key)
    if rule and rule['verdict'] == 'block':
        _require_filter_code(req, body)  # removing a block shows the content again
    req.app.db.remove_filter_rule(kind, key)
    return {'ok': True}


@route('POST', '/api/filter/report')
def _filter_report(req, query, body):
    """The user heard a woman singing (or something unsuitable) in a song: hide it from now on."""
    track = body.get('track') if isinstance(body.get('track'), dict) else {}
    video_id = str(track.get('videoId') or '')
    if not re.fullmatch(r'[\w-]{11}|local-[0-9a-f]{16}', video_id):
        raise ApiError(400, 'Invalid song')
    label = f"{track.get('title') or ''} – {track.get('artist') or ''}".strip(' –')
    req.app.db.set_filter_rule('song', video_id, 'block', label)
    return {'performers': content_filter.performers(str(track.get('artist') or ''), str(track.get('title') or ''))}

# endregion


# region access from outside the home

@route('GET', '/api/remote')
def _remote(req, query, body):
    _local_only(req)
    port = req.server.server_address[1]
    listening = req.server.server_address[0] == '0.0.0.0'
    addresses = remote.tailscale_addresses() if req.app.host.platform != 'android' else []
    return {
        'listening': listening,
        'tailscaleInstalled': remote.tailscale_installed() if req.app.host.platform != 'android' else False,
        'tailscaleUrls': [f'http://{ip}:{port}' for ip in addresses] if listening else [],
        'pinSet': bool(req.app.settings['remote_pin']),
    }


@route('GET', '/api/qr')
def _qr(req, query, body):
    _local_only(req)
    text = str(_require(query, 'text'))[:500]
    try:
        svg = remote.qr_svg(text).encode('utf-8')
    except ImportError:
        raise ApiError(404, 'QR codes are not available') from None
    req.send_response(200)
    req.send_header('Content-Type', 'image/svg+xml')
    req.send_header('Content-Length', str(len(svg)))
    req.send_header('Cache-Control', 'no-store')
    req.end_headers()
    req.wfile.write(svg)
    return _SENT

# endregion
