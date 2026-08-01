"""
Rainwave Kodi Addon - Now Playing Dialog

This module provides the NowPlayingDialog class which manages the visual dialog that displays now-playing information. This dialog is the primary user interface for the addon while a Rainwave stream is playing.

DIALOG OVERVIEW:
    - The dialog is defined in: resources/skins/Default/1080i/script-rainwave-nowplaying.xml
    - It stays visible while a Rainwave stream is playing
    - It displays: current song info, album artwork, progress bar, next/previous panels
    - It's controlled by service.py (shown/hidden based on playback state)

KEY FEATURES:
    - Smooth animations and transitions
    - Configurable panels (next/previous can be toggled)
    - Progress bar synchronized with actual audio
    - Background artwork slideshow integration

INHERITANCE:
    This class inherits from xbmcgui.WindowXMLDialog, which provides:
        - Dialog lifecycle management
        - Window property access
        - Control access and manipulation
        - Event handling
"""

import threading
import time
import xbmc
import xbmcaddon
import xbmcgui

from .utils import log

# Control IDs of the progress bar / time label
PROGRESS_CONTROL_ID = 501
TIME_LABEL_CONTROL_ID = 502

FORWARDED_ACTIONS = {
    xbmcgui.ACTION_MOVE_LEFT: "Left",
    xbmcgui.ACTION_MOVE_RIGHT: "Right",
    xbmcgui.ACTION_MOVE_UP: "Up",
    xbmcgui.ACTION_MOVE_DOWN: "Down",
    xbmcgui.ACTION_PAGE_UP: "PageUp",
    xbmcgui.ACTION_PAGE_DOWN: "PageDown",
    xbmcgui.ACTION_SELECT_ITEM: "Select",
    xbmcgui.ACTION_PARENT_DIR: "ParentDir",
    xbmcgui.ACTION_PREVIOUS_MENU: "Back",
    xbmcgui.ACTION_SHOW_INFO: "Info",
    xbmcgui.ACTION_NEXT_ITEM: "NextItem",
    xbmcgui.ACTION_PREV_ITEM: "PreviousItem",
}

ADDON_ID = "plugin.audio.rainwave"

#==NOW PLAYING DIALOG CLASS================

class NowPlayingDialog(xbmcgui.WindowXMLDialog):
    """
    The visual dialog that displays now-playing information.

    This class manages the dialog that shows:
        - Current song: title, artist, album
        - Album artwork
        - Progress bar with timing
        - Next song candidates (rotating)
        - Previous song
        - Background slideshow

    INHERITANCE:
        Inherits from xbmcgui.WindowXMLDialog to integrate with Kodi's dialog system. The parent class provides:
            - doModal(): Show the dialog modally
            - close(): Close the dialog
            - onInit(): Called when dialog is initialized
            - onAction(): Called when user performs an action
            - onClick(): Called when user clicks a control

    ATTRIBUTES:
        handle (int): Plugin handle
        addon_path (str): Path to the addon directory
        default_skin (str): Default skin name ("Default")
        default_res (str): Default resolution ("1080i")

    DIALOG LIFECYCLE:
        1. service.py creates an instance: dialog = NowPlayingDialog(...)
        2. service.py calls dialog.display() to show it
        3. Dialog stays visible until:
           - playback stops (service.py calls hide_widget())
           - user closes it
           - service stops
        4. service.py calls dialog.hide_widget() to hide it

    SKIN INTEGRATION:
        The dialog is defined in script-rainwave-nowplaying.xml which:
            - Defines the visual layout
            - Binds controls to Window properties
            - Sets up animations
            - Handles visibility conditions

    The dialog reads data from Window(10000) properties set by:
        - Widget class (song metadata)
        - Slideshow class (background images)
        - service.py (status properties)
    """
    
    def __init__(self, *args, **kwargs):
        # Initialize the now-playing dialog.
        
        super().__init__(*args, **kwargs)
        self._visible = False
        self._monitor = xbmc.Monitor()
        self._progress_thread = None
        self._song_start = None
        self._song_length = None
        self._clock_offset = 0.0
        self._display_offset = 0

    def display(self):
        """
        Show the now-playing dialog.

        This makes the dialog visible on screen. The dialog will stay
        visible until explicitly hidden or closed.

        CALLED BY:
            service.py calls this when:
                - A Rainwave stream starts playing (in RainwavePlayerMonitor._activate())
                - The widget needs to be shown

        DIALOG BEHAVIOR:
            - The dialog is non-modal (doesn't block Kodi's UI)
            - It can be closed by the user
            - It will be automatically hidden when playback stops
            - It updates its content dynamically via Window properties

        NOTE:
            This doesn't set any initial content - that's done by the Widget class writing to Window(10000) properties. The dialog simply reads and displays those properties.
        """
        
        if not self._visible:
            self.show()
            self._visible = True
            self._start_progress_thread()
            log("Now-playing widget shown")

    def hide_widget(self):
        """
        Hide the now-playing dialog.

        This makes the dialog invisible. It doesn't destroy the dialog instance - it can be shown again later.

        CALLED BY:
            service.py calls this when:
                - Playback stops (in RainwavePlayerMonitor._deactivate())
                - The addon is deactivated
                - The service stops

            This ensures the dialog doesn't stay on screen when not appropriate.

        NOTE:
            The dialog instance is kept alive in service.py, so calling display() again will show it without creating a new instance.
        """
        
        if self._visible:
            self._visible = False
            self._progress_thread = None
            self.close()
            log("Now-playing widget hidden")

    @property
    def is_visible(self):
        return self._visible

    def set_song_timing(self, start_actual, length, server_time, offset=0):
        """
        Set timing information for the progress bar.

        This method provides the timing data needed for the progress bar to accurately reflect the song's progress, accounting for the stream buffer delay.

        ARGS:
            start_actual (float): Unix timestamp when the song started playing on the Rainwave server
            length (float): Song duration in seconds
            server_time (float): Server clock timestamp at time of API response
            offset (float): Configured buffer delay in seconds

        HOW IT WORKS:
            The progress bar needs to know:
                1. When the song started (start_actual)
                2. How long the song is (length)
                3. What time it is NOW (to calculate elapsed time)

            However, there's a complication: the server's clock and Kodi's clock may not be perfectly synchronized. If we just used:
                elapsed = now - start_actual

            We might be slightly off. Instead, we use server_time to correct for any drift:
                elapsed = (server_time - start_actual) + (now - server_time)

            But there's also the buffer delay: the song started playing on the server at start_actual, but due to the buffer, it didn't start playing on the user's device until ~offset seconds later. So we need to subtract the offset:
                elapsed = (server_time - start_actual) + (now - server_time) - offset

            Actually, the SyncQueue already handles the offset by delaying the display until (now - offset) >= start_actual. So when this method is called, the song is already "due" to be displayed.

            The calculation used in the dialog is typically:
                elapsed = (server_time - start_actual) - offset
                remaining = length - elapsed
                percentage = (elapsed / length) * 100

            But the exact implementation is in the skin XML, which reads these timing values from Window properties or calculates them directly.

        WHAT THIS METHOD DOES:
            This method makes the timing values available to the skin XML so it can calculate and display the progress bar correctly.

            The skin XML uses expressions like:
                $INFO[Window(10000).Property(Rainwave.StartActual)]
                $INFO[Window(10000).Property(Rainwave.Length)]
                $INFO[Window(10000).Property(Rainwave.ServerTime)]

            And calculates:
                Elapsed: (ServerTime - StartActual) - Offset
                Percentage: (Elapsed / Length) * 100
        """
        
        if not start_actual or not length:
            return
        self._song_start = start_actual
        self._song_length = length
        self._clock_offset = server_time - time.time()
        self._display_offset = offset

    def _start_progress_thread(self):
        if self._progress_thread and self._progress_thread.is_alive():
            return
        self._progress_thread = threading.Thread(
            target=self._progress_loop, daemon=True
        )
        self._progress_thread.start()

    def _progress_loop(self):
        while self._visible and not self._monitor.abortRequested():
            self._update_progress()
            if self._monitor.waitForAbort(1):
                break

    def _update_progress(self):
        if not self._song_start or not self._song_length:
            return
        now_server = time.time() + self._clock_offset - self._display_offset
        elapsed = max(0.0, now_server - self._song_start)
        elapsed = min(elapsed, self._song_length)
        percent = (elapsed / self._song_length) * 100
        try:
            self.getControl(PROGRESS_CONTROL_ID).setPercent(percent)
            self.getControl(TIME_LABEL_CONTROL_ID).setLabel(
                "{0} / {1}".format(
                    self._format_time(elapsed), self._format_time(self._song_length)
                )
            )
        except RuntimeError:
            pass

    @staticmethod
    def _format_time(seconds):
        seconds = int(seconds)
        return "{0}:{1:02d}".format(seconds // 60, seconds % 60)

    def onAction(self, action):
        
        # Open game selector when "i" key is pressed
        
        if action.getId() == xbmcgui.ACTION_SHOW_INFO:
            self._open_game_selector()
            return

        # Open settings on context menu
        
        if action.getId() == xbmcgui.ACTION_CONTEXT_MENU:
            xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
            return

        # Forward all other actions to underlying UI
        
        name = FORWARDED_ACTIONS.get(action.getId())
        if name:
            target = xbmcgui.getCurrentWindowId()
            xbmc.executebuiltin(f"Action({name},{target})")
            
    def _open_game_selector(self):
        
        '''
        Open game selector and update manifest with manual override. See game_art.py.
        '''
        
        try:
            from .game_selector import GameSelectorDialog
            addon = xbmcaddon.Addon()
            
            # Get current album and song from the now-playing widget
            
            current_album = xbmcgui.Window(10000).getProperty("Rainwave.Album")
            current_song_title = xbmcgui.Window(10000).getProperty("Rainwave.Title")
            current_sid = xbmcgui.Window(10000).getProperty("Rainwave.CurrentStation")
            current_sid = int(current_sid) if current_sid else None
            current_game = xbmcgui.Window(10000).getProperty("Rainwave.ResolvedGame")
            current_game_id = xbmcgui.Window(10000).getProperty("Rainwave.ResolvedGameId") or None

            dialog = GameSelectorDialog(
                addon.getSettingString("steamgriddb_api_key"),
                current_album=current_album,
                current_sid=current_sid,
                current_song_title=current_song_title,
                current_game=current_game,
                current_game_id=current_game_id
            )
            if dialog.show():
                
                # Update slideshow immediately
                
                xbmcgui.Window(10000).setProperty(
                    "Rainwave.ManualGame",
                    dialog.selected_game.get("name", "")
                )
        except Exception as e:
            log(f"Game selector failed: {e}")

    def onClick(self, control_id):
        pass