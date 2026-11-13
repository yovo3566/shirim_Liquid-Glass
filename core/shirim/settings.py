"""Persistent user settings stored as JSON."""

import json
import os
import threading
import uuid

DEFAULTS = {
    'music_dir': '',
    'audio_format': 'm4a',  # m4a (original AAC, no re-encode) | mp3 | opus
    'audio_quality': 'high',  # high | saver (smaller files at a lower bitrate)
    'mp3_bitrate': 320,
    'concurrency': 4,
    'embed_cover': True,
    'skip_existing': True,
    'folder_per_artist': True,
    'use_cookies': False,
    'use_pot': True,
    'remote_access': True,
    'remote_pin': '',
    'autoplay': True,  # keep playing similar songs when the queue ends
    'auto_download_likes': False,
    'scan_local_music': True,  # show music already on the device in the library
    'extra_music_folders': '',  # one folder per line, scanned in addition to the default music folders
    'notify_new_releases': True,  # announce new songs of followed artists
    'auto_download_new_releases': False,
    # sound
    'eq_enabled': False,
    'eq_preset': 'flat',
    'eq_bands': '0,0,0,0,0',  # dB at 60 Hz, 230 Hz, 910 Hz, 3.6 kHz, 14 kHz
    'normalize_volume': True,  # even out loud and quiet songs
    'crossfade': 0,  # seconds of overlap between songs
    # storage
    'auto_clean': False,  # remove saved songs nobody listened to for a long time
    'auto_clean_days': 90,
    # filtered mode: hides women singing, explicit songs and all pictures
    'filter_enabled': False,
    'filter_unknown': 'hide',  # hide | show performers that couldn't be identified
    'filter_pin': '',  # when set, loosening the filter needs this code
    # sync between a phone and the computer
    'device_id': '',
    'sync_peer': '',  # the computer's address, e.g. http://192.168.1.20:8765
    'sync_peer_id': '',
    'sync_peer_name': '',
    'sync_token': '',
    'sync_last': 0,
    'sync_plays_cursor': 0,
    'sync_keep_liked': False,  # keep liked songs on this device, copied from the computer when possible
    'sync_keep_playlists': '',  # playlist uids to keep on this device, comma separated
}

# Never sent to other devices on the network, nor changeable by them
SECRET_SETTINGS = ('sync_token', 'remote_pin', 'filter_pin')

_LIMITS = {
    'concurrency': (1, 16),
    'mp3_bitrate': (96, 320),
    'crossfade': (0, 12),
    'auto_clean_days': (14, 730),
    'sync_last': (0, 2 ** 40),
    'sync_plays_cursor': (0, 2 ** 62),
}

_CHOICES = {
    'audio_format': ('m4a', 'mp3', 'opus'),
    'audio_quality': ('high', 'saver'),
    'eq_preset': ('flat', 'bass', 'vocal', 'treble', 'car', 'custom'),
    'filter_unknown': ('hide', 'show'),
}


def _coerce(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if isinstance(default, int):
        lo, hi = _LIMITS.get(key, (None, None))
        value = int(float(value))
        return value if lo is None else max(lo, min(hi, value))
    value = str(value)
    if key in _CHOICES and value not in _CHOICES[key]:
        raise ValueError(f'Invalid value for {key}: {value!r}')
    if key == 'eq_bands':
        gains = [max(-12.0, min(12.0, float(part))) for part in value.split(',')[:5]]
        value = ','.join(f'{g:g}' for g in gains + [0.0] * (5 - len(gains)))
    return value


class Settings:
    def __init__(self, path, defaults=None):
        self._path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self._data.update(defaults or {})
        try:
            # utf-8-sig: tolerate a BOM from editors like Notepad
            with open(path, encoding='utf-8-sig') as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        for key, value in saved.items():
            if key in DEFAULTS:
                try:
                    self._data[key] = _coerce(key, value)
                except (TypeError, ValueError):
                    pass
        if not self._data['device_id']:
            self.update({'device_id': uuid.uuid4().hex})

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def to_dict(self):
        with self._lock:
            return dict(self._data)

    def update(self, changes):
        clean = {key: _coerce(key, value) for key, value in changes.items() if key in DEFAULTS}
        with self._lock:
            self._data.update(clean)
            tmp = self._path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
            return dict(self._data)
