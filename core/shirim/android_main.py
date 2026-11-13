"""Entry points called from the Android app through Chaquopy."""

import json
import logging
import os
import types

from . import android_jsc, cookies, releases
from .app import App
from .content_filter import ContentFilter
from .db import Database
from .host import Host
from .settings import Settings

_app = None
# Fixed ports keep each app's page origin stable (player queue and other browser storage survive restarts); the
# filtered edition has its own, so both apps can run on one phone. Falls back to a random port when taken.
_PORT = 38765
_FILTERED_PORT = 38766


def _data_dir(files_dir):
    return os.path.join(files_dir, 'shirim')


def _folder_choices(music_dir):
    """Public folders a phone app may save audio files in, starting with the default Music/Shirim."""
    storage = os.path.dirname(os.path.dirname(music_dir))
    return [
        {'label': 'מוזיקה ← Shirim', 'path': music_dir},
        {'label': 'מוזיקה', 'path': os.path.join(storage, 'Music')},
        {'label': 'הורדות ← Shirim', 'path': os.path.join(storage, 'Download', 'Shirim')},
    ]


def start(files_dir, music_dir, web_dir, js_bridge, media_scanner, media_library, device_name='',
          audio_converter=None, app_installer=None, version_code=0, force_filter=False):
    """Start the server once per process and return its port."""
    global _app
    if _app is not None:
        return _app.server.server_address[1]
    logging.basicConfig(level=logging.INFO)
    android_jsc.set_bridge(js_bridge)
    host = Host(
        platform='android',
        data_dir=_data_dir(files_dir),
        default_music_dir=music_dir,
        web_dir=web_dir,
        ffmpeg_dir='',
        js_runtimes={},  # the WebView solver above replaces an external runtime
        on_file_saved=media_scanner.scan,
        local_music_provider=lambda: json.loads(str(media_library.listMusic())),
        device_name=str(device_name or 'טלפון'),
        audio_converter=audio_converter,
        opus_conversion=bool(audio_converter is not None and audio_converter.supportsOpus()),
        folder_choices=_folder_choices(music_dir),
        app_version_code=int(version_code or 0),
        app_installer=app_installer,
        filter_forced=bool(force_filter),
    )
    _app = App(host)
    return _app.start(port=_FILTERED_PORT if force_filter else _PORT, bind='127.0.0.1')


def port():
    """The running server's port, or 0 when the app hasn't started it yet."""
    return _app.server.server_address[1] if _app is not None else 0


def rescan():
    """Look for music again, e.g. after the user granted access to the phone's audio files."""
    if _app is not None:
        _app.scanner.start()


def check_releases(files_dir, force_filter=False):
    """Run by Android's background scheduler, also while the app is closed.

    Returns the new releases of followed artists as JSON, for a notification.
    """
    if _app is not None:
        db, settings, content_filter = _app.db, _app.settings, _app.filter
    else:
        os.makedirs(_data_dir(files_dir), exist_ok=True)
        db = Database(os.path.join(_data_dir(files_dir), 'library.db'))
        settings = Settings(os.path.join(_data_dir(files_dir), 'settings.json'))
        content_filter = ContentFilter(db, settings, types.SimpleNamespace(filter_forced=bool(force_filter)))
    if not settings['notify_new_releases'] and not settings['auto_download_new_releases']:
        return '[]'
    found = releases.check(db)
    if content_filter.active:
        classified = content_filter.classify([r['artist'] for r in found], budget=15)
        found = [r for r in found if content_filter.allows_artist(r['artist'], classified)]
    if _app is not None and found:
        _app.releases.save_pending()
        _app.events.bump()
    if not settings['notify_new_releases']:
        return '[]'
    return json.dumps([{'artist': r['artist'], 'title': r['title'], 'browseId': r['browseId']} for r in found],
                      ensure_ascii=False)


def save_login_cookies(cookie_headers):
    """Store cookies read from Android's CookieManager.

    cookie_headers maps a domain ('.youtube.com', '.google.com') to a 'name=value; name2=value2' header.
    CookieManager doesn't expose flags or expiry, so cookies are saved as secure, domain-wide cookies.
    Returns True when the cookies contain a signed-in session.
    """
    items = []
    for domain, header in dict(cookie_headers).items():
        for part in str(header or '').split(';'):
            name, sep, value = part.strip().partition('=')
            if sep and name:
                items.append({'name': name, 'value': value, 'domain': domain, 'path': '/', 'secure': True})
    if not any(item['name'] in cookies.LOGIN_COOKIES for item in items):
        return False
    cookies.save(items, _app.host.cookie_file)
    _app.settings.update({'use_cookies': True})
    if _app.downloads.blocked:
        _app.downloads.resume()
    _app.events.bump()
    return True
