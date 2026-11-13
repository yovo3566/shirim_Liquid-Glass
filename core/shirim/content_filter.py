"""Filtered mode: hides songs sung by women, explicit songs and every picture, everywhere the app shows music.

Whether a performer is a woman, a man or a group with a woman singing comes from, in order: the user's own
rules, the built-in lists, Wikidata, and clearly male or female given names. Performers that can't be
identified are hidden in strict mode (the default) until the user approves them.

The filter runs on every API response (server.Handler), so the desktop app, the phone app, iPhones and
Android Auto all see the same filtered catalogue. Stored data (likes, playlists, the library) is never
changed; hidden songs just aren't shown or played.
"""

import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait

import certifi

from . import names
from .filter_data import FEMALE_ARTISTS, FEMALE_FIRST_NAMES, MALE_ARTISTS, MALE_FIRST_NAMES

log = logging.getLogger(__name__)

FEMALE, MALE, MIXED, UNKNOWN = 'female', 'male', 'mixed', 'unknown'
HIDDEN = (FEMALE, MIXED)

_WIKIDATA = 'https://www.wikidata.org/w/api.php'
_USER_AGENT = 'Shirim/1.3 (personal music player; filtered mode)'
_SSL = ssl.create_default_context(cafile=certifi.where())
_WD_FEMALE = {'Q6581072', 'Q1052281'}  # female, trans woman
_WD_MALE = {'Q6581097', 'Q2449503'}  # male, trans man
_WD_HUMAN = 'Q5'
_WD_GROUPS = {'Q215380', 'Q5741069', 'Q2088357', 'Q9212979', 'Q216337', 'Q281643', 'Q56816954'}
_WD_MUSIC_JOBS = {'Q177220', 'Q488205', 'Q753110', 'Q639669', 'Q36834', 'Q2252262', 'Q183945', 'Q855091',
                  'Q1259917', 'Q2643890', 'Q806349', 'Q130857', 'Q1075651', 'Q386854', 'Q158852'}
_MUSIC_WORDS = ('singer', 'musician', 'rapper', 'band', 'group', 'duo', 'songwriter', 'vocalist', 'composer',
                'זמר', 'זמרת', 'להקה', 'מוזיקאי', 'ראפר')
_KNOWN_TTL = 180 * 86400
_UNKNOWN_TTL = 7 * 86400
_ERROR_TTL = 600  # Wikidata unreachable: try again in a few minutes
_LOOKUP_BUDGET = 3.0  # seconds a response may wait for performers being looked up; the rest continue in background
_TITLE_GUESTS = re.compile(
    r'[\(\[]\s*(?:feat\.?|ft\.?|featuring|עם|מארח(?:ים|ת)?(?:\s+את)?)\s+([^\)\]]+)[\)\]]|\s(?:feat\.|ft\.)\s+(.+)$',
    re.IGNORECASE)
# Words joining performers in a credit ("A & B", "A and B", "A x B", "A feat. B", "A עם B")
_JOINER = r'(?:&|and|x|with|feat\.?|ft\.?|featuring|עם|מארח(?:ים|ת)?(?:\s+את)?)'
_SPLIT_PERFORMERS = re.compile(rf'\s*,\s*|\s+{_JOINER}\s+', re.IGNORECASE)
_LEADING_JOINER = re.compile(rf'^{_JOINER}\s+', re.IGNORECASE)
_WIKIDATA_INTERVAL = 0.35  # Wikidata answers "too many requests" to faster clients
_WIKIDATA_ATTEMPTS = 3
_wikidata_lock = threading.Lock()
_wikidata_next = 0.0
_EXCLUDED_PREFIXES = ('/api/sync/', '/api/filter', '/api/settings', '/api/info', '/api/queue/', '/api/pin',
                      '/api/action', '/api/scan', '/api/cookies/', '/api/usb/', '/api/import/status')
_IMAGE_KEYS = ('thumb', 'art', 'cover')


class Hidden(Exception):
    """The requested artist or album is hidden in filtered mode."""


def artist_key(name):
    return re.sub(r'\s+', ' ', names.clean_artist(name or '')).strip().casefold()


def _recognized(name):
    key = artist_key(name)
    return key in FEMALE_ARTISTS or key in MALE_ARTISTS or _by_given_name(key) is not None


def _known_performer(name):
    key = artist_key(name)
    return key in FEMALE_ARTISTS or key in MALE_ARTISTS


def _split_hebrew_and(part):
    """"ישי ריבו ואלדד ציטרין" -> both names, and "ואמיר דדון" -> "אמיר דדון", when the names are recognizable.

    Names like "סטטיק ובן אל" stay whole: a split needs a recognizable first name, or a known performer after the ו.
    """
    if _known_performer(part):
        return [part]
    if part.startswith('ו') and not _recognized(part) and _recognized(part[1:]):
        part = part[1:]
    for match in re.finditer(r' ו(?=\S)', part):
        left, right = part[:match.start()], part[match.end():]
        if _recognized(left) or _known_performer(right):
            return [left, *_split_hebrew_and(right)]
    return [part]


def _split_credit(text):
    parts = []
    for part in _SPLIT_PERFORMERS.split(text or ''):
        part = names.clean_artist(_LEADING_JOINER.sub('', part.strip()))
        if part and not _LEADING_JOINER.fullmatch(part + ' '):
            parts.extend(_split_hebrew_and(part) if names.is_hebrew(part) else [part])
    return parts


def performers(artist, title=''):
    """Every performer credited on a song: all artists, joined names ("A & B"), and guests in the title."""
    found = _split_credit(artist)
    for match in _TITLE_GUESTS.finditer(title or ''):
        found.extend(_split_credit(match.group(1) or match.group(2) or ''))
    return list(dict.fromkeys(found))


def _by_given_name(key):
    first = key.split(' ')[0] if key else ''
    if first in FEMALE_FIRST_NAMES:
        return FEMALE
    if first in MALE_FIRST_NAMES and len(key.split(' ')) <= 3:
        return MALE
    return None


def _builtin(key):
    if key in FEMALE_ARTISTS:
        return FEMALE
    if key in MALE_ARTISTS:
        return MALE
    # "עומר אדם ונועה קירל": a Hebrew duet joined with ו, where either name gives it away
    for match in re.finditer(r' ו', key):
        left, right = key[:match.start()], key[match.end():]
        genders = {_builtin_single(left), _builtin_single(right)}
        if FEMALE in genders:
            return MIXED
        if genders == {MALE}:
            return MALE
    return None


def _builtin_single(key):
    if key in FEMALE_ARTISTS:
        return FEMALE
    if key in MALE_ARTISTS:
        return MALE
    return _by_given_name(key)


# region Wikidata

def _wikidata(params):
    """One Wikidata API call, spaced out and retried when Wikidata asks clients to slow down."""
    global _wikidata_next
    url = f'{_WIKIDATA}?{urllib.parse.urlencode({**params, "format": "json"})}'
    for attempt in range(_WIKIDATA_ATTEMPTS):
        with _wikidata_lock:
            delay = _wikidata_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            _wikidata_next = time.monotonic() + _WIKIDATA_INTERVAL
        request = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=8, context=_SSL) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == _WIKIDATA_ATTEMPTS - 1:
                raise
            retry_after = e.headers.get('Retry-After') or ''
            with _wikidata_lock:
                _wikidata_next = time.monotonic() + min(30, int(retry_after) if retry_after.isdigit() else 5 * (attempt + 1))


def _claim_ids(entity, prop):
    ids = set()
    for claim in entity.get('claims', {}).get(prop, []):
        value = claim.get('mainsnak', {}).get('datavalue', {}).get('value')
        if isinstance(value, dict) and value.get('id'):
            ids.add(value['id'])
    return ids


def _gender_of(entity):
    sex = _claim_ids(entity, 'P21')
    if sex & _WD_FEMALE:
        return FEMALE
    if sex & _WD_MALE:
        return MALE
    return None


def wikidata_gender(name):
    """FEMALE, MALE, MIXED (a group with women) or UNKNOWN, from Wikidata's entry for a performer."""
    language = 'he' if names.is_hebrew(name) else 'en'
    hits = _wikidata({'action': 'wbsearchentities', 'search': name, 'language': language, 'uselang': language,
                      'type': 'item', 'limit': 5}).get('search', [])
    if not hits:
        return UNKNOWN
    entities = _wikidata({'action': 'wbgetentities', 'ids': '|'.join(h['id'] for h in hits), 'props': 'claims|descriptions',
                          'languages': 'en|he'})['entities']
    for hit in hits:
        entity = entities.get(hit['id'], {})
        kinds = _claim_ids(entity, 'P31')
        description = ' '.join(d.get('value', '') for d in entity.get('descriptions', {}).values()).casefold()
        musical = bool(_claim_ids(entity, 'P106') & _WD_MUSIC_JOBS) or any(w in description for w in _MUSIC_WORDS)
        if _WD_HUMAN in kinds and musical:
            return _gender_of(entity) or UNKNOWN
        if kinds & _WD_GROUPS or (musical and any(w in description for w in ('band', 'group', 'duo', 'להקה'))):
            members = list(_claim_ids(entity, 'P527'))[:40]
            if not members:
                return UNKNOWN
            member_entities = _wikidata({'action': 'wbgetentities', 'ids': '|'.join(members), 'props': 'claims'})['entities']
            genders = {_gender_of(e) for e in member_entities.values()} - {None}
            if FEMALE in genders:
                return MIXED
            return MALE if genders == {MALE} else UNKNOWN
    return UNKNOWN

# endregion


class ContentFilter:
    def __init__(self, db, settings, host):
        self.db = db
        self.settings = settings
        self.host = host
        self._memory = {}
        self._pending = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='filter-lookup')

    # region state

    @property
    def forced(self):
        return bool(self.host.filter_forced)

    @property
    def active(self):
        return self.forced or bool(self.settings['filter_enabled'])

    @property
    def strict(self):
        return self.forced or self.settings['filter_unknown'] == 'hide'

    def status(self):
        with self._lock:
            checking = len(self._pending)
        return {
            'active': self.active,
            'forced': self.forced,
            'locked': bool(self.settings['filter_pin']),
            'strict': self.strict,
            'hideImages': self.active,
            'checking': checking,  # performers still being identified; hidden meanwhile in strict mode
        }

    # endregion

    # region performers

    def gender(self, name, lookup=True):
        """The performer's classification, without waiting for Wikidata unless lookup finishes it right away."""
        key = artist_key(name)
        if not key:
            return UNKNOWN
        rule = self.db.filter_rule('artist', key)
        if rule:
            return MALE if rule['verdict'] == 'allow' else FEMALE
        with self._lock:
            if key in self._memory:
                return self._memory[key]
        known = _builtin(key)
        if known is None:
            cached = self.db.artist_gender(key)
            if cached:
                ttl = _ERROR_TTL if cached['source'] == 'error' else _UNKNOWN_TTL if cached['gender'] == UNKNOWN else _KNOWN_TTL
                if time.time() - cached['checked_at'] < ttl:
                    known = cached['gender']
        if known is None:
            known = _by_given_name(key)
        if known is None:
            if lookup:
                self._lookup_later(name, key)
                return None  # still being looked up
            return UNKNOWN  # not remembered, so a later lookup can still identify the performer
        with self._lock:
            self._memory[key] = known
        return known

    def _lookup_later(self, name, key):
        with self._lock:
            if key in self._pending:
                return self._pending[key]
            future = self._pool.submit(self._lookup, name, key)
            self._pending[key] = future
            return future

    def _lookup(self, name, key):
        try:
            gender = wikidata_gender(names.clean_artist(name))
        except Exception as e:
            log.info('Wikidata lookup for %r failed: %s', name, e)
            # Remembered only briefly, so the performer is looked up again soon
            self.db.set_artist_gender(key, name, UNKNOWN, 'error')
            with self._lock:
                self._pending.pop(key, None)
            return UNKNOWN
        self.db.set_artist_gender(key, name, gender, 'wikidata')
        with self._lock:
            self._memory[key] = gender
            self._pending.pop(key, None)
        return gender

    def warm_up(self):
        """Identify the performers of the library, likes, playlists and history ahead of time, in the background."""
        if not self.active:
            return

        def run():
            credited = set()
            for row in self.db.list_tracks():
                credited.update(performers(row['artist'], row['title']))
            for row in self.db.list_likes() + self.db.recent_plays(300):
                credited.update(performers(row['artist'], row['title']))
            for follow in self.db.list_follows():
                credited.add(follow['name'])
            for name in credited:
                self.gender(name)  # queues lookups for the ones not known yet
            log.info('Filtered mode: checking %d performers', len(credited))

        threading.Thread(target=run, name='filter-warm-up', daemon=True).start()

    def classify(self, performer_names, budget=_LOOKUP_BUDGET):
        """Classify many performers, waiting up to budget seconds for the ones that need a lookup."""
        results, waiting = {}, {}
        for name in set(performer_names):
            gender = self.gender(name)
            if gender is None:
                waiting[name] = self._lookup_later(name, artist_key(name))
            else:
                results[name] = gender
        if waiting:
            wait(list(waiting.values()), timeout=budget)
            for name, future in waiting.items():
                results[name] = future.result() if future.done() else None
        return results

    def _visible(self, gender):
        if gender in HIDDEN:
            return False
        if gender == MALE:
            return True
        return not self.strict  # unknown, or still being looked up

    def allows_artist(self, name, classified=None):
        gender = classified.get(name) if classified is not None and name in classified else self.gender(name, lookup=False)
        return self._visible(gender)

    def allows_track(self, track, classified=None):
        video_id = track.get('videoId') or track.get('video_id') or ''
        rule = self.db.filter_rule('song', video_id) if video_id else None
        if rule:
            return rule['verdict'] == 'allow'
        if track.get('explicit'):
            return False
        credited = performers(track.get('artist') or '', track.get('title') or '')
        if not credited:
            return not self.strict
        classified = classified if classified is not None else self.classify(credited)
        return all(self._visible(classified.get(name)) for name in credited)

    # endregion

    # region API responses

    def applies_to(self, path):
        return self.active and path.startswith('/api/') and not path.startswith(_EXCLUDED_PREFIXES)

    def _collect(self, value, found):
        if isinstance(value, dict):
            if value.get('videoId') or value.get('video_id'):
                found.update(performers(value.get('artist') or '', value.get('title') or ''))
            elif value.get('browseId') and value.get('name'):
                found.add(value['name'])
            elif value.get('browseId') and value.get('artist'):
                found.update(performers(value['artist']))
            if str(value.get('id') or '').startswith('artist:'):
                found.update(self._mix_artist_names(value))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    self._collect(child, found)
        elif isinstance(value, list):
            for item in value:
                self._collect(item, found)

    def _mix_artist_names(self, mix):
        browse_id = str(mix['id']).split(':', 1)[1]
        aliases = self.db.aliases_for_browse_id(browse_id)
        return {aliases[0]['canonical']} if aliases else set()

    def _keep(self, item, classified):
        if not isinstance(item, dict):
            return True
        if item.get('videoId') or item.get('video_id'):
            return self.allows_track(item, classified)
        if item.get('browseId') and item.get('name'):
            return self.allows_artist(item['name'], classified)
        if item.get('browseId') and item.get('artist'):
            return all(self.allows_artist(n, classified) for n in performers(item['artist']))
        if str(item.get('id') or '').startswith('artist:'):
            return all(self.allows_artist(n, classified) for n in self._mix_artist_names(item))
        return True

    def _clean(self, value, classified):
        if isinstance(value, list):
            return [self._clean(item, classified) for item in value if self._keep(item, classified)]
        if isinstance(value, dict):
            cleaned = {}
            for key, child in value.items():
                if key in _IMAGE_KEYS and isinstance(child, str):
                    cleaned[key] = ''
                elif key == 'thumbs' and isinstance(child, list):
                    cleaned[key] = []
                elif isinstance(child, dict) and not self._keep(child, classified):
                    cleaned[key] = None
                else:
                    cleaned[key] = self._clean(child, classified)
            return cleaned
        return value

    def filter_payload(self, data):
        """Remove hidden songs, performers and albums, and every picture, from an API response.

        Raises Hidden when the response itself is the page of a hidden artist or album.
        """
        found = set()
        self._collect(data, found)
        classified = self.classify(found) if found else {}
        if isinstance(data, dict) and not (data.get('videoId') or data.get('video_id')) and not self._keep(data, classified):
            raise Hidden()
        return self._clean(data, classified)

    # endregion

    def song_allowed_by_id(self, video_id):
        """For requests that only carry a song id (playing, downloading): check what the app knows about it."""
        rule = self.db.filter_rule('song', video_id)
        if rule:
            return rule['verdict'] == 'allow'
        song = self.db.song_info(video_id)
        return self.allows_track(song) if song else True

    def filter_tracks(self, tracks):
        credited = set()
        for track in tracks:
            credited.update(performers(track.get('artist') or '', track.get('title') or ''))
        classified = self.classify(credited) if credited else {}
        return [t for t in tracks if self.allows_track(t, classified)]
