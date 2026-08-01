"""
Rainwave Kodi Addon - Synchronization Queue

This module provides the SyncQueue class which implements a lag buffer to synchronize metadata display with audio playback.

THE FUNDAMENTAL PROBLEM:
=======================

Rainwave's relay stream (relay.rainwave.cc) carries its own upstream buffer. This creates a 15-20 second lag between:

    1. "The API says this song is now playing" (at time T)
    2. "This song is actually audible to the listener" (at time T + 15-20s)

This is a property of the STREAM ITSELF, not of Kodi's local caching. Therefore, it cannot be shortened or eliminated. The best we can do is delay the DISPLAY (title/artist/art/progress bar) by the same amount, so the display stays in sync with what the user is actually hearing.

THE SOLUTION:
=============

The SyncQueue works as a SMALL LAG BUFFER rather than a one-shot timer:

    1. Every polled snapshot from the API is stamped with the time it was fetched
    2. The snapshot is stored in a history list: [(timestamp, song), ...]
    3. poll() looks for the NEWEST snapshot that is at least 'offset' seconds old
    4. When found, it returns that snapshot for display
    5. Deduplication ensures the same song isn't displayed repeatedly

WHY THIS APPROACH?
==================

Consider the timing:
    - refresh() runs every 5 seconds (POLL_INTERVAL)
    - The buffer delay is 15-20 seconds (offset)
    - Several snapshots are always "in flight" at once

If we used a simple "schedule a callback in N seconds" approach:
    - We'd schedule one callback per poll
    - But by the time that callback fires, there might be a newer snapshot
    - We'd be displaying stale data

Instead, by keeping ALL recent snapshots and always picking the one that's EXACTLY "offset" seconds stale, we ensure:
    - The display updates at the right time
    - We always show the most recent appropriate data
    - We handle the case where refresh() runs less frequently than the offset

This is what keeps behavior correct even though the polling interval (5s) is shorter than the buffer delay (15-20s).
"""

import xbmcaddon

#==CONSTANTS================

class SyncQueue:
    """
    Delays applying freshly-polled 'now playing' data so it reaches the screen roughly when the matching audio reaches the listener's ears.

    This class is the CORE of the addon's synchronization system. Without it, the display would show song metadata 15-20 seconds BEFORE the user actually hears that song, which would be confusing and jarring.

    ATTRIBUTES:
        offset (int): Buffer delay in seconds (loaded from settings)
        _history (list): List of (timestamp, song) tuples, oldest first
        _applied_key (tuple or None): Key of last applied song for deduplication

    THE KEY INSIGHT:
    ===============

    The relay stream's buffer is FIXED at ~15-20 seconds. This means:
        - When the API reports "Song A is now playing", Song A won't be audible for another 15-20 seconds
        - When Song A DOES become audible, the API will already be reporting "Song B is now playing"

    If we displayed metadata immediately from the API, we'd show:
        - Song A (while user hears nothing or the end of previous song)
        - Song B (while user hears Song A)
        - Song C (while user hears Song B)

    This would be completely wrong.

    THE SYNC QUEUE SOLUTION:
    ======================

    1. Store each API response with its fetch timestamp
    2. When poll() is called, calculate: target_time = now - offset
    3. Find the newest snapshot with timestamp <= target_time
    4. If it's different from what's currently displayed, return it
    5. Otherwise return None (nothing new to display)

    This ensures that when Song A becomes audible, we display Song A's metadata, not Song B's.

    WHY NOT JUST WAIT 'OFFSET' SECONDS?
    ==================================

    Because refresh() runs every 5 seconds, but offset is 15-20 seconds. If we waited 15 seconds after each poll, we'd:
        - Miss updates (only check every 15s instead of every 5s)
        - Still have the same problem of showing the wrong song

    By storing multiple snapshots and always picking the one that's EXACTLY the right age, we get smooth, accurate synchronization.
    """
    
    '''
    How far back a snapshot could possibly still be waiting to be applied: the configured offset itself, plus a little slack for scheduling jitter (the main loop's tick granularity, a slow poll, etc).
    
    Anything older than (offset + TRIM_SLACK) is pure history and would never be picked by poll() anyway, so it's dropped to keep the list from growing unbounded for the lifetime of a session.
    
    Example: If offset=15 and TRIM_SLACK=30, we keep snapshots from the last 45 seconds. Older snapshots are automatically trimmed.
    '''
    
    TRIM_SLACK = 30

    def __init__(self):
        """
        Initialize the sync queue.

        Sets up:
            - offset: Buffer delay from settings (loaded via reload_settings)
            - _history: Empty list to store (timestamp, song) tuples
            - _applied_key: None (no song has been applied yet)

        The _history list stores tuples of (fetched_at, song) where:
            - fetched_at: float timestamp from time.time() when snapshot was polled
            - song: dict containing song data from API

        The list is maintained in chronological order (oldest first) for efficient polling.
        """
        
        self.offset = 0
        self._history = []  # [(fetched_at, song), ...] oldest first
        self._applied_key = None
        self.reload_settings()

    def reload_settings(self):
        """
        Reload synchronization settings from addon configuration.

        Reads from Kodi's addon settings:
            - stream_sync_enabled: Whether synchronization is active (bool)
            - stream_sync_offset: Buffer delay in seconds (int, 0-60)

        WHEN DISABLED:
            If stream_sync_enabled is False, offset is set to 0. This makes poll() always find the newest snapshot immediately eligible, which reproduces the OLD behavior (apply as soon as polled) without requiring a separate code path.

        WHEN ENABLED:
            offset is set to the configured value (stream_sync_offset), which delays display by that many seconds to match the audio.

        This method is called:
            - On initialization
            - Whenever addon settings change (via SettingsMonitor in service.py)
        """
        
        addon = xbmcaddon.Addon()
        enabled = addon.getSettingBool("stream_sync_enabled")
        self.offset = max(0, addon.getSettingInt("stream_sync_offset")) if enabled else 0

    def push(self, song, now):
        """
        Record a freshly-polled snapshot for later display.

        ARGS:
            song (dict or None): Song data from API, or None if API call failed 
            now (float): Current timestamp from time.time()

        BEHAVIOR:
            - If song is None (API failure), the snapshot is SKIPPED entirely because there's nothing usable to queue
            - Otherwise, the (now, song) tuple is appended to _history
            - Old snapshots (older than now - offset - TRIM_SLACK) are trimmed

        TRIMMING:
            The trimming logic ensures the history list doesn't grow unbounded. Snapshots older than (offset + TRIM_SLACK) seconds are removed because they would never be picked by poll() anyway (they're too old).

            Example: If offset=15 and TRIM_SLACK=30, at time T=100:
                - We keep snapshots with timestamps >= 100 - 15 - 30 = 55
                - Snapshots older than 55 seconds are removed
        """
        
        if song is None:
            return
        self._history.append((now, song))
        cutoff = now - self.offset - self.TRIM_SLACK
        self._history = [(t, s) for (t, s) in self._history if t >= cutoff]

    def poll(self, now):
        """
        Return the song that should be displayed at this moment.

        This is the CORE METHOD of the SyncQueue. It determines which polled snapshot (if any) should be displayed right now.

        ARGS:
            now (float): Current timestamp from time.time()

        RETURNS:
            dict or None:
                - The song dict to display, if there's a new snapshot that's old enough to match the audio
                - None, if:
                  a) No snapshot has aged past the offset yet, OR
                  b) The snapshot that has aged is already what's displayed

        HOW IT WORKS:
            1. Calculate target time: now - offset
               This is the timestamp that a snapshot would need to have been fetched at in order to be "ready" to display now.

            2. Iterate through _history (oldest first):
               - For each (fetched_at, song) tuple:
               - If fetched_at <= target_time: it's old enough, remember it
               - Else: it's too new, and since _history is ordered, we can stop

            3. If we found a candidate:
               - Check if it's different from the last applied song (deduplication)
               - If different: update _applied_key and return it
               - If same: return None (already displayed)

            4. If no candidate found: return None

        DEDUPLICATION:
            The _applied_key tracks which song was last applied. We create a key from (title, artist, album) and only return a song if its key is different. This prevents repeatedly returning the same song between poll() calls when no new song has started.

        EFFICIENCY:
            Since _history is append-ordered (oldest first), once we hit a snapshot that's NOT old enough, we can break immediately because all subsequent snapshots will also be too new (they were fetched after the current one).
        """
        
        target = now - self.offset
        candidate = None
        for fetched_at, song in self._history:
            if fetched_at <= target:
                candidate = song
            else:
                '''
                _history is append-ordered (oldest first), so once we hit one that's not old enough yet, nothing after it can be either.
                '''
                
                break

        if candidate is None:
            return None

        key = (candidate.get("title"), candidate.get("artist"), candidate.get("album"))
        if key == self._applied_key:
            return None

        self._applied_key = key
        return candidate

    def reset(self):
        """
        Drop all queued state.

        This clears the history and resets the applied key tracking.

        CALLED ON:
            - (Re)activation: When a new stream starts playing, we call reset() to ensure a fresh tune-in doesn't get its first display blocked by leftover history from a previous session. This prevents the first song from being held back waiting on stale data.
            - Deactivation: When playback stops, we call reset() so nothing stale is waiting to fire if playback resumes later.

        This ensures a clean slate for each new playback session.
        """
        
        self._history = []
        self._applied_key = None