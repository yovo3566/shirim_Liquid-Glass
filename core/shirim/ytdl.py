"""yt-dlp options and error handling shared by downloads and instant playback."""

import contextlib
import logging
import os
import re
import shutil
import tempfile

log = logging.getLogger(__name__)

AUDIO_M4A = 'bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best'
# Data saver: YouTube's ~50-70 kbps streams, about half the size, falling back to the normal quality
AUDIO_SAVER = 'bestaudio[ext=m4a][abr<=72]/bestaudio[abr<=72]/' + AUDIO_M4A
BOT_CHECK_MARKERS = ('not a bot', 'Sign in to confirm')


def stream_format(settings):
    return AUDIO_SAVER if settings['audio_quality'] == 'saver' else AUDIO_M4A


class QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        log.debug('yt-dlp: %s', msg)

    def error(self, msg):
        log.debug('yt-dlp: %s', msg)


LOGGER = QuietLogger()


def clean_error(error):
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(error))
    text = re.sub(r'^ERROR:\s*', '', text)
    text = re.sub(r'^\[\w+\] [\w-]+: ', '', text)
    return text.split(' Use --cookies')[0].strip()[:300]


def is_bot_check(error):
    return any(marker in str(error) for marker in BOT_CHECK_MARKERS)


def base_options(settings, host):
    """Options every YoutubeDL instance gets. `settings` may be the Settings object or a snapshot dict."""
    opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'logger': LOGGER,
        'retries': 5,
        'fragment_retries': 10,
        'socket_timeout': 30,
        'cachedir': host.cache_dir,
        'js_runtimes': host.js_runtimes,
    }
    if settings['use_pot'] and host.pot_args:
        opts['extractor_args'] = host.pot_args
    return opts


@contextlib.contextmanager
def cookie_file(settings, host):
    """Yield a private copy of the cookies file, or None when signed-in downloads are off.

    yt-dlp writes cookies back when it finishes, so parallel instances must not share one file.
    """
    if not (settings['use_cookies'] and os.path.isfile(host.cookie_file)):
        yield None
        return
    fd, path = tempfile.mkstemp(suffix='.txt', dir=host.cache_dir)
    os.close(fd)
    try:
        shutil.copyfile(host.cookie_file, path)
        yield path
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
