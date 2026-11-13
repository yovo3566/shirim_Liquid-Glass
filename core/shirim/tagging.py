"""Write title, artist, album and cover art tags into downloaded audio files."""

import base64
import ssl
import urllib.request

import certifi
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

_SSL = ssl.create_default_context(cafile=certifi.where())
_MAX_COVER_BYTES = 5 * 1024 * 1024


def fetch_cover(url, timeout=15):
    """Return (bytes, mime) for a cover image URL, or None when it can't be fetched."""
    if not url:
        return None
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=timeout, context=_SSL) as response:
            return response.read(_MAX_COVER_BYTES), response.headers.get_content_type()
    except Exception:
        return None


def _load_id3(path):
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()


def write_tags(path, title, artist='', album='', cover=None, album_artist=None):
    if album_artist is None:
        album_artist = artist.split(', ')[0] if artist else ''
    lower = path.lower()

    if lower.endswith('.m4a'):
        audio = MP4(path)
        audio['\xa9nam'] = [title]
        if artist:
            audio['\xa9ART'] = [artist]
        if album_artist:
            audio['aART'] = [album_artist]
        if album:
            audio['\xa9alb'] = [album]
        if cover:
            image_format = MP4Cover.FORMAT_PNG if cover[1] == 'image/png' else MP4Cover.FORMAT_JPEG
            audio['covr'] = [MP4Cover(cover[0], imageformat=image_format)]
        audio.save()

    elif lower.endswith('.mp3'):
        tags = _load_id3(path)
        tags.setall('TIT2', [TIT2(encoding=3, text=title)])
        if artist:
            tags.setall('TPE1', [TPE1(encoding=3, text=artist)])
        if album_artist:
            tags.setall('TPE2', [TPE2(encoding=3, text=album_artist)])
        if album:
            tags.setall('TALB', [TALB(encoding=3, text=album)])
        if cover:
            tags.setall('APIC', [APIC(encoding=3, mime=cover[1], type=3, desc='Cover', data=cover[0])])
        tags.save(path, v2_version=3)

    elif lower.endswith('.opus'):
        audio = OggOpus(path)
        audio['title'] = [title]
        if artist:
            audio['artist'] = [artist]
        if album_artist:
            audio['albumartist'] = [album_artist]
        if album:
            audio['album'] = [album]
        if cover:
            picture = Picture()
            picture.type = 3
            picture.mime = cover[1]
            picture.data = cover[0]
            audio['metadata_block_picture'] = [base64.b64encode(picture.write()).decode('ascii')]
        audio.save()


def update_artists(path, artist, album_artist):
    """Rewrite only the artist fields of an existing file."""
    lower = path.lower()
    if lower.endswith('.m4a'):
        audio = MP4(path)
        audio['\xa9ART'] = [artist]
        audio['aART'] = [album_artist]
        audio.save()
    elif lower.endswith('.mp3'):
        tags = _load_id3(path)
        tags.setall('TPE1', [TPE1(encoding=3, text=artist)])
        tags.setall('TPE2', [TPE2(encoding=3, text=album_artist)])
        tags.save(path, v2_version=3)
    elif lower.endswith('.opus'):
        audio = OggOpus(path)
        audio['artist'] = [artist]
        audio['albumartist'] = [album_artist]
        audio.save()
