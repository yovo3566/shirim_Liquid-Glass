"""One canonical name per performer, so "Ben Zur" and "בן צור" end up in the same folder.

Name variants are linked through the YouTube Music artist they resolve to; the first spelling stored for
an artist wins (the library cleanup script seeds Hebrew spellings for existing artists).
"""

import logging
import threading

from . import names, search

log = logging.getLogger(__name__)


class ArtistNames:
    def __init__(self, db, lookup=search.artist_id):
        self.db = db
        self._lookup = lookup
        self._lock = threading.Lock()
        self._cache = {}

    def canonical(self, name):
        name = names.clean_artist(name)
        if not name:
            return ''
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            row = self.db.get_alias(name)
            if row:
                self._cache[name] = row['canonical']
                return row['canonical']

        # Look up outside the lock so downloads of other artists aren't held up by the network
        try:
            browse_id = self._lookup(name)
        except Exception as e:
            log.info('Artist lookup for %r failed: %s', name, e)
            return name

        with self._lock:
            canonical = name
            if browse_id:
                known = self.db.aliases_for_browse_id(browse_id)
                if known:
                    canonical = known[0]['canonical']
            self.db.set_alias(name, browse_id, canonical)
            self._cache[name] = canonical
            return canonical
