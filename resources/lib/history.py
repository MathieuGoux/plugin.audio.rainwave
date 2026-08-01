"""
Rainwave Kodi Addon - History Menu

This module provides the HistoryMenu class which displays recently played songs for each station. It offers a two-level browsing experience:

    Level 1: Station list - Shows all stations, each linking to its history
    Level 2: Song list - Shows recently played songs for a specific station

This allows users to browse what has been playing on each station, which is useful for discovering new music or seeing what played while they were away.

DATA SOURCE:
    The history data comes from the Rainwave API's sched_history endpoint, which returns recently played songs for each station. This is different from user-specific history (which requires authentication and isn't available in this anonymous addon).
"""

import sys
import time
import xbmcgui
import xbmcplugin

from .constants import STATIONS
from .artwork import Artwork


def _relative_time(played_at):
    """
    "12 min ago" style formatting for a unix timestamp, or "" if there's nothing to show. Purely cosmetic for this read-only list, so a plain wall-clock delta (no sync-queue-style correction for Kodi/server clock drift, unlike service.py's audio-sync math) is more than accurate enough.
    """
    
    if not played_at:
        return ""
    delta = max(0, int(time.time() - played_at))
    if delta < 60:
        return "just now"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"

#==HISTORY MENU CLASS================

class HistoryMenu:
    """
    Displays history menus for recently played songs.

    Provides a two-level directory structure:
        1. First level: List of stations (show_stations)
        2. Second level: List of recently played songs for a station (show_songs)

    ATTRIBUTES:
        handle (int): Plugin handle for adding directory items
        api (RainwaveAPI): API client for fetching history data

    HISTORY DATA:
        The history data comes from the API's sched_history field, which contains past election events. Each event has:
            - start_actual: When the song started playing (unix timestamp)
            - songs: List of songs in that election (usually just one)

    The addon extracts the song information and displays it in a user-friendly format with title, artist, album, and artwork.

    LIMITATIONS:
        - History is limited to what the API provides (typically ~5 songs)
        - Only shows station history, not user-specific history
        - Requires API access (won't work offline)
    """

    def __init__(self, handle, api):
        self.handle = handle
        self.api = api
        self.art = Artwork()

    def show_stations(self):
        """
        Show a menu of stations for history browsing.

        Creates a Kodi directory with one item per station. Each item, when selected, shows the recently played songs for that station.

        This is the FIRST LEVEL of history browsing. Users select a station from this list, then see its song history.

        DIRECTORY ITEMS:
            Each station is added as a directory item with:
                - Label: Station name (from constants.STATIONS)
                - URL: plugin://...?action=history_songs&id={sid}
                - isFolder: True (this leads to another directory level)

            When a user selects a station item, Kodi invokes the plugin with:
                - action=history_songs
                - id={station_id}

            This triggers the show_songs() method for that specific station.

        NOTE:
            This method does NOT call endOfDirectory() because it needs to return and let Kodi handle the directory finalization. The router.py will handle the directory end after this returns.
        """
        
        base_url = sys.argv[0]

        for sid, name in STATIONS.items():
            url = f"{base_url}?action=history_songs&id={sid}"
            item = xbmcgui.ListItem(label=name)
            item.setArt({
                "thumb": self.art.station(name),
                "icon": self.art.icon(),
                "fanart": self.art.fanart(),
            })
            xbmcplugin.addDirectoryItem(
                handle=self.handle,
                url=url,
                listitem=item,
                isFolder=True,
            )

        xbmcplugin.endOfDirectory(self.handle)

    def show_songs(self, sid):
        """
        Show recently played songs for a specific station.

        Fetches history data from the API for the specified station and creates a Kodi directory with one item per recently played song.

        This is the SECOND LEVEL of history browsing. Users see the actual song list for the station they selected from show_stations().

        ARGS:
            sid (int): Station ID to show history for

        DIRECTORY ITEMS:
            Each song is added as a directory item with:
                - Label: Song title
                - Info: Artist and album (via setInfo or MusicInfoTag)
                - Artwork: Album artwork if available
                - isFolder: False (these are display-only items, not playable)

            The items are display-only - selecting them doesn't play the song (we don't have a way to replay specific songs from history).

        DATA FLOW:
            1. Call api.get_history(sid) to get recently played songs
            2. If no history available, return early
            3. For each song in history:
               a. Parse song data (title, artist, album, art)
               b. Create ListItem with song info
               c. Set artwork if available
               d. Add to directory
            4. Finalize directory with endOfDirectory()

        NOTE:
            Unlike show_stations(), this method DOES call endOfDirectory() because it's the final level of the directory hierarchy.

        HISTORY DATA STRUCTURE:
            The API returns history as a list of election events:
            [
                {
                    "start_actual": 1234567890,  # Unix timestamp
                    "songs": [
                        {
                            "title": "Song Title",
                            "artists": [{"name": "Artist 1"}, {"name": "Artist 2"}],
                            "albums": [{"name": "Album Name", "art": "/path/to/art"}]
                        }
                    ]
                },
                ...
            ]

            This method processes this into a flat list of song items.
        """
        
        xbmcplugin.setContent(self.handle, "songs")

        history = self.api.get_history(sid)

        if not history:
            '''
            Most likely a transient API hiccup (see api.py) rather than a station that's truly never played anything. It is worth saying so rather than just showing an empty list, which looks identical to "this feature is broken".
            '''
            
            item = xbmcgui.ListItem(label="No history available right now")
            item.setProperty("IsPlayable", "false")
            xbmcplugin.addDirectoryItem(self.handle, sys.argv[0], item, False)
            xbmcplugin.endOfDirectory(self.handle)
            return

        for song in history:
            title = song.get("title") or "Unknown"
            artist = song.get("artist", "")
            album = song.get("album", "")
            when = _relative_time(song.get("played_at"))

            item = xbmcgui.ListItem(label=title)
            item.setLabel2(artist)
            '''
            Explicitly non-playable -- without this, some skins' list views try to resolve a click as playback by default, which would just fail (there's no stream URL for a past song) and show the user an error for what's meant to be a purely informational entry.
            '''
            
            item.setProperty("IsPlayable", "false")

            tag = item.getMusicInfoTag()
            tag.setTitle(title)
            tag.setArtist(artist)
            tag.setAlbum(album)
            tag.setMediaType("song")

            art = song.get("art", "")
            item.setArt({"thumb": art, "icon": self.art.icon()} if art else {"icon": self.art.icon()})

            detail = " / ".join(part for part in (artist, album, when) if part)
            if detail:
                item.setLabel(f"{title}   [COLOR=FF999999]{detail}[/COLOR]")

            xbmcplugin.addDirectoryItem(
                handle=self.handle,
                url=sys.argv[0],
                listitem=item,
                isFolder=False,
            )

        xbmcplugin.endOfDirectory(self.handle)