"""
Rainwave Kodi Addon - Station Menu

This module provides the StationMenu class which displays the station
selection menu to the user. When a user selects "Stations" from the
main menu, this class creates and displays a directory listing of all
available Rainwave stations.

KEY FEATURES:
    - Creates a Kodi directory with one item per station
    - Each station item is configured as a playable item
    - Uses artwork from the Artwork helper class
    - Properly marks items as playable and non-folder for Kodi 20+
"""

import xbmcgui
import xbmcplugin
import sys

from .constants import STATIONS
from .artwork import Artwork

#==STATION MENU CLASS================

class StationMenu:
    """
    Displays the station selection menu as a Kodi directory.

    This class creates a directory listing where each item represents a Rainwave station. When a user selects an item, Kodi invokes the plugin again with action=play and the station ID, which triggers playback via router.py.

    ATTRIBUTES:
        handle (int): Plugin handle for adding directory items
        art (Artwork): Artwork helper instance for station icons

    DIRECTORY ITEMS:
        Each station is added as a directory item with:
            - Label: Station name (from constants.STATIONS)
            - Artwork: Station icon and fanart (from Artwork class)
            - URL: plugin://...?action=play&id={sid}
            - IsPlayable: "true" (CRITICAL - tells Kodi this can be played)
            - isFolder: False (IMPORTANT - for Kodi 20+ compatibility)

    CRITICAL CHANGES FOR KODI 20+:
    ==============================

    In Kodi 20 and later, the default behavior for directory items changed. Previously, items without explicit isFolder=True were treated as files. Now, they may be treated as folders.

    For stations (which are streams, not folders), we MUST set:
        - isFolder=False: Explicitly marks as non-folder
        - IsPlayable="true": Marks as playable media

    Without isFolder=False:
        - Kodi may treat the station as a folder to browse
        - Selecting it would open a directory instead of playing
        - The play action would never trigger

    Without IsPlayable="true":
        - Kodi may not recognize the item as playable
        - Some skins may not show play indicators
        - Remote apps may not handle it correctly
    """
    
    def __init__(self, handle):
        self.handle = handle
        self.art = Artwork()

    def show(self):
        """
        Display the station menu as a Kodi directory.

        Creates a directory item for each station defined in STATIONS (from constants.py). Each item is configured to start playback when selected. Order of items is determined by the order in constants.py.

        STEPS:
            1. Get the plugin base URL from sys.argv[0]
            2. For each station in STATIONS:
               a. Create the play URL with action=play and station ID
               b. Create a ListItem with station name as label
               c. Set artwork (thumb, icon, fanart) from Artwork class
               d. Mark as playable and non-folder
               e. Add to directory
            3. Finalize directory with endOfDirectory()

        ARTWORK:
            - thumb: Station-specific artwork (from art.station())
            - icon: Addon icon (from art.icon())
            - fanart: Addon fanart (from art.fanart())

            The artwork files should be placed in:
                - resources/skins/media/{station_name}.png for station icons
                - icon.png in addon root for addon icon
                - resources/media/fanart.png for addon fanart

        URL FORMAT:
            The play URL follows Kodi's plugin URL format: plugin://plugin.audio.rainwave/?action=play&id={sid}

            When a user selects this item, Kodi will invoke the plugin with:
                - action=play
                - id={sid}

            router.py handles this in the play action handler.

        FINALIZATION:
            After adding all items, xbmcplugin.endOfDirectory() MUST be called to tell Kodi the directory listing is complete. Without this, Kodi will wait indefinitely and the menu won't display properly.
        """
        
        base_url = sys.argv[0]

        for sid, name in STATIONS.items():

            url = f"{base_url}?action=play&id={sid}"

            item = xbmcgui.ListItem(label=name)

            item.setArt({
                "thumb": self.art.station(name),
                "icon": self.art.icon(),
                "fanart": self.art.fanart()
            })

            # CRITICAL: mark as playable item
            item.setProperty("IsPlayable", "true")

            xbmcplugin.addDirectoryItem(
                handle=self.handle,
                url=url,
                listitem=item,
                isFolder=False   # <-- IMPORTANT CHANGE
            )

        xbmcplugin.endOfDirectory(self.handle)