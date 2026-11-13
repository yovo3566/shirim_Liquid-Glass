"""Parallel download queue built on yt-dlp."""

import glob
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request

import yt_dlp
from yt_dlp.plugins import load_all_plugins
from yt_dlp.utils import DownloadCancelled

from . import names, tagging, ytdl
from .ytdl import clean_error

log = logging.getLogger(__name__)

# YoutubeDL instances created in parallel worker threads would otherwise race to import plugins
load_all_plugins()

FORMATS = {
    'm4a': ytdl.AUDIO_M4A,
    'mp3': 'bestaudio/best',
    'opus': 'bestaudio[acodec=opus]/bestaudio/best',
}

FORMATS_SAVER = {
    'm4a': ytdl.AUDIO_SAVER,
    'mp3': 'bestaudio[abr<=72]/bestaudio/best',
    'opus': 'bestaudio[acodec=opus][abr<=72]/' + FORMATS['opus'],
}

_RETRYABLE_MARKERS = ('HTTP Error 403', 'timed out', 'Connection reset', 'Remote end closed', 'IncompleteRead')
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_VIDEO_ID = re.compile(r'(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})')
_YOUTUBE_ID = re.compile(r'[\w-]{11}')


class _Canceled(DownloadCancelled):
    msg = 'Canceled'


class BotCheckError(Exception):
    """YouTube refused to serve the video until the client proves it is not a bot."""


def safe_name(text, max_len=120):
    text = _UNSAFE_CHARS.sub('_', text).strip().rstrip('.')
    return text[:max_len].rstrip() or 'untitled'


class DownloadManager:
    PROGRESS_INTERVAL = 0.25

    def __init__(self, db, settings, host, events, artists):
        self.db = db
        self.settings = settings
        self.host = host
        self.events = events
        self.artists = artists
        self.paused = False
        self.blocked = ''  # set when YouTube demands a bot check; the queue pauses until resumed
        self._cond = threading.Condition()
        self._workers = []
        self._progress = {}
        self._progress_time = {}
        self._cancel = set()
        self._reserved = set()
        self._active_songs = {}
        self._list_version = 0
        # The paired computer (sync.SyncClient); songs it has are copied over the home network instead
        self.peer = None
        # Filtered mode (content_filter.ContentFilter): hidden songs aren't saved, and files get no cover picture
        self.content_filter = None

    # region public API

    def start(self):
        self._ensure_workers()

    def add(self, tracks):
        if self.content_filter is not None and self.content_filter.active:
            tracks = self.content_filter.filter_tracks(list(tracks))
        added = self.db.add_jobs(tracks)
        if added:
            self._changed(list_changed=True)
            self._kick()
        return len(added)

    def pause(self):
        self.paused = True
        self._changed()

    def resume(self):
        self.paused = False
        self.blocked = ''
        self._changed()
        self._kick()

    def cancel(self, ids=None):
        with self._cond:
            running = set(self._progress) if ids is None else set(ids) & set(self._progress)
            self._cancel.update(running)
            self._cond.notify_all()
        self.db.cancel_queued(ids)
        self._changed(list_changed=True)

    def retry(self, ids=None):
        count = self.db.retry(ids)
        if count:
            self._changed(list_changed=True)
            self._kick()
        return count

    def clear_finished(self):
        self.db.clear_finished()
        self._changed(list_changed=True)

    def settings_changed(self):
        self._ensure_workers()

    def snapshot(self):
        with self._cond:
            progress = {job_id: dict(p) for job_id, p in self._progress.items()}
        return {
            'counts': self.db.job_counts(),
            'paused': self.paused,
            'blocked': self.blocked,
            'progress': progress,
            'listVersion': self._list_version,
        }

    # endregion

    # region workers

    def _changed(self, list_changed=False):
        if list_changed:
            self._list_version += 1
        self.events.bump()

    def _kick(self):
        with self._cond:
            self._cond.notify_all()

    def _ensure_workers(self):
        with self._cond:
            self._workers = [t for t in self._workers if t.is_alive()]
            for _ in range(self.settings['concurrency'] - len(self._workers)):
                worker = threading.Thread(target=self._worker, name='download-worker', daemon=True)
                self._workers.append(worker)
                worker.start()
            self._cond.notify_all()

    def _worker(self):
        me = threading.current_thread()
        while True:
            with self._cond:
                while True:
                    # Lowering the concurrency setting retires workers once they are idle
                    if len(self._workers) > self.settings['concurrency']:
                        self._workers.remove(me)
                        return
                    job = None if self.paused else self.db.claim_next_job()
                    if job:
                        self._progress[job['id']] = {'stage': 'starting', 'percent': 0, 'speed': 0, 'eta': 0}
                        break
                    self._cond.wait(timeout=10)
            self._changed(list_changed=True)
            self._run(job)

    def _update(self, job_id, stage, percent, speed=None, eta=None):
        now = time.monotonic()
        with self._cond:
            progress = self._progress.get(job_id)
            if progress is None:
                return
            if progress['stage'] == stage and now - self._progress_time.get(job_id, 0) < self.PROGRESS_INTERVAL:
                return
            self._progress_time[job_id] = now
            progress.update(stage=stage, percent=round(percent, 1), speed=int(speed or 0), eta=int(eta or 0))
        self.events.bump()

    def _run(self, job):
        job_id = job['id']
        status, error = 'done', ''
        try:
            lead, credit = self._credits(job)
            self._claim_song(job_id, (lead, names.title_key(job['title']), job['duration']))
            if self.settings['skip_existing'] and self._in_library(job, lead):
                status = 'skipped'
            else:
                self.db.add_track(self._copy_from_peer(job, lead, credit) or self._download_with_retry(job, lead, credit))
        except _Canceled:
            status = 'canceled'
        except BotCheckError as e:
            # Put the job back and stop the queue: hammering YouTube only prolongs the block
            self.db.requeue_job(job_id)
            self.paused = True
            self.blocked = str(e)
            status = None
        except Exception as e:
            log.warning('Download of %s failed: %s', job['video_id'], e)
            status, error = 'error', clean_error(e)
        finally:
            with self._cond:
                self._progress.pop(job_id, None)
                self._progress_time.pop(job_id, None)
                self._cancel.discard(job_id)
                self._active_songs.pop(job_id, None)
                self._cond.notify_all()
        if status:
            self.db.finish_job(job_id, status, error)
        self._changed(list_changed=True)

    def _credits(self, job):
        """Canonical lead artist (folder and album artist) and the full artist credit of a job."""
        raw_lead = names.lead_artist(job['artist'], self.db.known_artists())
        lead = self.artists.canonical(raw_lead) if raw_lead else ''
        return lead, names.credit(job['artist'], raw_lead, lead)

    def _claim_song(self, job_id, song):
        """Wait while another worker downloads the same song, then mark this one as in progress."""
        lead, key, duration = song
        with self._cond:
            while any(other[:2] == (lead, key) and names.same_recording(other[2], duration)
                      for other in self._active_songs.values()):
                if job_id in self._cancel:
                    raise _Canceled()
                self._cond.wait(timeout=5)
            self._active_songs[job_id] = song

    def _in_library(self, job, lead):
        existing = self.db.get_track(job['video_id'])
        if existing and os.path.exists(existing['path']):
            return True
        # One song often exists on YouTube as several videos (single, album track, music video)
        for track in self.db.find_same_song(lead, names.title_key(job['title'])):
            if names.same_recording(track['duration'], job['duration']) and os.path.exists(track['path']):
                return True
        return False

    def _download_with_retry(self, job, lead, credit, attempts=3):
        for attempt in range(attempts):
            try:
                return self._download(job, lead, credit)
            except _Canceled:
                raise
            except Exception as e:
                if ytdl.is_bot_check(e):
                    raise BotCheckError(clean_error(e)) from e
                if attempt == attempts - 1 or not any(marker in str(e) for marker in _RETRYABLE_MARKERS):
                    raise
                time.sleep(2 * (attempt + 1))

    # endregion

    # region single download

    def _reserve_stem(self, out_dir, base, video_id):
        stem = os.path.join(out_dir, base)
        with self._cond:
            taken = stem.lower() in self._reserved
            if not taken and glob.glob(glob.escape(stem) + '.*'):
                owner = self.db.get_track(video_id)
                taken = not owner or not owner['path'].lower().startswith(stem.lower() + '.')
            if taken:
                stem = os.path.join(out_dir, f'{base} ({video_id})')
            self._reserved.add(stem.lower())
        return stem

    def _target_stem(self, s, lead, title, video_id):
        out_dir = s['music_dir'] or self.host.default_music_dir
        if s['folder_per_artist'] and lead:
            out_dir = os.path.join(out_dir, safe_name(lead))
        os.makedirs(out_dir, exist_ok=True)
        base = safe_name(f'{lead} - {title}' if lead else title)
        return self._reserve_stem(out_dir, base, video_id)

    def _file_saved(self, path):
        if self.host.on_file_saved:
            try:
                self.host.on_file_saved(path)
            except Exception as e:
                log.warning('File-saved hook failed for %s: %s', path, e)

    def _copy_from_peer(self, job, lead, credit):
        """Copy the song from the paired computer when it has it. None means: download it from YouTube."""
        job_id, video_id = job['id'], job['video_id']
        peer = self.peer
        if peer is None or not peer.has(video_id):
            if not _YOUTUBE_ID.fullmatch(video_id):
                raise RuntimeError('This song is only on the computer. Connect to the home network to copy it.')
            return None
        info = peer.info(video_id)
        lead = lead or info.get('lead_artist') or ''
        title = job['title'] if job['title'] != video_id else info['title']
        stem = self._target_stem(self.settings.to_dict(), lead, title, video_id)
        self._update(job_id, 'downloading', 0)

        def progress(done, total):
            if job_id in self._cancel:
                raise _Canceled()
            self._update(job_id, 'downloading', done * 100 / total if total else 0)

        try:
            path = peer.fetch(video_id, stem, progress)
        except _Canceled:
            raise
        except Exception as e:
            log.info('Copying %s from the computer failed: %s', video_id, e)
            if _YOUTUBE_ID.fullmatch(video_id):
                return None
            raise RuntimeError('Copying from the computer failed. Try again at home.') from e
        finally:
            with self._cond:
                self._reserved.discard(stem.lower())
        self._file_saved(path)
        title = info.get('title') or title
        return {
            'video_id': video_id,
            'title': title,
            'artist': info.get('artist') or credit,
            'album': info.get('album') or job['album'],
            'duration': info.get('duration') or job['duration'],
            'thumb': info.get('thumb') or job['thumb'],
            'path': path,
            'size': os.path.getsize(path),
            'lead_artist': lead,
            'title_key': names.title_key(title),
        }

    def _convert_on_device(self, job_id, source, stem, audio_format, bitrate):
        """Phones have no FFmpeg: the app converts the downloaded file itself. Returns the final path.

        When conversion fails the original download is kept, so the song is still saved.
        """
        self._update(job_id, 'converting', 100)
        try:
            return str(self.host.audio_converter.convert(source, stem, audio_format, bitrate))
        except Exception as e:
            log.warning('Converting %s to %s failed, keeping the original: %s', source, audio_format, e)
            target = stem + os.path.splitext(source)[1]
            shutil.move(source, target)
            return target

    def _download(self, job, lead, credit):
        job_id, video_id = job['id'], job['video_id']
        s = self.settings.to_dict()
        audio_format = s['audio_format'] if s['audio_format'] in self.host.formats else 'm4a'
        saver = s['audio_quality'] == 'saver'
        bitrate = 128 if saver else s['mp3_bitrate']
        # Without FFmpeg (the phone) MP3 is encoded from the M4A download, and Opus is repackaged from YouTube's own
        convert_on_device = audio_format != 'm4a' and not self.host.ffmpeg_dir
        stem = self._target_stem(s, lead, job['title'], video_id)

        temp_files = set()

        def progress_hook(d):
            if job_id in self._cancel:
                raise _Canceled()
            if d.get('tmpfilename'):
                temp_files.add(d['tmpfilename'])
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                done = d.get('downloaded_bytes') or 0
                self._update(job_id, 'downloading', done * 100 / total if total else 0, d.get('speed'), d.get('eta'))
            elif d['status'] == 'finished':
                self._update(job_id, 'processing', 100)

        def postprocessor_hook(d):
            if job_id in self._cancel:
                raise _Canceled()

        # Partial files live in a private temp folder: Android's scoped storage only allows audio files in
        # Music/, and on desktop half-downloaded files shouldn't show up in the music folder either
        temp_dir = os.path.join(self.host.cache_dir, f'job-{job_id}')
        download_format = 'm4a' if convert_on_device and audio_format == 'mp3' else audio_format
        opts = ytdl.base_options(s, self.host)
        opts.update({
            'format': (FORMATS_SAVER if saver else FORMATS)[download_format],
            # Files that are converted afterwards stay in the private folder until then
            'paths': {'home': temp_dir if convert_on_device else os.path.dirname(stem), 'temp': temp_dir},
            'outtmpl': os.path.basename(stem).replace('%', '%%') + '.%(ext)s',
            'progress_hooks': [progress_hook],
            'postprocessor_hooks': [postprocessor_hook],
            'overwrites': True,
        })
        if self.host.ffmpeg_dir:
            opts['ffmpeg_location'] = self.host.ffmpeg_dir
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': audio_format,
                'preferredquality': str(bitrate) if audio_format == 'mp3' else None,
            }]

        try:
            with ytdl.cookie_file(s, self.host) as cookies:
                if cookies:
                    opts['cookiefile'] = cookies
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
            path = info['requested_downloads'][0]['filepath']
            if convert_on_device:
                path = self._convert_on_device(job_id, path, stem, audio_format, bitrate)
        except BaseException:
            for temp in temp_files:
                for leftover in (temp, temp + '.ytdl'):
                    if os.path.exists(leftover):
                        try:
                            os.remove(leftover)
                        except OSError:
                            pass
            raise
        finally:
            with self._cond:
                self._reserved.discard(stem.lower())
            shutil.rmtree(temp_dir, ignore_errors=True)

        title = job['title'] if job['title'] != video_id else (info.get('track') or info.get('title') or video_id)
        if not credit:
            credit = ', '.join(info.get('artists') or []) or re.sub(r' - Topic$', '', info.get('uploader') or '')
            lead = names.lead_artist(credit)
        album = job['album'] or info.get('album') or ''
        thumb = job['thumb'] or info.get('thumbnail') or ''
        try:
            hide_pictures = self.content_filter is not None and self.content_filter.active
            cover = tagging.fetch_cover(thumb) if s['embed_cover'] and not hide_pictures else None
            tagging.write_tags(path, title, credit, album, cover, album_artist=lead)
        except Exception as e:
            log.warning('Tagging %s failed: %s', path, e)
        self._file_saved(path)

        return {
            'video_id': video_id,
            'title': title,
            'artist': credit,
            'album': album,
            'duration': job['duration'] or int(info.get('duration') or 0),
            'thumb': thumb,
            'path': path,
            'size': os.path.getsize(path),
            'lead_artist': lead,
            'title_key': names.title_key(title),
        }

    # endregion


def resolve_url(url):
    """Turn a pasted YouTube / YouTube Music link (video or playlist) into queueable tracks."""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    if 'list' not in query:
        match = _VIDEO_ID.search(url)
        if not match:
            raise ValueError('Not a YouTube link')
        video_id = match.group(1)
        # oEmbed returns the title without touching the (bot-checked) player API
        title, author = video_id, ''
        try:
            oembed = 'https://www.youtube.com/oembed?format=json&url=' + urllib.parse.quote(
                f'https://www.youtube.com/watch?v={video_id}', safe='')
            with urllib.request.urlopen(oembed, timeout=15, context=tagging._SSL) as response:
                data = json.load(response)
            title, author = data.get('title') or video_id, data.get('author_name') or ''
        except Exception:
            pass
        return [{
            'videoId': video_id,
            'title': title,
            'artist': re.sub(r' - Topic$', '', author),
            'album': '',
            'duration': 0,
            'thumb': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
        }]

    opts = {'extract_flat': 'in_playlist', 'quiet': True, 'no_warnings': True, 'logger': ytdl.LOGGER,
            'skip_download': True, 'playlistend': 5000}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f'https://www.youtube.com/playlist?list={query["list"][0]}', download=False)
    tracks = []
    for entry in info.get('entries') or []:
        if not entry or not entry.get('id') or len(entry['id']) != 11:
            continue
        tracks.append({
            'videoId': entry['id'],
            'title': entry.get('title') or entry['id'],
            'artist': re.sub(r' - Topic$', '', entry.get('channel') or entry.get('uploader') or ''),
            'album': '',
            'duration': int(entry.get('duration') or 0),
            'thumb': f'https://i.ytimg.com/vi/{entry["id"]}/hqdefault.jpg',
        })
    return tracks
