"""SQLite storage: music library, download queue, playlists, likes, followed artists and listening history."""

import json
import sqlite3
import threading
import time
import uuid

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS tracks (
    video_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL DEFAULT '',
    album       TEXT NOT NULL DEFAULT '',
    duration    INTEGER NOT NULL DEFAULT 0,
    thumb       TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    added_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL DEFAULT '',
    album       TEXT NOT NULL DEFAULT '',
    duration    INTEGER NOT NULL DEFAULT 0,
    thumb       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS artist_aliases (
    name        TEXT PRIMARY KEY,
    browse_id   TEXT,
    canonical   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artist_aliases_browse_id ON artist_aliases(browse_id);
CREATE TABLE IF NOT EXISTS likes (
    video_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL DEFAULT '',
    album       TEXT NOT NULL DEFAULT '',
    duration    INTEGER NOT NULL DEFAULT 0,
    thumb       TEXT NOT NULL DEFAULT '',
    liked_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL,
    position    INTEGER NOT NULL,
    video_id    TEXT NOT NULL,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL DEFAULT '',
    album       TEXT NOT NULL DEFAULT '',
    duration    INTEGER NOT NULL DEFAULT 0,
    thumb       TEXT NOT NULL DEFAULT '',
    added_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS playlist_tracks_order ON playlist_tracks(playlist_id, position);
CREATE TABLE IF NOT EXISTS follows (
    browse_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    thumb       TEXT NOT NULL DEFAULT '',
    followed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plays (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL,
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL DEFAULT '',
    album       TEXT NOT NULL DEFAULT '',
    duration    INTEGER NOT NULL DEFAULT 0,
    thumb       TEXT NOT NULL DEFAULT '',
    played_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS plays_time ON plays(played_at);
CREATE INDEX IF NOT EXISTS plays_song ON plays(video_id, played_at);
CREATE TABLE IF NOT EXISTS artist_releases (
    artist_id   TEXT NOT NULL,
    release_id  TEXT NOT NULL,
    artist_name TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    year        TEXT NOT NULL DEFAULT '',
    thumb       TEXT NOT NULL DEFAULT '',
    first_seen  REAL NOT NULL,
    is_new      INTEGER NOT NULL DEFAULT 0,  -- appeared after the artist was first checked
    seen        INTEGER NOT NULL DEFAULT 0,  -- the user has looked at the new-releases screen since
    saved       INTEGER NOT NULL DEFAULT 0,  -- queued for offline saving
    PRIMARY KEY (artist_id, release_id)
);
CREATE INDEX IF NOT EXISTS artist_releases_new ON artist_releases(is_new, first_seen);
CREATE TABLE IF NOT EXISTS cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    expires     REAL NOT NULL
);
-- Removed likes, follows and playlists, so a sync doesn't bring them back from the other device
CREATE TABLE IF NOT EXISTS tombstones (
    kind        TEXT NOT NULL,  -- 'like' | 'follow' | 'playlist'
    key         TEXT NOT NULL,
    deleted_at  REAL NOT NULL,
    PRIMARY KEY (kind, key)
);
-- Filtered mode: the user's own decisions about performers and songs ('allow' shows, 'block' hides)
CREATE TABLE IF NOT EXISTS filter_rules (
    kind        TEXT NOT NULL,  -- 'artist' (key: content_filter.artist_key) | 'song' (key: video id)
    key         TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (kind, key)
);
-- Filtered mode: who performers are (female / male / mixed / unknown), as found on Wikidata
CREATE TABLE IF NOT EXISTS artist_gender (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    gender      TEXT NOT NULL,
    source      TEXT NOT NULL,
    checked_at  REAL NOT NULL
);
-- Phones paired with this computer for syncing; only a hash of each device's token is kept
CREATE TABLE IF NOT EXISTS sync_devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    paired_at   REAL NOT NULL,
    last_seen   REAL NOT NULL DEFAULT 0
);
'''

# Columns added after the first release; created on startup when missing
_MIGRATIONS = {
    'tracks': {
        'lead_artist': "ALTER TABLE tracks ADD COLUMN lead_artist TEXT NOT NULL DEFAULT ''",
        'title_key': "ALTER TABLE tracks ADD COLUMN title_key TEXT NOT NULL DEFAULT ''",
        # 'download' for songs the app downloaded, 'local' for music found on the device
        'source': "ALTER TABLE tracks ADD COLUMN source TEXT NOT NULL DEFAULT 'download'",
        'mtime': 'ALTER TABLE tracks ADD COLUMN mtime REAL NOT NULL DEFAULT 0',
    },
    'playlists': {
        # Identifies a playlist across synced devices, whose numeric ids differ
        'uid': "ALTER TABLE playlists ADD COLUMN uid TEXT NOT NULL DEFAULT ''",
    },
    'plays': {
        # 1 once the play has been sent to (or came from) the paired computer
        'synced': 'ALTER TABLE plays ADD COLUMN synced INTEGER NOT NULL DEFAULT 0',
    },
}
_TRACK_COLUMNS = ('title', 'artist', 'album', 'duration', 'thumb', 'path', 'size', 'lead_artist', 'title_key')

# Song fields stored with likes, playlist items and plays, so they display without the catalogue
_SONG_COLUMNS = 'video_id, title, artist, album, duration, thumb'
_SONG_SELECT = 'video_id AS videoId, title, artist, album, duration, thumb'
_MAX_PLAYS = 50000  # years of listening; the monthly recap reads from these
_TOMBSTONE_DAYS = 365

FINISHED = ('done', 'error', 'canceled', 'skipped')

# SQLite limits the number of bound parameters per statement
_CHUNK = 500


def _chunks(items):
    items = list(items)
    for i in range(0, len(items), _CHUNK):
        yield items[i:i + _CHUNK]


def _song(track):
    """Song values from a catalogue track ('videoId') or a library track ('video_id')."""
    return (
        track.get('videoId') or track.get('video_id'),
        str(track.get('title') or '')[:300],
        str(track.get('artist') or '')[:300],
        str(track.get('album') or '')[:300],
        int(track.get('duration') or 0),
        str(track.get('thumb') or '')[:1000],
    )


def _new_uid():
    return uuid.uuid4().hex


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.executescript(_SCHEMA)
        for table, migrations in _MIGRATIONS.items():
            columns = {row[1] for row in self._conn.execute(f'PRAGMA table_info({table})')}
            for column, statement in migrations.items():
                if column not in columns:
                    self._conn.execute(statement)
        self._conn.execute('CREATE INDEX IF NOT EXISTS tracks_song ON tracks(lead_artist, title_key)')
        for (playlist_id,) in self._conn.execute("SELECT id FROM playlists WHERE uid = ''").fetchall():
            self._conn.execute('UPDATE playlists SET uid = ? WHERE id = ?', (_new_uid(), playlist_id))
        self._conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS playlists_uid ON playlists(uid)')
        # Jobs that were mid-download when the app closed start over
        self._conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, args)]

    def _one(self, sql, args=()):
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _run(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).rowcount

    def _transaction(self):
        return _Transaction(self)

    def backup(self, path):
        with self._lock:
            target = sqlite3.connect(path)
            try:
                self._conn.backup(target)
            finally:
                target.close()

    # region jobs

    def add_jobs(self, tracks):
        """Queue tracks for download, ignoring ones already queued or running. Returns new job ids."""
        now = time.time()
        added = []
        with self._transaction() as conn:
            active = {row[0] for row in conn.execute(
                "SELECT video_id FROM jobs WHERE status IN ('queued', 'running')")}
            for track in tracks:
                video_id = track.get('videoId')
                if not video_id or video_id in active:
                    continue
                active.add(video_id)
                cursor = conn.execute(
                    'INSERT INTO jobs (video_id, title, artist, album, duration, thumb, created_at) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (video_id, track.get('title') or video_id, track.get('artist') or '',
                     track.get('album') or '', int(track.get('duration') or 0),
                     track.get('thumb') or '', now))
                added.append(cursor.lastrowid)
        return added

    def claim_next_job(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE jobs SET status = 'running', error = '' WHERE id = ?", (row['id'],))
            job = dict(row)
            job['status'] = 'running'
            return job

    def finish_job(self, job_id, status, error=''):
        self._run('UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?',
                  (status, error, time.time(), job_id))

    def requeue_job(self, job_id):
        self._run("UPDATE jobs SET status = 'queued' WHERE id = ? AND status = 'running'", (job_id,))

    def list_jobs(self, limit=2000):
        return self._all(
            'SELECT * FROM jobs ORDER BY '
            "CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
            "CASE WHEN status IN ('running', 'queued') THEN id END, "
            'finished_at DESC LIMIT ?', (limit,))

    def job_counts(self):
        counts = dict.fromkeys(('queued', 'running', *FINISHED), 0)
        for row in self._all('SELECT status, COUNT(*) AS n FROM jobs GROUP BY status'):
            counts[row['status']] = row['n']
        return counts

    def cancel_queued(self, ids=None):
        """Cancel queued jobs (all of them when ids is None). Running jobs are stopped by the manager."""
        now = time.time()
        if ids is None:
            return self._run("UPDATE jobs SET status = 'canceled', finished_at = ? WHERE status = 'queued'", (now,))
        total = 0
        for chunk in _chunks(ids):
            marks = ','.join('?' * len(chunk))
            total += self._run(
                f"UPDATE jobs SET status = 'canceled', finished_at = ? WHERE status = 'queued' AND id IN ({marks})",
                (now, *chunk))
        return total

    def retry(self, ids=None):
        where = "status IN ('error', 'canceled')"
        if ids is None:
            return self._run(f"UPDATE jobs SET status = 'queued', error = '', finished_at = NULL WHERE {where}")
        total = 0
        for chunk in _chunks(ids):
            marks = ','.join('?' * len(chunk))
            total += self._run(
                f"UPDATE jobs SET status = 'queued', error = '', finished_at = NULL WHERE {where} AND id IN ({marks})",
                tuple(chunk))
        return total

    def clear_finished(self):
        return self._run(f"DELETE FROM jobs WHERE status IN ({','.join('?' * len(FINISHED))})", FINISHED)

    # endregion

    # region library

    def add_track(self, track):
        with self._transaction() as conn:
            # A library scan may have picked the file up as a local song just before the download finished
            conn.execute("DELETE FROM tracks WHERE path = ? AND source = 'local'", (track['path'],))
            conn.execute(
                'INSERT OR REPLACE INTO tracks '
                '(video_id, title, artist, album, duration, thumb, path, size, added_at, lead_artist, title_key) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (track['video_id'], track['title'], track.get('artist', ''), track.get('album', ''),
                 int(track.get('duration') or 0), track.get('thumb', ''), track['path'],
                 int(track.get('size') or 0), time.time(), track.get('lead_artist', ''), track.get('title_key', '')))

    def upsert_local_tracks(self, rows):
        """Add or refresh songs found on the device, keeping when they were first added."""
        now = time.time()
        with self._transaction() as conn:
            for row in rows:
                conn.execute(
                    'INSERT INTO tracks (video_id, title, artist, album, duration, thumb, path, size, added_at, '
                    "lead_artist, title_key, source, mtime) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', ?) "
                    'ON CONFLICT(video_id) DO UPDATE SET title = excluded.title, artist = excluded.artist, '
                    'album = excluded.album, duration = excluded.duration, thumb = excluded.thumb, '
                    'path = excluded.path, size = excluded.size, lead_artist = excluded.lead_artist, '
                    'title_key = excluded.title_key, mtime = excluded.mtime',
                    (row['video_id'], row['title'], row['artist'], row['album'], int(row['duration'] or 0),
                     row['thumb'], row['path'], int(row['size'] or 0), now, row['lead_artist'], row['title_key'],
                     float(row['mtime'] or 0)))

    def delete_local_tracks_except(self, keep_ids):
        with self._transaction() as conn:
            stale = [row[0] for row in conn.execute("SELECT video_id FROM tracks WHERE source = 'local'")
                     if row[0] not in keep_ids]
            for video_id in stale:
                conn.execute('DELETE FROM tracks WHERE video_id = ?', (video_id,))
        return len(stale)

    def update_tracks(self, updates):
        """Apply [(video_id, {column: value})] in one transaction."""
        with self._transaction() as conn:
            for video_id, fields in updates:
                fields = {k: v for k, v in fields.items() if k in _TRACK_COLUMNS}
                if fields:
                    assignments = ', '.join(f'{column} = ?' for column in fields)
                    conn.execute(f'UPDATE tracks SET {assignments} WHERE video_id = ?', (*fields.values(), video_id))

    def get_track(self, video_id):
        return self._one('SELECT * FROM tracks WHERE video_id = ?', (video_id,))

    def find_same_song(self, lead_artist, title_key):
        return self._all('SELECT * FROM tracks WHERE lead_artist = ? AND title_key = ?', (lead_artist, title_key))

    def known_artists(self):
        """Lead artists in the library plus every name already resolved (covers artists still downloading)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT lead_artist FROM tracks WHERE lead_artist != '' "
                'UNION SELECT name FROM artist_aliases UNION SELECT canonical FROM artist_aliases')
            return {row[0] for row in rows}

    def library_counts_by_artist(self):
        return self._all("SELECT lead_artist, COUNT(*) AS n FROM tracks WHERE lead_artist != '' GROUP BY lead_artist")

    def list_tracks(self):
        return self._all('SELECT * FROM tracks ORDER BY added_at DESC')

    def track_ids(self):
        with self._lock:
            return {row[0] for row in self._conn.execute('SELECT video_id FROM tracks')}

    def delete_track(self, video_id):
        return self._run('DELETE FROM tracks WHERE video_id = ?', (video_id,))

    # endregion

    # region artist aliases

    def get_alias(self, name):
        return self._one('SELECT * FROM artist_aliases WHERE name = ?', (name,))

    def aliases_for_browse_id(self, browse_id):
        return self._all('SELECT * FROM artist_aliases WHERE browse_id = ?', (browse_id,))

    def browse_id_for(self, canonical):
        row = self._one('SELECT browse_id FROM artist_aliases WHERE canonical = ? AND browse_id IS NOT NULL '
                        'ORDER BY name = canonical DESC LIMIT 1', (canonical,))
        return row['browse_id'] if row else None

    def set_alias(self, name, browse_id, canonical):
        self._run('INSERT OR REPLACE INTO artist_aliases (name, browse_id, canonical) VALUES (?, ?, ?)',
                  (name, browse_id, canonical))

    # endregion

    # region likes

    def set_like(self, track, liked):
        values = _song(track)
        now = time.time()
        with self._transaction() as conn:
            if liked:
                conn.execute(f'INSERT OR REPLACE INTO likes ({_SONG_COLUMNS}, liked_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (*values, now))
                conn.execute("DELETE FROM tombstones WHERE kind = 'like' AND key = ?", (values[0],))
            elif conn.execute('DELETE FROM likes WHERE video_id = ?', (values[0],)).rowcount:
                conn.execute("INSERT OR REPLACE INTO tombstones (kind, key, deleted_at) VALUES ('like', ?, ?)",
                             (values[0], now))

    def list_likes(self):
        return self._all(f'SELECT {_SONG_SELECT}, liked_at FROM likes ORDER BY liked_at DESC')

    def liked_ids(self):
        with self._lock:
            return [row[0] for row in self._conn.execute('SELECT video_id FROM likes')]

    # endregion

    # region playlists

    def create_playlist(self, name):
        now = time.time()
        with self._lock:
            return self._conn.execute('INSERT INTO playlists (name, created_at, updated_at, uid) VALUES (?, ?, ?, ?)',
                                      (name, now, now, _new_uid())).lastrowid

    def rename_playlist(self, playlist_id, name):
        return self._run('UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?', (name, time.time(), playlist_id))

    def delete_playlist(self, playlist_id):
        with self._transaction() as conn:
            row = conn.execute('SELECT uid FROM playlists WHERE id = ?', (playlist_id,)).fetchone()
            if row:
                conn.execute("INSERT OR REPLACE INTO tombstones (kind, key, deleted_at) VALUES ('playlist', ?, ?)",
                             (row[0], time.time()))
            conn.execute('DELETE FROM playlist_tracks WHERE playlist_id = ?', (playlist_id,))
            return conn.execute('DELETE FROM playlists WHERE id = ?', (playlist_id,)).rowcount

    def list_playlists(self):
        playlists = self._all(
            'SELECT p.id, p.uid, p.name, p.updated_at, COUNT(t.item_id) AS count FROM playlists p '
            'LEFT JOIN playlist_tracks t ON t.playlist_id = p.id GROUP BY p.id ORDER BY p.updated_at DESC')
        for playlist in playlists:
            playlist['thumbs'] = [row['thumb'] for row in self._all(
                "SELECT thumb FROM playlist_tracks WHERE playlist_id = ? AND thumb != '' ORDER BY position LIMIT 4",
                (playlist['id'],))]
        return playlists

    def get_playlist(self, playlist_id):
        playlist = self._one('SELECT id, uid, name, updated_at FROM playlists WHERE id = ?', (playlist_id,))
        if playlist:
            playlist['tracks'] = self._all(
                f'SELECT item_id AS itemId, {_SONG_SELECT} FROM playlist_tracks WHERE playlist_id = ? ORDER BY position',
                (playlist_id,))
        return playlist

    def playlist_id_for_uid(self, uid):
        row = self._one('SELECT id FROM playlists WHERE uid = ?', (uid,))
        return row['id'] if row else None

    def playlist_video_ids(self):
        with self._lock:
            return {row[0] for row in self._conn.execute('SELECT DISTINCT video_id FROM playlist_tracks')}

    def add_to_playlist(self, playlist_id, tracks):
        """Append tracks, skipping songs already in the playlist. Returns how many were added."""
        now = time.time()
        added = 0
        with self._transaction() as conn:
            present = {row[0] for row in conn.execute(
                'SELECT video_id FROM playlist_tracks WHERE playlist_id = ?', (playlist_id,))}
            position = conn.execute('SELECT COALESCE(MAX(position), -1) FROM playlist_tracks WHERE playlist_id = ?',
                                    (playlist_id,)).fetchone()[0]
            for track in tracks:
                values = _song(track)
                if values[0] in present:
                    continue
                present.add(values[0])
                position += 1
                added += 1
                conn.execute(f'INSERT INTO playlist_tracks (playlist_id, position, {_SONG_COLUMNS}, added_at) '
                             'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (playlist_id, position, *values, now))
            conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (now, playlist_id))
        return added

    def _renumber(self, conn, playlist_id, item_ids):
        for position, item_id in enumerate(item_ids):
            conn.execute('UPDATE playlist_tracks SET position = ? WHERE item_id = ?', (position, item_id))
        conn.execute('UPDATE playlists SET updated_at = ? WHERE id = ?', (time.time(), playlist_id))

    def remove_from_playlist(self, playlist_id, item_ids):
        remove = set(item_ids)
        with self._transaction() as conn:
            order = [row[0] for row in conn.execute(
                'SELECT item_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position', (playlist_id,))]
            for item_id in remove:
                conn.execute('DELETE FROM playlist_tracks WHERE item_id = ? AND playlist_id = ?', (item_id, playlist_id))
            self._renumber(conn, playlist_id, [i for i in order if i not in remove])

    def move_playlist_item(self, playlist_id, item_id, index):
        with self._transaction() as conn:
            order = [row[0] for row in conn.execute(
                'SELECT item_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position', (playlist_id,))]
            if item_id not in order:
                return
            order.remove(item_id)
            order.insert(max(0, min(index, len(order))), item_id)
            self._renumber(conn, playlist_id, order)

    # endregion

    # region followed artists and their releases

    def set_follow(self, browse_id, name, thumb, followed):
        now = time.time()
        with self._transaction() as conn:
            if followed:
                conn.execute('INSERT OR REPLACE INTO follows (browse_id, name, thumb, followed_at) VALUES (?, ?, ?, ?)',
                             (browse_id, name, thumb, now))
                conn.execute("DELETE FROM tombstones WHERE kind = 'follow' AND key = ?", (browse_id,))
            elif conn.execute('DELETE FROM follows WHERE browse_id = ?', (browse_id,)).rowcount:
                conn.execute("INSERT OR REPLACE INTO tombstones (kind, key, deleted_at) VALUES ('follow', ?, ?)",
                             (browse_id, now))

    def list_follows(self):
        return self._all('SELECT browse_id AS browseId, name, thumb, followed_at FROM follows ORDER BY followed_at DESC')

    def known_release_ids(self, artist_id):
        with self._lock:
            return {row[0] for row in self._conn.execute(
                'SELECT release_id FROM artist_releases WHERE artist_id = ?', (artist_id,))}

    def add_releases(self, artist_id, artist_name, releases):
        now = time.time()
        with self._transaction() as conn:
            for release in releases:
                conn.execute(
                    'INSERT OR IGNORE INTO artist_releases '
                    '(artist_id, release_id, artist_name, title, year, thumb, first_seen, is_new) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (artist_id, release['browseId'], artist_name, release.get('title') or '', str(release.get('year') or ''),
                     release.get('thumb') or '', now, int(bool(release.get('is_new')))))

    def list_new_releases(self, limit=60):
        return self._all(
            'SELECT release_id AS browseId, artist_id AS artistId, artist_name AS artist, title, year, thumb, '
            'MAX(first_seen) AS first_seen, MIN(seen) AS seen FROM artist_releases WHERE is_new = 1 '
            'GROUP BY release_id ORDER BY first_seen DESC LIMIT ?', (limit,))

    def unseen_release_count(self):
        return self._one('SELECT COUNT(DISTINCT release_id) AS n FROM artist_releases WHERE is_new = 1 AND seen = 0')['n']

    def mark_releases_seen(self):
        self._run('UPDATE artist_releases SET seen = 1 WHERE is_new = 1 AND seen = 0')

    def releases_to_save(self):
        return self._all('SELECT DISTINCT release_id FROM artist_releases WHERE is_new = 1 AND saved = 0')

    def mark_release_saved(self, release_id):
        self._run('UPDATE artist_releases SET saved = 1 WHERE release_id = ?', (release_id,))

    # endregion

    # region listening history

    def add_play(self, track):
        values = _song(track)
        now = time.time()
        with self._transaction() as conn:
            last = conn.execute('SELECT video_id, played_at FROM plays ORDER BY id DESC LIMIT 1').fetchone()
            # Pausing, seeking or reopening the app shouldn't count the same song again
            if last and last['video_id'] == values[0] and now - last['played_at'] < 120:
                return
            conn.execute(f'INSERT INTO plays ({_SONG_COLUMNS}, played_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (*values, now))
            conn.execute('DELETE FROM plays WHERE id <= (SELECT MAX(id) FROM plays) - ?', (_MAX_PLAYS,))

    def recent_plays(self, limit=20):
        # SQLite fills the other columns from the row holding MAX(played_at)
        return self._all(f'SELECT {_SONG_SELECT}, MAX(played_at) AS played_at FROM plays '
                         'GROUP BY video_id ORDER BY played_at DESC LIMIT ?', (limit,))

    def play_counts_by_artist(self, days=90):
        return self._all('SELECT artist, COUNT(*) AS n FROM plays WHERE played_at > ? GROUP BY artist',
                         (time.time() - days * 86400,))

    def plays_between(self, start, end):
        return self._all(f'SELECT {_SONG_SELECT}, played_at FROM plays WHERE played_at >= ? AND played_at < ? '
                         'ORDER BY played_at', (start, end))

    def first_play_times_by_artist(self):
        return {row['artist']: row['first'] for row in self._all(
            'SELECT artist, MIN(played_at) AS first FROM plays GROUP BY artist')}

    def play_time_range(self):
        return self._one('SELECT MIN(played_at) AS first, MAX(played_at) AS last, COUNT(*) AS n FROM plays')

    def last_played_map(self):
        with self._lock:
            return {row[0]: row[1] for row in self._conn.execute(
                'SELECT video_id, MAX(played_at) FROM plays GROUP BY video_id')}

    # endregion

    # region sync between devices

    def sync_state(self, plays_after_id=None, unsynced_plays=False):
        """Everything a paired device needs to converge with this one.

        plays_after_id: include plays inserted after this id (the other side's cursor).
        unsynced_plays: include plays not yet sent (the phone's side of the exchange).
        """
        state = {
            'likes': self._all(f'SELECT {_SONG_SELECT}, liked_at FROM likes'),
            'follows': self.list_follows(),
            'tombstones': self._all('SELECT kind, key, deleted_at FROM tombstones WHERE deleted_at > ?',
                                    (time.time() - _TOMBSTONE_DAYS * 86400,)),
            'playlists': [],
            'plays': [],
        }
        for playlist in self._all('SELECT id, uid, name, created_at, updated_at FROM playlists'):
            playlist['tracks'] = self._all(
                f'SELECT {_SONG_SELECT} FROM playlist_tracks WHERE playlist_id = ? ORDER BY position', (playlist.pop('id'),))
            state['playlists'].append(playlist)
        if unsynced_plays:
            state['plays'] = self._all(f'SELECT id, {_SONG_SELECT}, played_at FROM plays WHERE synced = 0 ORDER BY id')
        elif plays_after_id is not None:
            state['plays'] = self._all(f'SELECT id, {_SONG_SELECT}, played_at FROM plays WHERE id > ? ORDER BY id LIMIT 20000',
                                       (int(plays_after_id),))
        return state

    def max_play_id(self):
        return self._one('SELECT COALESCE(MAX(id), 0) AS n FROM plays')['n']

    def mark_plays_synced(self, ids):
        with self._transaction() as conn:
            for chunk in _chunks(ids):
                conn.execute(f"UPDATE plays SET synced = 1 WHERE id IN ({','.join('?' * len(chunk))})", tuple(chunk))

    def sync_merge(self, state):
        """Merge another device's state into this one. The newest change to each item wins. Returns changes made."""
        changes = 0
        now = time.time()
        with self._transaction() as conn:
            tombs = {(row[0], row[1]): row[2] for row in conn.execute('SELECT kind, key, deleted_at FROM tombstones')}

            def keep_tombstone(kind, key, deleted_at):
                if deleted_at > tombs.get((kind, key), -1):
                    tombs[kind, key] = deleted_at
                    conn.execute('INSERT OR REPLACE INTO tombstones (kind, key, deleted_at) VALUES (?, ?, ?)',
                                 (kind, key, deleted_at))

            incoming_tombs = []
            for tomb in state.get('tombstones') or []:
                if isinstance(tomb, dict) and tomb.get('kind') in ('like', 'follow', 'playlist') and tomb.get('key'):
                    incoming_tombs.append((tomb['kind'], str(tomb['key'])[:200], min(_number(tomb.get('deleted_at')), now)))

            # likes
            likes = {row[0]: row[1] for row in conn.execute('SELECT video_id, liked_at FROM likes')}
            for like in state.get('likes') or []:
                if not isinstance(like, dict):
                    continue
                values = _song(like)
                liked_at = min(_number(like.get('liked_at')), now)
                if not values[0] or liked_at <= likes.get(values[0], -1) or liked_at <= tombs.get(('like', values[0]), -1):
                    continue
                conn.execute(f'INSERT OR REPLACE INTO likes ({_SONG_COLUMNS}, liked_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (*values, liked_at))
                likes[values[0]] = liked_at
                changes += 1

            # followed artists
            follows = {row[0]: row[1] for row in conn.execute('SELECT browse_id, followed_at FROM follows')}
            for follow in state.get('follows') or []:
                if not isinstance(follow, dict) or not follow.get('browseId'):
                    continue
                browse_id = str(follow['browseId'])[:60]
                followed_at = min(_number(follow.get('followed_at')), now)
                if followed_at <= follows.get(browse_id, -1) or followed_at <= tombs.get(('follow', browse_id), -1):
                    continue
                conn.execute('INSERT OR REPLACE INTO follows (browse_id, name, thumb, followed_at) VALUES (?, ?, ?, ?)',
                             (browse_id, str(follow.get('name') or '')[:200], str(follow.get('thumb') or '')[:1000],
                              followed_at))
                follows[browse_id] = followed_at
                changes += 1

            # playlists: the most recently edited copy replaces the other
            local = {row[0]: (row[1], row[2]) for row in conn.execute('SELECT uid, id, updated_at FROM playlists')}
            for playlist in state.get('playlists') or []:
                if not isinstance(playlist, dict) or not playlist.get('uid'):
                    continue
                uid = str(playlist['uid'])[:64]
                updated_at = min(_number(playlist.get('updated_at')), now)
                if updated_at <= tombs.get(('playlist', uid), -1):
                    continue
                if uid in local and local[uid][1] >= updated_at:
                    continue
                name = str(playlist.get('name') or 'פלייליסט')[:100]
                if uid in local:
                    playlist_id = local[uid][0]
                    conn.execute('UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?', (name, updated_at, playlist_id))
                    conn.execute('DELETE FROM playlist_tracks WHERE playlist_id = ?', (playlist_id,))
                else:
                    playlist_id = conn.execute(
                        'INSERT INTO playlists (uid, name, created_at, updated_at) VALUES (?, ?, ?, ?)',
                        (uid, name, min(_number(playlist.get('created_at'), updated_at), now), updated_at)).lastrowid
                for position, track in enumerate(t for t in playlist.get('tracks') or [] if isinstance(t, dict)):
                    values = _song(track)
                    if values[0]:
                        conn.execute(f'INSERT INTO playlist_tracks (playlist_id, position, {_SONG_COLUMNS}, added_at) '
                                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (playlist_id, position, *values, updated_at))
                local[uid] = (playlist_id, updated_at)
                changes += 1

            # removals that happened after the item's last change
            for kind, key, deleted_at in incoming_tombs:
                if kind == 'like' and key in likes and deleted_at > likes[key]:
                    conn.execute('DELETE FROM likes WHERE video_id = ?', (key,))
                    del likes[key]
                    changes += 1
                elif kind == 'follow' and key in follows and deleted_at > follows[key]:
                    conn.execute('DELETE FROM follows WHERE browse_id = ?', (key,))
                    del follows[key]
                    changes += 1
                elif kind == 'playlist' and key in local and deleted_at > local[key][1]:
                    conn.execute('DELETE FROM playlist_tracks WHERE playlist_id = ?', (local[key][0],))
                    conn.execute('DELETE FROM playlists WHERE id = ?', (local[key][0],))
                    del local[key]
                    changes += 1
                keep_tombstone(kind, key, deleted_at)

            # listening history: add plays this device doesn't have yet
            for play in state.get('plays') or []:
                if not isinstance(play, dict):
                    continue
                values = _song(play)
                played_at = _number(play.get('played_at'))
                if not values[0] or not played_at:
                    continue
                exists = conn.execute('SELECT 1 FROM plays WHERE video_id = ? AND played_at BETWEEN ? AND ? LIMIT 1',
                                      (values[0], played_at - 1, played_at + 1)).fetchone()
                if not exists:
                    conn.execute(f'INSERT INTO plays ({_SONG_COLUMNS}, played_at, synced) VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
                                 (*values, played_at))

            conn.execute('DELETE FROM tombstones WHERE deleted_at < ?', (now - _TOMBSTONE_DAYS * 86400,))
        return changes

    def add_sync_device(self, token_hash, name):
        self._run('INSERT INTO sync_devices (token_hash, name, paired_at, last_seen) VALUES (?, ?, ?, ?)',
                  (token_hash, name, time.time(), time.time()))

    def sync_device(self, token_hash):
        return self._one('SELECT * FROM sync_devices WHERE token_hash = ?', (token_hash,))

    def touch_sync_device(self, device_id):
        self._run('UPDATE sync_devices SET last_seen = ? WHERE id = ?', (time.time(), device_id))

    def list_sync_devices(self):
        return self._all('SELECT id, name, paired_at, last_seen FROM sync_devices ORDER BY last_seen DESC')

    def remove_sync_device(self, device_id):
        return self._run('DELETE FROM sync_devices WHERE id = ?', (device_id,))

    # endregion

    # region filtered mode

    def filter_rule(self, kind, key):
        return self._one('SELECT * FROM filter_rules WHERE kind = ? AND key = ?', (kind, key))

    def set_filter_rule(self, kind, key, verdict, label=''):
        self._run('INSERT OR REPLACE INTO filter_rules (kind, key, verdict, label, created_at) VALUES (?, ?, ?, ?, ?)',
                  (kind, key, verdict, label[:300], time.time()))

    def remove_filter_rule(self, kind, key):
        return self._run('DELETE FROM filter_rules WHERE kind = ? AND key = ?', (kind, key))

    def list_filter_rules(self):
        return self._all('SELECT kind, key, verdict, label, created_at FROM filter_rules ORDER BY created_at DESC')

    def artist_gender(self, key):
        return self._one('SELECT * FROM artist_gender WHERE key = ?', (key,))

    def set_artist_gender(self, key, name, gender, source):
        self._run('INSERT OR REPLACE INTO artist_gender (key, name, gender, source, checked_at) VALUES (?, ?, ?, ?, ?)',
                  (key, name[:200], gender, source, time.time()))

    def unknown_artists(self, limit=100):
        return self._all("SELECT key, name, checked_at FROM artist_gender WHERE gender = 'unknown' "
                         'AND key NOT IN (SELECT key FROM filter_rules WHERE kind = \'artist\') '
                         'ORDER BY checked_at DESC LIMIT ?', (limit,))

    def song_info(self, video_id):
        """What the app knows about a song (title and artist) from the library, likes, playlists or history."""
        for table in ('tracks', 'likes', 'playlist_tracks', 'plays', 'jobs'):
            row = self._one(f'SELECT video_id AS videoId, title, artist FROM {table} WHERE video_id = ? LIMIT 1', (video_id,))
            if row:
                return row
        return None

    # endregion

    # region cache

    def cache_get(self, key):
        row = self._one('SELECT value, expires FROM cache WHERE key = ?', (key,))
        if not row or row['expires'] < time.time():
            return None
        return json.loads(row['value'])

    def cache_set(self, key, value, ttl):
        self._run('INSERT OR REPLACE INTO cache (key, value, expires) VALUES (?, ?, ?)',
                  (key, json.dumps(value, ensure_ascii=False), time.time() + ttl))

    # endregion


class _Transaction:
    """`with db._transaction() as conn:` runs the block in BEGIN/COMMIT while holding the database lock."""

    def __init__(self, db):
        self._db = db

    def __enter__(self):
        self._db._lock.acquire()
        self._db._conn.execute('BEGIN')
        return self._db._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self._db._conn.execute('ROLLBACK' if exc_type else 'COMMIT')
        finally:
            self._db._lock.release()
        return False
