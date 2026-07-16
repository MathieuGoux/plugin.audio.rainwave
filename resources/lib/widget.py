import xbmcgui


class Widget:
    """Writes now-playing data to Home (10000) window properties.

    The now-playing skin XML reads these via $INFO[Window(10000)
    .Property(...)] expressions. refresh() polls the API and updates
    the "up next"/"previous" panels immediately, but returns the
    current-song data rather than displaying it -- the caller runs
    that through SyncQueue and calls apply_current() once it's due,
    so the display stays in step with the (buffered) audio rather
    than jumping ahead of it.
    """

    PREFIX = "Rainwave."
    KEYS = ("Title", "Artist", "Album", "Art", "Station")
    SONG_KEYS = ("Title", "Artist", "Album", "Art")
    ROTATION_INTERVAL = 15  # seconds each "up next" candidate stays on screen
    SLOTS = ("A", "B")

    def __init__(self, api):
        self.api = api
        self.window = xbmcgui.Window(10000)
        self._candidates = []
        self._candidate_index = -1
        self._candidate_key = None
        self._active_slot = "A"
        self._next_rotation_due = 0

    def apply_current(self, song):
        """Write the *currently playing* song's fields to Window(10000).

        Split out from refresh() so the caller can hold a freshly
        polled snapshot back for a few seconds (via sync_queue.py)
        before it lands on screen, instead of it appearing the instant
        the API reports it -- see SyncQueue's docstring for why.
        """
        if song is None:
            return
        self.window.setProperty(self.PREFIX + "Title", song.get("title", ""))
        self.window.setProperty(self.PREFIX + "Artist", song.get("artist", ""))
        self.window.setProperty(self.PREFIX + "Album", song.get("album", ""))
        self.window.setProperty(self.PREFIX + "Art", song.get("art", ""))
        self.window.setProperty(self.PREFIX + "Station", song.get("station", ""))

    def refresh(self, sid=None):
        song = self.api.get_now_playing(sid)

        # None means "no usable data this cycle" (e.g. a session still
        # bootstrapping) -- leave whatever's already on screen alone
        # rather than blanking it out.
        if song is None:
            return None

        # Deliberately NOT writing Title/Artist/Album/Art/Station here
        # anymore -- that's now apply_current()'s job. This method
        # still runs on every 5-second poll (sync_queue.py needs a
        # fresh snapshot that often to keep its lag buffer accurate),
        # but the *current song* fields are what's out of sync with
        # the audio, so the caller stages them through SyncQueue and
        # only calls apply_current() once the configured buffer delay
        # has elapsed. The "up next"/"previous" panels below aren't
        # audio-synced in the same sense (they describe songs that
        # haven't played yet, or already finished), so those stay
        # immediate.

        # The next election is still open for voting and Rainwave
        # doesn't expose live vote counts through this endpoint (see
        # api.py), so there's no reliable single "leader" to show.
        # Instead of picking one, rotate through every candidate --
        # actually advancing which one is displayed is tick()'s job
        # (called every second from the main loop), not this method's:
        # refresh() only runs once per 5-second data poll, which is
        # much faster than we actually want candidates to change, so
        # the two are deliberately decoupled. This just keeps the
        # candidate list itself current and detects when a new
        # election has started.
        candidates = song.get("next_candidates", [])
        key = tuple(c.get("title") for c in candidates)
        if key != self._candidate_key:
            self._candidate_key = key
            self._candidates = candidates
            self._candidate_index = -1
            if candidates:
                # New election: show its first candidate right away
                # rather than waiting out whatever's left of the
                # previous election's rotation timer.
                self._next_rotation_due = 0
            else:
                # No open election right now (between elections, a DJ
                # set, etc) -- clear both slots so the panel goes
                # blank instead of holding on to a candidate from an
                # election that's already closed.
                for slot in self.SLOTS:
                    for k in self.SONG_KEYS:
                        self.window.clearProperty(self.PREFIX + f"Next{k}{slot}")
                self._next_rotation_due = float("inf")
        else:
            self._candidates = candidates

        # "previous" is simply the last song that played -- already
        # decided, so a single song is the right shape here (unlike
        # next_candidates above). Comes back as {} (not missing keys)
        # when unavailable, via api.py's _parse_song(), so .get(...)
        # with a blank-string default is enough.
        previous_song = song.get("previous", {})
        for k in self.SONG_KEYS:
            self.window.setProperty(
                self.PREFIX + "Previous" + k, previous_song.get(k.lower(), "")
            )

        # Timing fields (start_actual/length/server_time) aren't shown
        # via $INFO like the rest -- the progress bar needs live
        # per-second updates that a static window property can't give
        # us, so the caller feeds these straight to the dialog object
        # instead. Returning song here just avoids a second API call.
        return song

    def tick(self, now):
        # Called once a second from the main loop (matching how
        # Slideshow.tick() already works), independent of the 5-second
        # data poll. Every ROTATION_INTERVAL seconds, swaps which
        # candidate is displayed in "up next".
        #
        # Uses the same double-buffered A/B slot technique as the
        # picture slideshow: write the new candidate into whichever
        # slot ISN'T currently visible, then flip Rainwave.NextActive
        # to it. The skin has two overlapping groups, each bound to
        # one slot via <visible>String.IsEqual(...)</visible> with a
        # fade <animation effect="fade">VisibleChange</animation> --
        # flipping which one is visible triggers both the fade-out of
        # the old candidate and fade-in of the new one, since the new
        # one's properties are already populated by the time it
        # becomes visible (no pop-in of a blank/loading state).
        if not self._candidates or now < self._next_rotation_due:
            return

        self._candidate_index = (self._candidate_index + 1) % len(self._candidates)
        current = self._candidates[self._candidate_index]

        inactive_slot = "B" if self._active_slot == "A" else "A"
        for k in self.SONG_KEYS:
            self.window.setProperty(
                self.PREFIX + f"Next{k}{inactive_slot}", current.get(k.lower(), "")
            )

        self._active_slot = inactive_slot
        self.window.setProperty(self.PREFIX + "NextActive", inactive_slot)
        self._next_rotation_due = now + self.ROTATION_INTERVAL

    def clear(self):
        for key in self.KEYS:
            self.window.clearProperty(self.PREFIX + key)
        for slot in self.SLOTS:
            for k in self.SONG_KEYS:
                self.window.clearProperty(self.PREFIX + f"Next{k}{slot}")
        self.window.clearProperty(self.PREFIX + "NextActive")
        for k in self.SONG_KEYS:
            self.window.clearProperty(self.PREFIX + "Previous" + k)
        self._candidates = []
        self._candidate_index = -1
        self._candidate_key = None
        self._active_slot = "A"
        self._next_rotation_due = 0
