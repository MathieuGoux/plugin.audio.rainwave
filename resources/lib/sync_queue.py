import xbmcaddon


class SyncQueue:
    """Delays applying freshly-polled 'now playing' data so it reaches
    the screen roughly when the matching audio reaches the listener's
    ears, instead of the moment Rainwave's API reports it.

    The relay stream (relay.rainwave.cc) carries its own upstream
    buffer -- observed as a fairly constant 15-20s of lag between "the
    API says this song is now playing" and "this song is actually
    audible." That's a property of the stream itself, not of Kodi's
    local caching, so it can't be shortened here; the best we can do
    is delay the *display* (title/artist/art/progress bar) by the
    same amount, so the two stay in step with each other.

    This works as a small lag buffer rather than a one-shot timer:
    every polled snapshot is stamped with the time it was fetched and
    kept around; poll() always looks for the newest snapshot that is
    at least `offset` seconds old and hands that one back (once) when
    it's new. That -- rather than scheduling a single "apply in N
    seconds" callback per poll -- is what keeps behaviour correct even
    though refresh() runs every 5 seconds while the delay is 15-20s:
    several snapshots are always in flight at once, and we always want
    whichever one is now exactly "offset" seconds stale, not whichever
    one was pushed first.
    """

    # How far back a snapshot could possibly still be waiting to be
    # applied: the configured offset itself, plus a little slack for
    # scheduling jitter (the main loop's tick granularity, a slow
    # poll, etc). Anything older than that is pure history and would
    # never be picked by poll() anyway, so it's dropped to keep the
    # list from growing for the lifetime of a session.
    TRIM_SLACK = 30

    def __init__(self):
        self.offset = 0
        self._history = []  # [(fetched_at, song), ...] oldest first
        self._applied_key = None
        self.reload_settings()

    def reload_settings(self):
        addon = xbmcaddon.Addon()
        enabled = addon.getSettingBool("stream_sync_enabled")
        # Sync "disabled" is just offset=0: poll() then always finds
        # the newest snapshot immediately eligible, which reproduces
        # the old apply-as-soon-as-polled behaviour without a second
        # code path to maintain.
        self.offset = max(0, addon.getSettingInt("stream_sync_offset")) if enabled else 0

    def push(self, song, now):
        """Record a freshly-polled snapshot. song may be None (the API
        call failed this cycle, see api.py/widget.py) -- nothing
        usable to queue, so just skip it.
        """
        if song is None:
            return
        self._history.append((now, song))
        cutoff = now - self.offset - self.TRIM_SLACK
        self._history = [(t, s) for (t, s) in self._history if t >= cutoff]

    def poll(self, now):
        """Return the song that should be on screen right now, or
        None if there's nothing new to apply -- either no snapshot
        has aged past the offset yet, or the one that has is already
        what's currently displayed (repeatedly true between song
        changes, since refresh() polls far more often than songs
        change).
        """
        target = now - self.offset
        candidate = None
        for fetched_at, song in self._history:
            if fetched_at <= target:
                candidate = song
            else:
                # _history is append-ordered (oldest first), so once
                # we hit one that's not old enough yet, nothing after
                # it can be either.
                break

        if candidate is None:
            return None

        key = (candidate.get("title"), candidate.get("artist"), candidate.get("album"))
        if key == self._applied_key:
            return None

        self._applied_key = key
        return candidate

    def reset(self):
        """Drop all queued state. Called on (re)activation so a fresh
        tune-in doesn't get its first display blocked by leftover
        history from a previous session, and on deactivation so
        nothing stale is waiting to fire if playback resumes later.
        """
        self._history = []
        self._applied_key = None
