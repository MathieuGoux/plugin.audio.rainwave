"""
Rainwave Kodi Addon - Now Playing Widget

This module provides the Widget class which manages the now-playing display by writing data to Window(10000) properties.

HOW THE DISPLAY SYSTEM WORKS:
==============================

1. The skin XML file (script-rainwave-nowplaying.xml) reads Window(10000) properties via $INFO[Window(10000).Property(...)] expressions.

2. This Widget class writes those properties based on API data from Rainwave.

3. The SyncQueue (sync_queue.py) ensures data is displayed at the RIGHT TIME to match the actual audio playback, compensating for the 15-20s stream buffer.

THE CRITICAL SEPARATION:
=======================

This module implements a deliberate separation between:

    - **refresh()**: Polls the API and updates "up next"/"previous" panels IMMEDIATELY
    - **apply_current()**: Applies the current song data (but only when it's due)

We do this because the current song metadata from the API is 15-20 seconds AHEAD of what the user is actually hearing (due to the stream buffer). If we displayed it immediately, the widget would show the wrong song.

Instead:
    1. refresh() runs every 5 seconds, gets fresh data from API
    2. It returns the current song data but DOESN'T display it
    3. The caller (service.py) passes this to SyncQueue
    4. SyncQueue holds it for ~15-20 seconds (configurable)
    5. When the time is right, SyncQueue calls apply_current()
    6. apply_current() writes the data to Window properties for display

This ensures the display stays in sync with the audio.

WHAT THIS MODULE HANDLES:
=========================

- Current song display (via apply_current, called by SyncQueue)
- Next song candidates display (rotated every 15 seconds)
- Previous song display (immediate)
- Double-buffered next candidate rotation (smooth transitions)
- Property clearing on deactivation

WHAT IT DOESN'T HANDLE:
======================

- The actual polling (done by service.py)
- The synchronization timing (done by SyncQueue)
- The dialog visibility (done by NowPlayingDialog)
"""

import xbmcgui

#==WIDGET CLASS================

class Widget:
    """
    Writes now-playing data to Home (10000) window properties.

    The now-playing skin XML reads these via $INFO[Window(10000).Property(...)] expressions. refresh() polls the API and updates the "up next" and "previous" panels immediately, but returns the current-song data rather than displaying it -- the caller runs that through SyncQueue and calls apply_current() once it's due, so the display stays in step with the (buffered) audio rather than jumping ahead of it.

    ATTRIBUTES:
        api: RainwaveAPI instance for fetching song data window: Window(10000) instance for property access
        _candidates: List of next song candidates from API
        _candidate_index: Current index in candidates list
        _candidate_key: Key for detecting when candidates change
        _active_slot: Currently visible slot ("A" or "B")
        _next_rotation_due: Timestamp when next rotation should happen

    DOUBLE-BUFFERING:
        Uses A/B slots for next candidate display. The skin has two overlapping groups, each bound to one slot via <visible> condition with a fade animation. Flipping which slot is visible triggers both fade-out of old and fade-in of new, since the new one's properties are already set.
    """
    
    # Property name prefix for all Window(10000) properties
    # All Rainwave-related properties start with "Rainwave." to avoid conflicts
    PREFIX = "Rainwave."
    
    # Property keys for current song display
    # These are used to write current song info to Window properties
    KEYS = ("Title", "Artist", "Album", "Art", "Station")
    
    # Property keys for song data (subset of KEYS, without Station)
    # Used for next and previous song displays
    SONG_KEYS = ("Title", "Artist", "Album", "Art")
    
    # How long each "up next" candidate stays on screen (seconds)
    # Candidates are rotated to show all upcoming songs in the election
    ROTATION_INTERVAL = 15  # seconds
    
    # Double-buffering slots for smooth candidate transitions
    # The skin has two overlapping groups (A and B) that fade between each other
    SLOTS = ("A", "B")

    def __init__(self, api):
        """
        Initialize widget with API client.

        ARGS:
            api (RainwaveAPI): Instance for fetching song data from Rainwave

        SETS UP:
            - Window(10000) for property access
            - Internal state for candidate rotation tracking
            - Initial slot and timing state
        """
        
        self.api = api
        self.window = xbmcgui.Window(10000)
        self._candidates = []
        self._candidate_index = -1
        self._candidate_key = None
        self._active_slot = "A"
        self._next_rotation_due = 0

    def apply_current(self, song):
        """
        Write the *currently playing* song's fields to Window(10000).

        SPLIT FROM refresh() FOR SYNCHRONIZATION:
            This method is deliberately separate from refresh() so the caller (service.py via SyncQueue) can hold a freshly polled snapshot back for a few seconds before it lands on screen.

            Without this separation, metadata would appear the instant the API reports it, which is 15-20 seconds before the user actually hears it. This would make the widget look broken (showing wrong song info).

        ARGS:
            song (dict or None): Song data from API with keys:
                - title: Song title
                - artist: Artist name
                - album: Album name
                - art: Album artwork URL
                - station: Station name

            If song is None, the method does nothing (no-op)
        """
        
        if song is None:
            return
        self.window.setProperty(self.PREFIX + "Title", song.get("title", ""))
        self.window.setProperty(self.PREFIX + "Artist", song.get("artist", ""))
        self.window.setProperty(self.PREFIX + "Album", song.get("album", ""))
        self.window.setProperty(self.PREFIX + "Art", song.get("art", ""))
        self.window.setProperty(self.PREFIX + "Station", song.get("station", ""))

    def refresh(self, sid=None):
        """
        Poll API for latest data and update non-current displays.

        THIS METHOD DOES NOT WRITE CURRENT SONG DATA:
            The current song fields (Title/Artist/Album/Art/Station) are NOT written here. They are handled by apply_current() which is called by the sync queue when the data is due.

            This method STILL runs on every 5-second poll because SyncQueue needs a fresh snapshot that often to keep its lag buffer accurate, but the *current song* fields are what's out of sync with the audio.

        WHAT THIS METHOD DOES:
            1. Fetches latest song data from API via get_now_playing()
            2. If API returns None (failure), returns None immediately
            3. Updates next candidates display (immediate, not sync-delayed)
            4. Updates previous song display (immediate)
            5. Returns the current song data for sync-delayed display

        WHY NEXT/PREVIOUS ARE IMMEDIATE:
            These panels describe songs that haven't played yet (next candidates) or have already finished (previous), so they don't need to be audio-synced in the same way. Only the CURRENT song needs the delay to match the audio.

        ARGS:
            sid (int or None): Station ID to poll. If None, uses API's current_sid.

        RETURNS:
            dict or None: Current song data for SyncQueue, or None if API failed
        """
        
        song = self.api.get_now_playing(sid)
        
        if song is None:
            return None

        candidates = song.get("next_candidates", [])
        key = tuple(c.get("title") for c in candidates)
        if key != self._candidate_key:
            self._candidate_key = key
            self._candidates = candidates
            self._candidate_index = -1
            if candidates:
                '''
                New election: show its first candidate right away rather than waiting out whatever's left of the previous election's rotation timer.
                '''
                
                self._next_rotation_due = 0
            else:
                '''
                No open election right now (between elections, a DJ set, etc) -- clear both slots so the panel goes blank instead of holding on to a candidate from an election that's already closed.
                '''
                
                for slot in self.SLOTS:
                    for k in self.SONG_KEYS:
                        self.window.clearProperty(self.PREFIX + f"Next{k}{slot}")
                self._next_rotation_due = float("inf")
        else:
            self._candidates = candidates

        '''
        "previous" is simply the last song that played -- already decided, so a single song is the right shape here (unlike next_candidates above). Comes back as {} (not missing keys) when unavailable, via api.py's _parse_song(), so .get(...) with a blank-string default is enough.
        '''
        
        previous_song = song.get("previous", {})
        for k in self.SONG_KEYS:
            self.window.setProperty(
                self.PREFIX + "Previous" + k, previous_song.get(k.lower(), "")
            )

        '''
        Timing fields (start_actual/length/server_time) aren't shown via $INFO like the rest -- the progress bar needs live per-second updates that a static window property can't give us, so the caller feeds these straight to the dialog object instead. Returning song here just avoids a second API call.
        '''
        
        return song

    def tick(self, now):
        """
        Called once a second from the main loop.

        Handles rotating through next song candidates every ROTATION_INTERVAL seconds using the double-buffered A/B slot technique.

        WHY DOUBLE-BUFFERING?
            The skin has two overlapping groups, each bound to one slot via <visible>String.IsEqual(...)</visible> with a fade <animation>.

        Example skin XML:
        
        <control type="group">
            <visible>String.IsEqual(Window(10000).Property(Rainwave.NextActive),A)</visible>
            <animation effect="fade" type="VisibleChange">...</animation>
            <!-- Properties: Rainwave.NextTitleA, Rainwave.NextArtistA, etc. -->
        </control>
        
        <control type="group">
            <visible>String.IsEqual(Window(10000).Property(Rainwave.NextActive),B)</visible>
            <animation effect="fade" type="VisibleChange">...</animation>
            <!-- Properties: Rainwave.NextTitleB, Rainwave.NextArtistB, etc. -->
        </control>

        When we flip Rainwave.NextActive from A to B:
            1. Group A (currently visible) starts fading out
            2. Group B (currently hidden) starts fading in
            3. Since Group B's properties were already set before the flip, there's no pop-in of blank/loading state

        ARGS:
            now (float): Current timestamp from time.time()
        """
        
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
        """
        Clear all widget properties from Window(10000).

        CALLED WHEN:
            - Playback stops
            - Addon is deactivated
            - Service stops

        This ensures no stale data remains on screen when the addon is not actively playing a Rainwave stream.

        RESETS:
            - All current song properties (Title, Artist, Album, Art, Station)
            - All next candidate properties for both slots (A and B)
            - NextActive property
            - All previous song properties
            - Internal state (candidates, index, key, slot, rotation timer)

        This provides a clean slate for the next playback session.
        """
        
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