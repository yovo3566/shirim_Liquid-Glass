"""Tidy an existing Shirim library: one folder per artist and no duplicate songs.

- Spellings of the same artist ("ישי ריבו - Ishay Ribo", "Ishay Ribo", "Yishai Ribo") are merged, matched
  through the YouTube Music artist they resolve to. Hebrew spellings are preferred.
- Copies of one recording (same artist and title, length within a few seconds) are moved out of the music
  folder into a duplicates folder; the best copy stays (album release first).
- Kept files are renamed "Artist - Title" and moved into their artist's folder; tags and the database follow.

Close the app first. Without --apply nothing is changed and the plan is printed.
"""

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core'))

from shirim import names, search, tagging  # noqa: E402
from shirim.db import Database  # noqa: E402
from shirim.downloader import safe_name  # noqa: E402
from shirim.settings import Settings  # noqa: E402

_SUFFIXED = re.compile(r' \([\w-]{11}\)\.\w+$')


def _lookup(name):
    try:
        return search.artist_id(name)
    except Exception:
        return None


def resolve_artists(lead_counts):
    """Canonical spelling for every lead-artist name, plus the YouTube Music id each name resolved to."""
    ordered = sorted(lead_counts)
    with ThreadPoolExecutor(max_workers=6) as pool:
        browse_ids = dict(zip(ordered, pool.map(_lookup, ordered)))
    groups = collections.defaultdict(list)
    for name, browse_id in browse_ids.items():
        groups[browse_id or f'name:{name}'].append(name)
    canonical = {}
    for variants in groups.values():
        preferred = [v for v in variants if names.is_hebrew(v)] or variants
        chosen = max(preferred, key=lambda v: (lead_counts[v], v))
        for variant in variants:
            canonical[variant] = chosen
    return canonical, browse_ids, [g for g in groups.values() if len(g) > 1]


def cluster_by_duration(tracks):
    clusters = []
    for track in sorted(tracks, key=lambda t: t['duration'] or 0):
        for cluster in clusters:
            if names.same_recording(cluster[0]['duration'], track['duration']):
                cluster.append(track)
                break
        else:
            clusters.append([track])
    return clusters


def keep_score(track, key):
    album_key = names.title_key(track['album'])
    return (bool(album_key) and album_key != key, not _SUFFIXED.search(track['path']), -track['added_at'])


def unique_path(path, taken):
    stem, ext = os.path.splitext(path)
    candidate, n = path, 2
    while candidate.lower() in taken or os.path.exists(candidate):
        candidate = f'{stem} ({n}){ext}'
        n += 1
    taken.add(candidate.lower())
    return candidate


def plan(db, settings):
    # Only songs the app downloaded; music found elsewhere on the device stays where the user keeps it
    tracks = [t for t in db.list_tracks() if t.get('source', 'download') != 'local']
    present = [t for t in tracks if os.path.isfile(t['path'])]
    known = {names.lead_artist(t['artist']) for t in present}
    raw_leads = {t['video_id']: names.lead_artist(t['artist'], known) for t in present}
    lead_counts = collections.Counter(name for name in raw_leads.values() if name)
    canonical, browse_ids, merged = resolve_artists(lead_counts)

    songs = collections.defaultdict(list)
    credits = {}
    for track in present:
        raw = raw_leads[track['video_id']]
        lead = canonical.get(raw, raw)
        key = names.title_key(track['title'])
        credits[track['video_id']] = (lead, names.credit(track['artist'], raw, lead), key)
        songs[lead, key].append(track)

    duplicates, kept = [], []
    for (lead, key), group in songs.items():
        clusters = cluster_by_duration(group)
        clusters.sort(key=lambda c: (-len(c), min(t['added_at'] for t in c)))
        for index, cluster in enumerate(clusters):
            best = max(cluster, key=lambda t: keep_score(t, key))
            duplicates.extend((t, best) for t in cluster if t is not best)
            kept.append((best, index, lead, key))

    music_dir = settings['music_dir']
    taken = set()
    moves = []
    for best, index, lead, key in sorted(kept, key=lambda k: (k[2], k[3], k[1])):
        folder = os.path.join(music_dir, safe_name(lead)) if settings['folder_per_artist'] and lead else music_dir
        label = f"{lead} - {best['title']}" if lead else best['title']
        if index:
            album_key = names.title_key(best['album'])
            label += f" ({best['album']})" if album_key and album_key != key else f' ({index + 1})'
        ext = os.path.splitext(best['path'])[1]
        target = os.path.join(folder, safe_name(label) + ext)
        stem = os.path.splitext(target)[0]
        n = 2
        while target.lower() in taken:
            target = f'{stem} ({n}){ext}'
            n += 1
        taken.add(target.lower())
        moves.append((best, target))

    return {
        'tracks': tracks, 'missing': len(tracks) - len(present), 'lead_counts': lead_counts,
        'canonical': canonical, 'browse_ids': browse_ids, 'merged': merged,
        'credits': credits, 'duplicates': duplicates, 'moves': moves,
    }


def describe(result, duplicates_dir):
    print(f"Songs in library: {len(result['tracks'])} (files missing: {result['missing']})")
    print(f"\nArtist spellings merged ({len(result['merged'])} artists):")
    for variants in sorted(result['merged'], key=lambda g: -sum(result['lead_counts'][v] for v in g)):
        chosen = result['canonical'][variants[0]]
        print('  ' + ' + '.join(f"{v} ({result['lead_counts'][v]})" for v in variants) + f'  ->  {chosen}')
    size = sum(t['size'] for t, _ in result['duplicates'])
    print(f"\nDuplicate copies to move to {duplicates_dir}: {len(result['duplicates'])} ({size / 1e9:.2f} GB)")
    renamed = sum(1 for t, target in result['moves'] if t['path'] != target)
    print(f"Songs kept: {len(result['moves'])}, renamed or moved: {renamed}")
    folders = {os.path.dirname(target) for _, target in result['moves']}
    print(f'Artist folders after cleanup: {len(folders)}')
    print('\nExamples (kept <- moved aside):')
    for track, best in result['duplicates'][:12]:
        print(f"  {best['title']} [{best['album'] or '-'}, {best['duration']}s]  <-  "
              f"{track['title']} [{track['album'] or '-'}, {track['duration']}s]")


def apply(db, result, data_dir, duplicates_dir, music_dir):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    manifest_path = os.path.join(data_dir, f'cleanup-{stamp}.json')
    manifest = {'duplicates': [], 'moved': []}

    def save_manifest():
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)

    try:
        taken = set()
        for track, best in result['duplicates']:
            lead = result['credits'][track['video_id']][0]
            dest = unique_path(os.path.join(duplicates_dir, safe_name(lead or 'unknown'),
                                            os.path.basename(track['path'])), taken)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(track['path'], dest)
            db.delete_track(track['video_id'])
            manifest['duplicates'].append({'video_id': track['video_id'], 'from': track['path'], 'to': dest,
                                           'kept': best['path']})

        # Two passes through temporary names, so swapped names and case-only renames can't collide
        staged = []
        for track, target in result['moves']:
            if track['path'] != target:
                temp = track['path'] + '.shirim-move'
                os.replace(track['path'], temp)
                staged.append((track, temp, target))
        for track, temp, target in staged:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(temp, target)
            manifest['moved'].append({'video_id': track['video_id'], 'from': track['path'], 'to': target})

        updates = []
        for track, target in result['moves']:
            lead, credit, key = result['credits'][track['video_id']]
            if credit != track['artist'] or lead != track['lead_artist']:
                try:
                    tagging.update_artists(target, credit, lead)
                except Exception as e:
                    print(f'  tag update failed for {target}: {e}')
            updates.append((track['video_id'], {'path': target, 'artist': credit, 'lead_artist': lead,
                                                'title_key': key, 'size': os.path.getsize(target)}))
        db.update_tracks(updates)

        for name, chosen in result['canonical'].items():
            db.set_alias(name, result['browse_ids'].get(name), chosen)

        for dirpath, _, _ in os.walk(music_dir, topdown=False):
            if dirpath != music_dir and not os.listdir(dirpath):
                os.rmdir(dirpath)
    finally:
        save_manifest()
    print(f'\nDone. Record of every move: {manifest_path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='make the changes (default: only show the plan)')
    parser.add_argument('--data-dir', default=os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Shirim'))
    parser.add_argument('--duplicates-dir', default=os.path.join(os.path.expanduser('~'), 'Shirim-duplicates'))
    args = parser.parse_args()

    settings = Settings(os.path.join(args.data_dir, 'settings.json'))
    db_path = os.path.join(args.data_dir, 'library.db')
    if not os.path.isfile(db_path):
        sys.exit(f'No library at {db_path}')

    if args.apply:
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = os.path.join(args.data_dir, f'library-backup-{stamp}.db')
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(backup)
        source.backup(target)
        target.close()
        source.close()
        print(f'Database backed up to {backup}')
        db = Database(db_path)
    else:
        # Work on a copy: opening the real database would already run schema migrations
        copy = os.path.join(tempfile.mkdtemp(prefix='shirim-cleanup-'), 'library.db')
        source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        target = sqlite3.connect(copy)
        source.backup(target)
        target.close()
        source.close()
        db = Database(copy)

    result = plan(db, settings)
    describe(result, args.duplicates_dir)
    if args.apply:
        apply(db, result, args.data_dir, args.duplicates_dir, settings['music_dir'])
    else:
        print('\nNothing was changed (dry run). Run with --apply to make these changes.')


if __name__ == '__main__':
    main()
