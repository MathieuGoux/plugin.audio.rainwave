"""
Rainwave Kodi Addon - Background Service

This module provides the long-running background service that runs while Kodi is active. It handles:

    1. Monitoring Kodi's playback state to detect when Rainwave streams are playing
    2. Polling the Rainwave API for now-playing information (every 5 seconds)
    3. Updating the now-playing widget with current song data
    4. Managing the background slideshow
    5. Synchronizing metadata display with actual audio playback

ARCHITECTURE NOTE:
    Kodi creates a NEW Python interpreter for each plugin:// invocation. This means the plugin process (default.py/router.py) and service process (service.py) CANNOT share Python objects directly. They communicate via:

        - Window(10000) properties: router.py sets "Rainwave.CurrentStation" when a station is selected, and service.py reads this to know which station to poll

        - File system: For persistent data like cached artwork

    The service starts automatically when Kodi starts (see addon.xml: <extension point="xbmc.service" library="service.py" start="startup"/>) and runs continuously until Kodi exits.
"""

import os
import time
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.api import RainwaveAPI
from resources.lib.widget import Widget
from resources.lib.nowplaying_dialog import NowPlayingDialog
from resources.lib.slideshow import Slideshow
from resources.lib.game_art import GameArtProvider
from resources.lib.sync_queue import SyncQueue
from resources.lib.utils import log

#==CONSTANTS================

# Polling interval for Rainwave API (seconds)
# Rainwave's "now playing" data doesn't change more frequently than this
POLL_INTERVAL = 5  # seconds, Rainwave "now playing" refresh

# Main loop granularity (seconds)
# Most operations are gated by their own internal timers, so a fine-grained tick doesn't significantly impact performance. The primary exception is the buffering spinner animation which needs frequent updates for smoothness.
TICK = 0.1  # seconds

# Rainwave's relay stream host - used to identify Rainwave streams among any other audio that might be playing in Kodi
STREAM_HOST = "relay.rainwave.cc"

# Buffering spinner animation configuration
# Frame files: resources/media/spinner_00.png .. spinner_{N-1:02d}.png
# 12 frames at 0.1s each = ~1.2 seconds per full rotation
SPINNER_FRAME_COUNT = 12
SPINNER_FRAME_INTERVAL = 0.1  # seconds per frame

#==PLAYBACK MONITOR================

class RainwavePlayerMonitor(xbmc.Player):
    """
    Monitors Kodi's player state to detect when Rainwave streams are playing and updates the widget accordingly.

    WHY THIS CLASS EXISTS:
        - Kodi creates a new Python interpreter for each plugin invocation
        - The plugin (router.py) and service (this file) cannot share objects
        - Communication happens via Window(10000) properties
        - router.py sets "Rainwave.CurrentStation" when a station is selected
        - This class reads that property to know which station to poll

    DEBOUNCING LOGIC:
        Previously, the addon trusted Kodi's playback callbacks (onPlayBackStopped, onPlayBackError, etc.) unconditionally. However, live internet radio streams can briefly hiccup (buffering stall, momentary reconnect) without playback actually ending from the listener's perspective. Kodi's engine can still fire onPlayBackStopped for that split second.

        The old behavior:
            1. Callback fires -> deactivate widget
            2. Audio continues playing (same continuous connection)
            3. onAVStarted never fires again to reactivate
            4. Polling (and widget) stays dead for the rest of the session

        The new approach (implemented in _check_active_state):
            1. Treat every callback as a prompt to re-check actual state
            2. Check isPlayingAudio() and _is_rainwave_stream() directly
            3. Also call _check_active_state() once per second from main loop
            4. Require TWO consecutive "not playing" readings before deactivating

        This prevents the widget from staying dead after brief hiccups while still catching real stops within ~2 seconds.
    """

    def __init__(self, widget, dialog, sync_queue, slideshow, spinner_dir):
        super().__init__()
        self.widget = widget
        self.dialog = dialog
        self.sync_queue = sync_queue
        self.slideshow = slideshow
        self.spinner_dir = spinner_dir
        self.active = False
        self.home = xbmcgui.Window(10000)
        self._not_playing_streak = 0
        self._last_song_key = None

    def _is_rainwave_stream(self):
        """
        Check if the currently playing file is a Rainwave stream.

        Uses STREAM_HOST ('relay.rainwave.cc') to identify Rainwave streams among any other audio that might be playing in Kodi.

        Returns:
            bool: True if currently playing a Rainwave stream, False otherwise
        """
        
        try:
            return STREAM_HOST in self.getPlayingFile()
        except Exception:
            return False

    def _current_sid(self):
        """
        Get the currently selected station ID from Window properties.

        The plugin (router.py) sets 'Rainwave.CurrentStation' when a user selects a station. This method reads that property to determine which station to poll for now-playing information.

        Returns:
            int or None: Station ID as integer, or None if not set
        """
        
        sid = self.home.getProperty("Rainwave.CurrentStation")
        return int(sid) if sid else None

    def _check_active_state(self):
        """
        Determine if the widget should be active based on current playback state.

        This is the core of the debouncing logic. See class docstring for detailed explanation of why this is necessary.

        The method:
            1. Checks actual playback state via isPlayingAudio() and _is_rainwave_stream()
            2. If playing: resets not-playing streak, activates if not already active
            3. If not playing: increments streak, deactivates after 2 consecutive readings

        This runs both from Kodi callbacks AND once per second from the main loop for robustness.
        """
        
        try:
            is_playing = self.isPlayingAudio() and self._is_rainwave_stream()
        except Exception:
            is_playing = False

        if is_playing:
            self._not_playing_streak = 0
            if not self.active:
                self._activate()
            return

        self._not_playing_streak += 1
        if self._not_playing_streak >= 2 and self.active:
            self._deactivate()

    def _activate(self):
        """
        Activate the widget and start polling for now-playing data.

        Called when a Rainwave stream starts playing. Performs:

            1. Resets sync queue to clear any stale lag-buffer history from a previous session (so first display isn't held back waiting on stale data - see SyncQueue.reset())

            2. Sets buffering state and starts spinner animation:
               - Sets "Rainwave.Buffering" = "true" property
               - Sets initial spinner frame
               The skin shows a "Tuning in..." placeholder while this is true

            3. Fetches initial song data from API

            4. Displays the now-playing dialog

            5. Triggers first sync pump (_pump_sync) to start the display cycle

        Note: The sync queue deliberately withholds the very first song until it's caught up with the actual (buffered) audio. Without this, the widget would show data for a song that hasn't started playing yet, which would look like the widget failed to load for ~15-20s.
        """
        
        self.active = True
        self.sync_queue.reset()
        self.home.setProperty("Rainwave.Buffering", "true")
        self.home.setProperty(
            "Rainwave.SpinnerFrame", os.path.join(self.spinner_dir, "spinner_00.png")
        )
        now = time.time()
        song = self.widget.refresh(self._current_sid())
        self.sync_queue.push(song, now)
        self.dialog.display()
        self._pump_sync(now)

    def _pump_sync(self, now):
        """
        Apply whichever polled snapshot has finished waiting out the buffer delay.

        This method checks if any queued snapshot is now old enough to display (now - offset seconds) and applies it to the UI.

        This is called every tick (0.1s), NOT just on the 5-second poll cadence, because the buffer delay (15-20s) is normally longer than POLL_INTERVAL. A snapshot from a few polls back is often the one that's due, and checking every second ensures the display update lands close to the
        real audio transition instead of up to 5 seconds late.

        Args:
            now (float): Current timestamp from time.time()

        Behavior:
            - Gets song from sync queue that's due (if any)
            - Clears buffering state
            - Applies song to widget
            - Updates timing info
            - Updates player info tag
            - Updates slideshow with current game in auto mode
        """
        
        song = self.sync_queue.poll(now)
        if song is None:
            return
        self.home.clearProperty("Rainwave.Buffering")
        self.widget.apply_current(song)
        self._apply_timing(song)
        self._update_player_info(song)
        '''
        Same delayed data as everything else above -- in auto mode, this is what keeps the background changing to match the game whose audio is actually playing, not whichever game the API most recently reported (see slideshow.py/game_art.py). The song title is passed too, only actually used as a fallback search signal if the album title alone can't find a match -- see game_art.py's _resolve_game_id(). The station sid lets that same method trust the album title outright on stations where it's reliably the game name (see TRUSTED_ALBUM_STATIONS there), skipping the song-title tier entirely instead of risking it outvoting a known-good match.
        '''
        
        self.slideshow.set_current_game(song.get("album"), song.get("title"), self._current_sid())

    def onAVStarted(self):
        """
        Called by Kodi when audio/video playback starts.

        Triggers a check of the active state to see if we should activate the widget for a Rainwave stream.
        """
        
        self._check_active_state()

    def _apply_timing(self, song):
        """
        Update the progress bar timing information.

        Sets the song timing on the now-playing dialog:
            - start_actual: Unix timestamp when the song started on the server
            - length: Song duration in seconds
            - server_time: Server clock at time of API response
            - offset: Configured buffer delay

        This allows the progress bar to accurately reflect song progress despite the stream buffer delay.

        Args:
            song (dict or None): Song data from API, or None if unavailable
        """
        
        if song is None:
            return
        self.dialog.set_song_timing(
            song.get("start_actual"),
            song.get("length"),
            song.get("server_time"),
            self.sync_queue.offset,
        )

    def _update_player_info(self, song):
        """
        Update Kodi's player info tag with current song metadata.

        CRITICAL: This is what makes the metadata visible to JSON-RPC clients like Kore (Kodi's official remote app). Previously, only the skin widget was updated, which meant remote apps couldn't see the now-playing information.

        IMPORTANT IMPLEMENTATION NOTE:
            Building a fresh xbmcgui.ListItem() and calling updateInfoTag() on it looks reasonable and is what Kodi's own examples show, but in practice title/artist/album set this way don't reliably reach Player.GetItem over JSON-RPC (only properties like art do).

            The combination that actually works:
                1. Fetch the REAL currently-playing item via getPlayingItem()
                2. Mutate ITS music info tag in place
                3. Pass that same item back to updateInfoTag()

            This method also handles seeking to the correct position in the song to align the progress bar with actual playback. The seek only happens once per song (tracked via _last_song_key) to avoid causing audible jumps/stutters.

        Args:
            song (dict or None): Song data from API, or None if unavailable
        """
        
        if song is None:
            return
        if not self._is_rainwave_stream():
            return
        try:
            item = self.getPlayingItem()
            tag = item.getMusicInfoTag()
            tag.setTitle(song.get("title", ""))
            tag.setArtist(song.get("artist", ""))
            tag.setAlbum(song.get("album", ""))
            tag.setMediaType("song")
            '''
            Without a duration, Kodi has nothing to compute a percentage/progress from -- Player.GetProperties' "totaltime" stays effectively unset, so Kore has no data to draw a progress bar with at all (not a refresh problem like title/artist, an actual missing-data one).
            '''
            
            length = song.get("length")
            if length:
                tag.setDuration(int(length))

            art = song.get("art", "")
            if art:
                item.setArt({"thumb": art, "icon": art})
            self.updateInfoTag(item)
            '''
            Kodi's internal playback clock starts counting from 0 the moment *we* tuned in, not from wherever Rainwave actually was in the track -- so without a seek, the progress bar would be accurate in shape but wrong in position (e.g. showing 0:15 elapsed on a track that was actually already 2 minutes in). Only do this once per song (tracked via _last_song_key), not on every 5-second poll -- seeking repeatedly on an unchanged song would cause an audible jump/stutter each time.
            
            Same re-basing as set_song_timing(): this method only runs once the sync queue has decided the song is due for display, `sync_queue.offset` seconds after the server reported it -- so `server_time - start_actual` alone would seek `offset` seconds further into the song than what the listener is actually about to hear, right at the moment it's applied. Subtracting the offset lines the seek target up with the delayed display instead.
            
            This may simply do nothing on some Kodi versions/configurations: IsLive=true (set in router.py, needed to stop brief stalls being misread as end-of-track) can also make Kodi refuse seeks on the grounds that a live stream has no meaningful seek target. If so, the progress bar will still render (from the duration set above) but start counting from 0 each song rather than the song's true elapsed position -- a cosmetic gap, not a functional one.
            '''
            
            song_key = (song.get("title"), song.get("artist"), song.get("album"))
            if song_key != self._last_song_key:
                self._last_song_key = song_key
                start_actual = song.get("start_actual")
                server_time = song.get("server_time")
                if start_actual and server_time:
                    elapsed = max(0, server_time - start_actual - self.sync_queue.offset)
                    try:
                        self.seekTime(elapsed)
                    except Exception as e:
                        log(f"Could not seek to song position: {e}")
        except Exception as e:
            log(f"Could not update player info tag: {e}")

    def onPlayBackStopped(self):
        """
        Called by Kodi when playback stops normally.

        Triggers a check of the active state. Due to debouncing logic, this won't immediately deactivate - it just prompts a re-check.
        """
        
        self._check_active_state()

    def onPlayBackEnded(self):
        """
        Called by Kodi when playback ends naturally (track finished).

        Triggers a check of the active state.
        """
        
        self._check_active_state()

    def onPlayBackError(self):
        """
        Called by Kodi when a playback error occurs.

        Triggers a check of the active state. Due to debouncing logic, brief errors (like buffering stalls) won't deactivate the widget.
        """
        
        self._check_active_state()

    def _deactivate(self):
        """
        Deactivate the widget when playback stops.

        Cleans up:
            - Hides the now-playing dialog
            - Clears the widget display
            - Resets buffering state
            - Resets sync queue
            - Re-enables screensaver (if setting enabled)

        Note: We check the inhibit_screensaver setting here too, though un-inhibiting when nothing was inhibited is harmless.
        """
        
        if self.active:
            self.active = False
            self.dialog.hide_widget()
            self.widget.clear()
            self.home.clearProperty("Rainwave.Buffering")
            self.sync_queue.reset()
            
            if xbmcaddon.Addon().getSettingBool("inhibit_screensaver"):
                xbmc.executebuiltin('InhibitScreensaver(false)')
                
    def onAction(self, action):
        """
        Handle user actions/key presses.

        Currently handles:
            - ACTION_SHOW_INFO ('i' key): Opens game selector dialog

        Args:
            action: The action object from Kodi
        """
        
        if action.getId() == xbmcgui.ACTION_SHOW_INFO:
            self._open_game_selector()
        super().onAction(action)
        
    def _open_game_selector(self):
        """
        Open the manual game selector dialog.

        Called when user presses the Information key ('i') during playback. Allows manual selection of game artwork when automatic detection fails or user wants to override it.

        The selected game is passed to the slideshow for immediate display. Overrides are stored in the art cache manifest for future use.
        """
        
        try:
            from .game_selector import GameSelectorDialog
            dialog = GameSelectorDialog(
                "game_selector.xml",
                xbmcaddon.Addon().getAddonInfo("path"),
                api_key=xbmcaddon.Addon().getSettingString("steamgriddb_api_key")
            )
            dialog.doModal()

            if dialog.selected_game:
                self.slideshow.set_manual_game(dialog.selected_game["name"])
                log(f"Game selector: Using art for {dialog.selected_game['name']}")
        except Exception as e:
            log(f"Game selector failed: {e}", xbmc.LOGERROR)            
#==SETTINGS HELPERS================

def _reload_display_settings(home):
    """
    Reload the show_prev_next setting and update Window property.

    Small enough (one bool) not to warrant its own module. Mirrors the same "read setting, write a window property, skin reads the property" pattern used by Slideshow.reload_settings().

    The skin's previous/next panel is gated on Rainwave.ShowPrevNext via a <visible> condition, so flipping this takes effect immediately without requiring a restart.

    Args:
        home (xbmcgui.Window): Window(10000) instance
    """
    
    enabled = xbmcaddon.Addon().getSettingBool("show_prev_next")
    home.setProperty("Rainwave.ShowPrevNext", "true" if enabled else "false")

#==MAIN SERVICE FUNCTION================

def run():
    """
    Main service entry point - runs continuously while Kodi is running.

    This is the heart of the addon's background functionality. It:

        1. Initializes all components:
           - API client (RainwaveAPI)
           - Widget for now-playing display
           - Game art provider (GameArtProvider)
           - Slideshow manager (Slideshow)
           - Sync queue (SyncQueue)
           - Window(10000) for property access

        2. Sets up monitors:
           - Player monitor for playback state
           - Settings monitor for live setting updates

        3. Enters the main loop that runs until Kodi requests abort

    THE MAIN LOOP (runs every TICK = 0.1 seconds):
        - Checks active state (playback monitoring)
        - Polls API for updates (every POLL_INTERVAL = 5 seconds)
        - Pumps sync queue (every tick)
        - Ticks widget and slideshow (every tick)
        - Advances spinner animation (when buffering)

    Most operations are gated by their own internal timers and just no-op if not due yet, so the fine-grained TICK doesn't significantly impact performance.
    """
    
    api = RainwaveAPI()
    widget = Widget(api)
    game_art = GameArtProvider()
    slideshow = Slideshow(game_art)
    sync_queue = SyncQueue()
    home = xbmcgui.Window(10000)
    _reload_display_settings(home)

    addon_path = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path"))
    
    '''
    Every $INFO-bound texture elsewhere in this addon (slideshow images, album art) is a full absolute path -- static references written directly in the skin XML (like the settings gear icon) get resolved against the skin's own media folder automatically, but that resolution isn't guaranteed for a bare filename handed to $INFO[Window(...).Property(...)] at runtime. Building the full path here up front, once, rather than a bare filename each tick, keeps this consistent with the pattern already proven to work.
    '''
    
    spinner_dir = os.path.join(addon_path, "resources", "media")

    dialog = NowPlayingDialog(
        "script-rainwave-nowplaying.xml",
        xbmcaddon.Addon().getAddonInfo("path"),
        "Default",
        "1080i",
    )

    player_monitor = RainwavePlayerMonitor(widget, dialog, sync_queue, slideshow, spinner_dir)

    class SettingsMonitor(xbmc.Monitor):
        """
        Reloads settings-driven state whenever the user changes it.

        This allows a running Kodi session to pick up changes immediately without requiring a restart.

        When settings change, it:
            - Reloads slideshow settings
            - Reloads sync queue settings
            - Reloads display settings

        Inherits from xbmc.Monitor to receive the onSettingsChanged callback.
        """
    
        def onSettingsChanged(self):
            slideshow.reload_settings()
            sync_queue.reload_settings()
            _reload_display_settings(home)
            log("Settings changed, reloaded")

    kodi_monitor = SettingsMonitor()
    last_refresh = 0.0
    last_spinner_frame = 0.0
    spinner_index = 0
    
#==MAIN SERVICE LOOP================

    log("Service started")
    # Main loop - runs until Kodi requests abort (shutdown or addon stop)

    while not kodi_monitor.abortRequested():
        now = time.time()
        # Check if we should be active (Rainwave stream playing)
        # This runs every TICK (0.1s) for responsiveness

        player_monitor._check_active_state()

        if player_monitor.active:
            # Poll API for fresh data every POLL_INTERVAL (5s)
            
            if now - last_refresh >= POLL_INTERVAL:
                song = widget.refresh(player_monitor._current_sid())
                sync_queue.push(song, now)
                last_refresh = now
                
            # Pump sync queue every tick to apply due data
            # This is what keeps display in sync with audio
            
            player_monitor._pump_sync(now)
            
            # Tick widget and slideshow for their internal timers
            widget.tick(now)
            slideshow.tick(now)

            # Animate buffering spinner if visible
            # Only bothers advancing while the placeholder is actually visible
            if home.getProperty("Rainwave.Buffering") == "true":
                if now - last_spinner_frame >= SPINNER_FRAME_INTERVAL:
                    # Advance to next spinner frame
                    
                    spinner_index = (spinner_index + 1) % SPINNER_FRAME_COUNT
                    frame_path = os.path.join(spinner_dir, f"spinner_{spinner_index:02d}.png")
                    home.setProperty("Rainwave.SpinnerFrame", frame_path)
                    last_spinner_frame = now

        # Sleep until next tick or abort requested
        # waitForAbort returns True if abort was requested, False if timeout
        
        if kodi_monitor.waitForAbort(TICK):
            break

#==CLEANUP ON EXIT================

    # Re-enable screensaver if it was inhibited and setting is enabled

    if player_monitor.active and xbmcaddon.Addon().getSettingBool("inhibit_screensaver"):
        xbmc.executebuiltin('InhibitScreensaver(false)')
    
    # Hide the widget

    dialog.hide_widget()
    log("Service stopped")

#==ENTRY POINT================

if __name__ == '__main__':
    run()