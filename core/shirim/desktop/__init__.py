"""Windows desktop launcher: runs the Shirim server and shows the UI in a native window."""

import argparse
import ctypes
import email.utils
import logging
import logging.handlers
import os
import shutil
import sys
import threading
import webbrowser

APP_NAME = 'Shirim'
# The regular app and the filtered edition (--filtered) are separate apps: own data, port, window and taskbar
# identity, so they can be installed and even run side by side, like the two phone apps
EDITIONS = {
    False: {'data_folder': 'Shirim', 'title': 'שירים', 'port': 8765,
            'mutex': 'Local\\ShirimSingleInstance', 'app_id': None, 'icon': None},
    True: {'data_folder': 'Shirim-Filtered', 'title': 'שירים מסונן', 'port': 8766,
           'mutex': 'Local\\ShirimFilteredSingleInstance', 'app_id': 'Shirim.Filtered', 'icon': 'shirim-filtered.ico'},
}
LOGIN_URL = 'https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/'
# Where people usually keep music on Windows; scanned for songs to show in the library
MUSIC_FOLDERS = ('Music', 'Downloads', 'Desktop', 'Documents',
                 os.path.join('OneDrive', 'Music'), os.path.join('OneDrive', 'Desktop'), os.path.join('OneDrive', 'Documents'))


def _resource_dirs():
    """The web UI, bundled tools (FFmpeg, Deno), the phone apps offered to paired phones, and the app icons."""
    if getattr(sys, 'frozen', False):
        bundle = sys._MEIPASS
        return (os.path.join(bundle, 'web'), os.path.join(bundle, 'bin'), os.path.join(bundle, 'updates'), bundle)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, 'core'))
    return (os.path.join(root, 'web'), os.path.join(root, 'vendor', 'win'), os.path.join(root, 'build-updates'),
            os.path.join(root, 'desktop'))


WEB_DIR, BIN_DIR, UPDATES_DIR, ICONS_DIR = _resource_dirs()

# A windowed PyInstaller build has no console; some libraries still write to stdout
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

import webview  # noqa: E402

from shirim import cookies  # noqa: E402
from shirim.app import App  # noqa: E402
from shirim.host import Host  # noqa: E402

log = logging.getLogger('shirim.desktop')
_instance_mutex = None

# WebView2 flags. The environment variable replaces the arguments pywebview passes, so its own flag is repeated.
# A music app must play without a click first: autoplay to the next song, the keyboard's media keys, and the silent
# warm-up in web/js/player.js that lets songs load while the window is minimized.
BROWSER_ARGUMENTS = '--disable-features=ElasticOverscroll --autoplay-policy=no-user-gesture-required'


def _set_browser_arguments():
    existing = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '')
    if BROWSER_ARGUMENTS not in existing:
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = f'{existing} {BROWSER_ARGUMENTS}'.strip()


def _setup_logging(data_dir):
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(data_dir, 'shirim.log'), maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _already_running(mutex_name):
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(None, False, mutex_name)
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _set_app_id(app_id):
    """Own taskbar button and pin for this edition; must run before any window exists."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except (AttributeError, OSError):
        log.warning('Could not set the taskbar identity %s', app_id)


def _set_window_icon(icon_path):
    """Give this process's app windows another icon (the exe carries the regular app's icon)."""
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_uint]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    image_icon, load_from_file, wm_seticon = 1, 0x10, 0x80
    big = user32.LoadImageW(None, icon_path, image_icon, user32.GetSystemMetrics(11), user32.GetSystemMetrics(12),
                            load_from_file)
    small = user32.LoadImageW(None, icon_path, image_icon, user32.GetSystemMetrics(49), user32.GetSystemMetrics(50),
                              load_from_file)
    if not big:
        log.warning('Could not load the icon %s', icon_path)
        return
    own_pid = os.getpid()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def apply(hwnd, _param):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid and user32.IsWindowVisible(hwnd):
            user32.SendMessageW(hwnd, wm_seticon, ctypes.c_void_p(1), ctypes.c_void_p(big))
            user32.SendMessageW(hwnd, wm_seticon, ctypes.c_void_p(0), ctypes.c_void_p(small or big))
        return True

    user32.EnumWindows(apply, None)


def _js_runtimes():
    deno = os.path.join(BIN_DIR, 'deno.exe')
    if os.path.isfile(deno):
        return {'deno': {'path': deno}}
    if shutil.which('deno'):
        return {'deno': {}}
    if shutil.which('node'):
        return {'node': {}}
    return {}


def _music_roots():
    home = os.path.expanduser('~')
    return [path for path in (os.path.join(home, name) for name in MUSIC_FOLDERS) if os.path.isdir(path)]


def _cookie_dicts(jar):
    """Convert pywebview's SimpleCookie objects into plain dicts for cookies.save."""
    items = []
    for cookie in jar:
        for name, morsel in cookie.items():
            expires = 0
            if morsel['expires']:
                try:
                    expires = email.utils.parsedate_to_datetime(morsel['expires']).timestamp()
                except (TypeError, ValueError):
                    pass
            items.append({
                'name': name,
                'value': morsel.value,
                'domain': morsel['domain'],
                'path': morsel['path'] or '/',
                'expires': expires,
                'secure': bool(morsel['secure']),
                'httponly': bool(morsel['httponly']),
            })
    return items


class Desktop:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.login_window = None

    def _pick_folder(self, start):
        folder_dialog = getattr(getattr(webview, 'FileDialog', None), 'FOLDER', None) or webview.FOLDER_DIALOG
        result = self.window.create_file_dialog(folder_dialog, directory=start)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def open_folder(self, _body):
        path = self.app.settings['music_dir']
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def choose_folder(self, _body):
        path = self._pick_folder(self.app.settings['music_dir'])
        if not path:
            return {'music_dir': None}
        return {'music_dir': self.app.settings.update({'music_dir': path})['music_dir']}

    def add_music_folder(self, _body):
        path = self._pick_folder(os.path.expanduser('~'))
        if path:
            folders = [line for line in self.app.settings['extra_music_folders'].splitlines() if line.strip()]
            if path not in folders:
                folders.append(path)
                self.app.settings.update({'extra_music_folders': '\n'.join(folders)})
                self.app.scanner.start()
        return {'folders': self.app.scanner.roots()}

    def remove_music_folder(self, body):
        path = str(body.get('path') or '')
        folders = [line for line in self.app.settings['extra_music_folders'].splitlines() if line.strip() and line != path]
        self.app.settings.update({'extra_music_folders': '\n'.join(folders)})
        self.app.scanner.start()
        return {'folders': self.app.scanner.roots()}

    def open_url(self, body):
        """Open a link outside the app: sharing to WhatsApp, the Tailscale site, a song on YouTube Music."""
        url = str(body.get('url') or '')
        if not url.startswith(('https://', 'whatsapp://')):
            return {'ok': False}
        webbrowser.open(url)
        return {'ok': True}

    def login(self, _body):
        if self.login_window is not None:
            return {'ok': True}
        window = webview.create_window('התחברות ליוטיוב', LOGIN_URL, width=520, height=760)
        self.login_window = window
        window.events.loaded += lambda: self._capture_login(window)
        window.events.closed += lambda: setattr(self, 'login_window', None)
        return {'ok': True}

    def _capture_login(self, window):
        # Google redirects to youtube.com after a successful sign-in; that page carries the session cookies
        try:
            if not (window.get_current_url() or '').startswith('https://www.youtube.com'):
                return
            items = _cookie_dicts(window.get_cookies())
        except Exception:
            log.exception('Reading sign-in cookies failed')
            return
        if not any(item['name'] in cookies.LOGIN_COOKIES for item in items):
            return
        count, _ = cookies.save(items, self.app.host.cookie_file)
        log.info('Saved %d YouTube cookies from sign-in window', count)
        self.app.settings.update({'use_cookies': True})
        if self.app.downloads.blocked:
            self.app.downloads.resume()
        self.app.events.bump()
        threading.Timer(1.0, window.destroy).start()


def main():
    parser = argparse.ArgumentParser(description='Shirim music app')
    parser.add_argument('--server', action='store_true', help='run without a window (for development)')
    parser.add_argument('--port', type=int, help='default: 8765, or 8766 for the filtered edition')
    parser.add_argument('--data-dir', help='settings and library location')
    parser.add_argument('--filtered', action='store_true',
                        help='filtered edition: women singing, explicit songs and pictures stay hidden')
    args = parser.parse_args()
    edition = EDITIONS[args.filtered]

    data_dir = args.data_dir or os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
                                             edition['data_folder'])
    os.makedirs(data_dir, exist_ok=True)
    _setup_logging(data_dir)

    if not args.server and _already_running(edition['mutex']):
        ctypes.windll.user32.MessageBoxW(0, f"האפליקציה {edition['title']} כבר פתוחה.", edition['title'], 0x40)
        return
    if not args.server and edition['app_id']:
        _set_app_id(edition['app_id'])

    ffmpeg_dir = BIN_DIR if os.path.isfile(os.path.join(BIN_DIR, 'ffmpeg.exe')) else ''
    host = Host(
        platform='windows',
        data_dir=data_dir,
        default_music_dir=os.path.join(os.path.expanduser('~'), 'Music', 'Shirim'),
        web_dir=WEB_DIR,
        ffmpeg_dir=ffmpeg_dir,
        js_runtimes=_js_runtimes(),
        music_roots=_music_roots(),
        device_name=os.environ.get('COMPUTERNAME') or '',
        app_update_dir=UPDATES_DIR if os.path.isdir(UPDATES_DIR) else '',
        filter_forced=args.filtered,
    )
    app = App(host)
    desktop = Desktop(app)
    app.actions['open_folder'] = desktop.open_folder
    if not args.server:
        app.actions['choose_folder'] = desktop.choose_folder
        app.actions['add_music_folder'] = desktop.add_music_folder
        app.actions['login'] = desktop.login
    app.actions['remove_music_folder'] = desktop.remove_music_folder
    app.actions['open_url'] = desktop.open_url

    port = app.start(port=args.port or edition['port'])
    url = f'http://127.0.0.1:{port}/'
    log.info('%s running at %s (ffmpeg=%s, js=%s, filtered=%s)', APP_NAME, url, bool(ffmpeg_dir),
             list(host.js_runtimes), args.filtered)

    if args.server:
        print(f'Shirim running at {url}', flush=True)
        threading.Event().wait()
        return

    try:
        _set_browser_arguments()
        desktop.window = webview.create_window(
            edition['title'], url, width=1320, height=860, min_size=(400, 600), background_color='#050507')
        if edition['icon']:
            icon_path = os.path.join(ICONS_DIR, edition['icon'])
            desktop.window.events.shown += lambda: _set_window_icon(icon_path)
        webview.start(private_mode=False, storage_path=os.path.join(data_dir, 'webview'))
    except Exception:
        log.exception('Native window failed; opening the browser instead')
        webbrowser.open(url)
        threading.Event().wait()
    finally:
        # Daemon download threads and child processes must not keep the app alive
        os._exit(0)


if __name__ == '__main__':
    main()
