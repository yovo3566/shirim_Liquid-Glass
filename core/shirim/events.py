"""Change counter that lets HTTP clients long-poll for updates."""

import threading


class Events:
    def __init__(self):
        self._cond = threading.Condition()
        self.version = 0

    def bump(self):
        with self._cond:
            self.version += 1
            self._cond.notify_all()

    def wait(self, since, timeout=25.0):
        """Block until the version differs from `since` (or timeout) and return the current version."""
        with self._cond:
            self._cond.wait_for(lambda: self.version != since, timeout)
            return self.version
