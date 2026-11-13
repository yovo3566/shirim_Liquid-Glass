"""Copying songs to a USB stick for the car (Windows)."""

import ctypes
import logging
import os
import re
import shutil
import string
import subprocess
import threading
import time

from . import names, scanner, tagging

log = logging.getLogger(__name__)

_DRIVE_REMOVABLE = 2
_CREATE_NO_WINDOW = 0x08000000
# FAT32 is what car stereos read; it rejects these characters and very long names
_FAT_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_NAME = 90


def fat_name(text):
    text = _FAT_UNSAFE.sub('_', text).strip().rstrip('. ')
    return text[:_MAX_NAME].rstrip('. ') or 'untitled'


def removable_drives():
    if os.name != 'nt':
        return []
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f'{letter}:\\'
        if kernel32.GetDriveTypeW(root) != _DRIVE_REMOVABLE:
            continue
        label = ctypes.create_unicode_buffer(261)
        filesystem = ctypes.create_unicode_buffer(261)
        if not kernel32.GetVolumeInformationW(root, label, 261, None, None, None, filesystem, 261):
            continue  # a card reader slot with no card in it
        free, total = ctypes.c_ulonglong(0), ctypes.c_ulonglong(0)
        kernel32.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), ctypes.byref(free))
        drives.append({'root': root, 'label': label.value, 'filesystem': filesystem.value,
                       'free': free.value, 'total': total.value})
    return drives


class UsbExporter:
    def __init__(self, db, host, events):
        self.db = db
        self.host = host
        self.events = events
        self.status = {'running': False, 'done': 0, 'total': 0, 'copied': 0, 'skipped': 0, 'missing': 0,
                       'failed': 0, 'current': '', 'folder': '', 'error': '', 'finished_at': 0}
        self._cancel = False
        self._lock = threading.Lock()

    def start(self, root, folder, video_ids, layout='numbered', convert=True):
        if root not in {d['root'] for d in removable_drives()}:
            raise ValueError('The USB stick is no longer connected')
        with self._lock:
            if self.status['running']:
                raise ValueError('Copying is already in progress')
            self._cancel = False
            destination = os.path.join(root, fat_name(folder))
            self.status = {**self.status, 'running': True, 'done': 0, 'total': len(video_ids), 'copied': 0,
                           'skipped': 0, 'missing': 0, 'failed': 0, 'current': '', 'error': '', 'folder': destination}
        threading.Thread(target=self._run, args=(destination, video_ids, layout, convert),
                         name='usb-export', daemon=True).start()

    def cancel(self):
        self._cancel = True

    def _update(self, **changes):
        self.status.update(changes)
        self.events.bump()

    def _run(self, destination, video_ids, layout, convert):
        ffmpeg = os.path.join(self.host.ffmpeg_dir, 'ffmpeg.exe') if convert and self.host.ffmpeg_dir else ''
        try:
            tracks = [self.db.get_track(video_id) for video_id in video_ids]
            needed = sum(t['size'] for t in tracks if t and os.path.isfile(t['path']))
            free = shutil.disk_usage(os.path.splitdrive(destination)[0] + os.sep).free
            if needed > free:
                raise OSError(f'Not enough space on the USB stick: {needed / 1e6:.0f} MB needed, {free / 1e6:.0f} MB free')
            os.makedirs(destination, exist_ok=True)
            width = max(3, len(str(len(video_ids))))
            for index, track in enumerate(tracks):
                if self._cancel:
                    break
                if not track or not os.path.isfile(track['path']):
                    self._update(missing=self.status['missing'] + 1, done=index + 1)
                    continue
                self._update(current=track['title'])
                try:
                    key = 'copied' if self._copy(track, destination, index, width, layout, ffmpeg) else 'skipped'
                    self._update(**{key: self.status[key] + 1})
                except Exception as e:
                    log.warning('Copying %s to USB failed: %s', track['path'], e)
                    self._update(failed=self.status['failed'] + 1)
                self._update(done=index + 1)
        except Exception as e:
            log.warning('USB export failed: %s', e)
            self._update(error=str(e)[:300])
        finally:
            self._update(running=False, current='', finished_at=time.time())

    def _copy(self, track, destination, index, width, layout, ffmpeg):
        """Copy one song; returns False when an identical copy is already there."""
        ext = os.path.splitext(track['path'])[1].lower()
        lead = track['lead_artist'] or names.lead_artist(track['artist'])
        if layout == 'artists' and lead:
            folder = os.path.join(destination, fat_name(lead))
            base = fat_name(track['title'])
        else:
            # Car stereos play files in name order, so numbers keep the playlist order
            label = f"{track['artist']} - {track['title']}" if track['artist'] else track['title']
            folder = destination
            base = fat_name(f'{index + 1:0{width}d} - {label}')
        convert = bool(ffmpeg) and ext != '.mp3'
        final_ext = '.mp3' if convert else ext
        target = os.path.join(folder, base + final_ext)
        if os.path.isfile(target) and (convert or os.path.getsize(target) == track['size']):
            return False

        os.makedirs(folder, exist_ok=True)
        # The temp name keeps the real extension so the tagger recognises the format
        temp = os.path.join(folder, f'.{base}.part{final_ext}')
        try:
            if convert:
                subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', track['path'], '-vn',
                                '-codec:a', 'libmp3lame', '-b:a', '192k', temp],
                               check=True, timeout=600, creationflags=_CREATE_NO_WINDOW)
                cover = scanner.extract_cover(track['path']) or tagging.fetch_cover(track['thumb'])
                tagging.write_tags(temp, track['title'], track['artist'], track['album'], cover, album_artist=lead)
            else:
                shutil.copyfile(track['path'], temp)
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.remove(temp)
        return True
