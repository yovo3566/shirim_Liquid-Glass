"""Platform-specific environment supplied by the desktop or Android launcher."""

import dataclasses
import os
import typing


@dataclasses.dataclass
class Host:
    platform: str  # 'windows' | 'android'
    data_dir: str  # settings, database, cookies and caches
    default_music_dir: str
    web_dir: str
    ffmpeg_dir: str = ''  # empty when FFmpeg isn't available (Android)
    js_runtimes: dict = dataclasses.field(default_factory=lambda: {'deno': {}})
    pot_args: dict = dataclasses.field(default_factory=dict)  # extractor_args that enable a PO token provider
    on_file_saved: typing.Callable[[str], None] | None = None  # e.g. Android media scanning
    music_roots: list = dataclasses.field(default_factory=list)  # folders searched for music already on the device
    # Returns the device's music as [{path, title, artist, album, duration (ms), size, mtime}] (Android's MediaStore);
    # when set it replaces walking music_roots
    local_music_provider: typing.Callable[[], list] | None = None
    device_name: str = ''  # shown on the paired device, e.g. "DESKTOP-1A2B" or "Samsung SM-G996B"
    # Without FFmpeg (Android): converts a downloaded file, convert(source, target_stem, format, bitrate) -> path
    audio_converter: typing.Any = None
    opus_conversion: bool = False  # whether audio_converter can produce Opus (Android 10 and later)
    # Folders the user can pick for saved songs on a phone: [{'label', 'path'}]
    folder_choices: list = dataclasses.field(default_factory=list)
    # App updates: the phone's installed version and installer; the computer's folder holding the newest phone app
    app_version_code: int = 0
    app_installer: typing.Any = None
    app_update_dir: str = ''
    # The filtered edition: filtered mode is always on and can't be turned off or loosened
    filter_forced: bool = False

    @property
    def app_update_channel(self):
        return 'filtered' if self.filter_forced else 'standard'

    @property
    def formats(self):
        """Audio formats songs can be saved in on this device."""
        if self.ffmpeg_dir:
            return ['m4a', 'mp3', 'opus']
        if self.audio_converter is not None:
            return ['m4a', 'mp3'] + (['opus'] if self.opus_conversion else [])
        return ['m4a']

    def __post_init__(self):
        os.makedirs(self.cache_dir, exist_ok=True)

    @property
    def cache_dir(self):
        return os.path.join(self.data_dir, 'cache')

    @property
    def cookie_file(self):
        return os.path.join(self.data_dir, 'cookies.txt')
