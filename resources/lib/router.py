"""
Rainwave Kodi Addon - URL Router

This module provides the Router class which handles URL routing and action dispatching for the Kodi plugin. It's the entry point that Kodi calls when the plugin is invoked via plugin:// URLs.

ARCHITECTURE CONTEXT:

    Kodi creates a NEW Python interpreter for each plugin:// invocation. This means:

        1. We cannot maintain state between invocations in Python variables
        2. All state must be persisted via:
           - Window(10000) properties (for communication with service.py)
           - Addon settings (for user configuration)
           - File system (for cached data)

        3. Each plugin invocation is independent and stateless

URL ROUTING:

    When the addon is invoked, Kodi passes command-line arguments:
        - sys.argv[0]: Plugin URL (e.g., plugin://plugin.audio.rainwave/)
        - sys.argv[1]: Handle (unique ID for this plugin instance)
        - sys.argv[2]: Query string (starts with '?', e.g., ?action=play&id=5)

    The Router parses these arguments and dispatches to the appropriate action handler based on the 'action' parameter.

ACTION FLOW:

    - No action: Show main menu
    - 'stations': Show station selection menu
    - 'history': Show history menu (station list)
    - 'history_songs': Show songs for a specific station
    - 'play': Start playback for a station
    - 'settings': Open Kodi settings
    - 'request': Request a song (currently non-functional)
    - 'clear_art_cache': Clear cached artwork
"""

import sys
import xbmc
import xbmcaddon
import xbmcplugin
import xbmcgui

from urllib.parse import parse_qs
from .stations import StationMenu
from .player import Player
from .views import MainMenu
from .api import RainwaveAPI
from .game_art import GameArtProvider
from .history import HistoryMenu

#==ROUTER CLASS================

class Router:
    """
    Routes plugin invocations to appropriate action handlers.

    This class is instantiated once per plugin invocation and handles:
        - Parsing command-line arguments from Kodi
        - Dispatching to appropriate action handlers
        - Managing the directory listing for menus
        - Setting resolved URLs for playback

    ATTRIBUTES:
        handle (int): Plugin handle from sys.argv[1]
        params (dict): Parsed query parameters from sys.argv[2]
        api (RainwaveAPI): API client instance for data fetching

    HANDLE USAGE:
        The handle is used for:
            - xbmcplugin.addDirectoryItem(): Adding items to directory listings
            - xbmcplugin.setResolvedUrl(): Starting playback with a resolved URL
            - xbmcplugin.endOfDirectory(): Finalizing directory listings

    PARAMS PARSING:
        sys.argv[2] starts with '?' and contains URL-encoded parameters.
        
            Example: ?action=play&id=5&name=Test 
        
        parse_qs() parses this into: {'action': ['play'], 'id': ['5'], 'name': ['Test']}
        
        Note that values are always lists, so we use [0] to get the first value.
    """
    
    def __init__(self):
        """
        Initialize router with Kodi-provided arguments.

        Parses:
            - sys.argv[1]: Handle (unique plugin instance ID)
            - sys.argv[2]: Query string parameters

        Also initializes the API client for use by action handlers.
        """
        
        self.handle = int(sys.argv[1])
        self.params = parse_qs(sys.argv[2][1:])
        self.api = RainwaveAPI()

    def run(self):
        """
        Main routing method - dispatches based on action parameter.

        This is the primary entry point called from default.py's main().

        ACTION HANDLERS:

            No action (None):
                - Shows main menu via MainMenu
                - Ends directory listing

            'stations':
                - Shows station selection menu via StationMenu
                - Ends directory listing

            'history':
                - Shows history menu (station list) via HistoryMenu
                - Does NOT end directory (HistoryMenu handles it)

            'settings':
                - Opens Kodi's addon settings dialog
                - Sets resolved URL to False (not a playable item)

            'history_songs':
                - Shows songs for a specific station via HistoryMenu
                - Does NOT end directory (HistoryMenu handles it)

            'play':
                - Starts playback for a station
                - Sets resolved URL to True (playable item)
                - Inhibits screensaver if enabled

            'request':
                - Attempts to request a song (currently non-functional)
                - Shows notification, sets resolved URL to False

            'clear_art_cache':
                - Clears cached artwork after user confirmation
                - Sets resolved URL to False

            Each handler either:
                - Shows a directory (calls xbmcplugin.endOfDirectory)
                - Sets a resolved URL (calls xbmcplugin.setResolvedUrl)
                - Opens settings (calls xbmcaddon.Addon().openSettings)
        """
        
        action = self.params.get("action", [None])[0]
        
        handle = int(sys.argv[1])

        if action is None:
            MainMenu(self.handle).show()
            xbmcplugin.endOfDirectory(self.handle)
            return

        if action == "stations":
            StationMenu(self.handle).show()
            xbmcplugin.endOfDirectory(self.handle)
            return

        if action == "history":
            HistoryMenu(self.handle, self.api).show_stations()
            return
            
        if action == "settings":
            xbmcaddon.Addon().openSettings()
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return

        if action == "history_songs":
            sid = int(self.params["id"][0])
            HistoryMenu(self.handle, self.api).show_songs(sid)
            return
            
        if action == "play":
            
        #==PLAY ACTION: Start playback for a station================    
            
            # Extract station ID from URL parameters
            # Example: ?action=play&id=5 -> params['id'] = ['5']
            sid = int(self.params["id"][0])

            # Register as listener (best-effort, doesn't block playback)
            # This increments the station's listener count on Rainwave
            # Note: This consistently 404s for anonymous users but we call it anyway for completeness
            self.api.tune_in(sid)
            
            #==CRITICAL: Store current station in Window(10000) property================
            
            '''
            This is how service.py knows which station to poll for updates.
            
            Note: The plugin (this code) and service (service.py) run in SEPARATE Python processes. They cannot share variables or objects directly. Window(10000) properties are the communication mechanism between them.
            
            Without this, service.py would have no way to know which station is currently playing, and the now-playing display would never update after the first song.
            '''

            xbmcgui.Window(10000).setProperty(
                "Rainwave.CurrentStation",
                str(sid)
            )
            
            # Get player instance for stream URL generation
            player = Player(self.api)

            #==Get stream URL================

            '''
            The URL format will be: 
            https://relay.rainwave.cc/{station_slug}.mp3|Icy-MetaData=0
            
            where {station_slug} comes from the STATIONS dict in player.py
            '''
            
            stream_url = player.get_stream_url(sid)
            
            #==Stop current playback================
            
            '''
            This ensures a clean start. Without this, attempting to play a new station might fail or behave unexpectedly if Kodi is already playing something else.
            '''
            
            xbmc.Player().stop()

            #==Fetch initial song metadata================
            
            '''
            This populates the ListItem with current song info BEFORE playback starts. This ensures the user sees metadata immediately rather than waiting for the first API poll from service.py.
            
            IMPORTANT: get_now_playing() returns None when the API call fails (network hiccup, session not tuned in yet, etc - see api.py).
            
            THIS MUST NEVER BLOCK PLAYBACK:
                1. The stream URL doesn't depend on this metadata at all
                2. service.py's periodic refresh will fill in real info once/if the API recovers
                3. So we treat a failed fetch as "no metadata yet" and still play the stream
            '''
            song = self.api.get_now_playing(sid) or {}
            
            #==Create listitem with stream URL and metadata================

            listitem = xbmcgui.ListItem(path=stream_url)
            
            #==Set metadata using modern MusicInfoTag API================

            '''IMPORTANT: Do NOT use the deprecated setInfo() method!
            
            Kodi has been warning about setInfo("music", {...}) being deprecated in the logs: "Please use the respective setter in InfoTagMusic"
            
            More importantly, the deprecated path doesn't reliably populate the same underlying tag that Player.GetItem/updateInfoTag() expose over JSON-RPC. This is exactly why Kore (Kodi's remote app) wasn't showing title/artist/album - only artwork (set via the separate, non-deprecated setArt() call) was getting through.
            
            Here is the modern, fully-supported path:
            '''
            
            tag = listitem.getMusicInfoTag()
            tag.setTitle(song.get("title", ""))
            tag.setArtist(song.get("artist", ""))
            tag.setAlbum(song.get("album", ""))
            tag.setMediaType("song")
            
            #==Set artwork================

            art = song.get("art", "")
            if art:
                # Both thumb and icon are set to the same artwork URL
                # This ensures the artwork shows in all contexts
                listitem.setArt({"thumb": art, "icon": art})

            #==CRITICAL: Mark as live stream================

            '''
            WITHOUT THIS, THE ADDON DOESN'T WORK CORRECTLY!
            
            Here's what happens without IsLive=true:
            
                1. Kodi doesn't know this is a continuous internet radio stream
                2. Kodi treats brief buffering stalls as the track having ended
                3. When Kodi thinks the track ended, it fires onPlayBackEnded
                4. Kodi tries to fetch a "next" item from this plugin's directory to auto-advance (there isn't one, hence "GetDirectory - Error")
                5. Kodi gives up and fully stops playback
                6. This kills service.py's polling for the REST OF THE SESSION
            
            With IsLive=true:
                - Kodi treats interruptions as "keep buffering/reconnecting" rather than "this track is over"
                - The stream continues uninterrupted
                - service.py keeps polling and updating the display
            '''
            
            listitem.setProperty("IsLive", "true")

            # Log the URL for debugging purposes
            xbmc.log(f"[Rainwave] PLAYING URL = {stream_url}", xbmc.LOGINFO)

            #==Set resolved URL to start playback================
            
            '''
            This tells Kodi to play the stream with the provided ListItem.
            The True parameter indicates this is a resolved URL (not a directory that needs further resolution).
            '''
            
            xbmcplugin.setResolvedUrl(self.handle, True, listitem)

            #==Inhibit screensaver during playback (configurable)================

            '''
            This keeps the now-playing widget visible during playback. The user can disable this in settings if they prefer the screensaver to activate normally.
            '''

            if xbmcaddon.Addon().getSettingBool("inhibit_screensaver"):
                xbmc.executebuiltin('InhibitScreensaver(true)')

            #==Widget visibility================

            '''
            The now-playing widget itself is shown/hidden by service.py, which watches actual playback state via xbmc.Player callbacks.
            
            We don't show it here because:
                1. There's a delay before playback actually starts
                2. service.py handles it automatically when it detects playback
            
            Return without ending directory since we set a resolved URL
            '''

            return

        if action == "request":
            '''
            Attempt to request a song (currently non-functional)

            This would call the API to request a specific song, but Rainwave's API authentication doesn't work with Discord-only auth, so there's currently no way to request songs.

            The parameters would be:
                - song: Song ID to request
                - station: Station ID to request it on

            For now, this just shows a notification and does nothing
            '''
            
            self.api.request_song(int(self.params["song"][0]), int(self.params["station"][0]))
            xbmcgui.Dialog().notification("Rainwave", "Requested")
            
            # Set resolved URL to False since this isn't a playable item
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return

        if action == "clear_art_cache":
            
            #==Clear cached artwork================
            
            '''
            This action is invoked via the "Clear art cache" button in Add-on Settings (see settings.xml).
            
            IMPORTANT: This runs in its OWN short-lived process, separate from the long-running service.py. This is because:
           
                1. Settings actions run in their own process
                2. We cannot reach into the running service's GameArtProvider (it's not accessible from here anyway)
            
            Therefore, we construct a new GameArtProvider instance just for this operation.
            '''
            
            dialog = xbmcgui.Dialog()
            if dialog.yesno("Rainwave", "Delete all cached background art? This can't be undone."):
                GameArtProvider().clear()
                dialog.notification("Rainwave", "Art cache cleared")
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return