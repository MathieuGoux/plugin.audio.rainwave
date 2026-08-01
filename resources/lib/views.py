"""
Rainwave Kodi Addon - View Definitions

This module provides the MainMenu class which defines the main menu view shown when the addon is first launched. It's the entry point that users see when they open the addon from Kodi's interface.

PURPOSE:
=======

The main menu provides access to all top-level addon functionality:
    - Stations: Browse and play Rainwave stations
    - History: View recently played songs
    - Settings: Configure addon behavior

This is a simple module with a single class and method, but it's important for providing a clear user interface.

VIEW HIERARCHY:
===============

Main Menu (this file)
    ├── Stations → StationMenu (stations.py)
    ├── History → HistoryMenu (history.py)
    └── Settings → Kodi's built-in settings dialog

Each menu level is a separate directory listing that users can navigate through using Kodi's standard navigation controls.
"""

import xbmc
import xbmcplugin
import xbmcgui

#==MAIN MENU CLASS================

class MainMenu:
    """
    Creates the main menu with all top-level addon options.

    This class is responsible for displaying the first screen users see when they launch the addon. It creates a Kodi directory with items for each main menu option.

    ATTRIBUTES:
        handle (int): Plugin handle for adding directory items

    MENU ITEMS:
        The main menu contains these items:
            1. Stations - Opens station selection menu
               - action: stations
               - label: "Stations"
               - icon: Addon icon
    
            2. History - Opens history menu
               - action: history
               - label: "History"
               - icon: Addon icon

    3. Settings - Opens Kodi's settings dialog
               - action: settings
               - label: "Settings"
               - icon: Settings gear icon (built-in Kodi icon)

    DIRECTORY CREATION:
        Each menu item is added using xbmcplugin.addDirectoryItem():
            
            - label: Display text for the item
            - path: URL with action parameter
            - listitem: ListItem with icon and other properties
            - isFolder: True (these lead to other directories)

    FINALIZATION:
        After adding all items, xbmcplugin.endOfDirectory() is called to tell Kodi the directory listing is complete.

    USAGE:
        menu = MainMenu(handle)
        menu.show()

    Where handle comes from sys.argv[1] in the plugin entry point.
    """
    
    def __init__(self, handle):
        self.handle = handle

    def _add(self, label, action):
        item = xbmcgui.ListItem(label)
        url = f"plugin://plugin.audio.rainwave/?action={action}"
        xbmcplugin.addDirectoryItem(self.handle, url, item, True)

    def show(self):
        """
        Display the main menu as a Kodi directory.

        Creates a directory listing with items for each main menu option.

        STEPS:
            1. Get the plugin base URL from sys.argv[0]
        
            2. Create directory items for each menu option:
                a. Stations: action=stations
                b. History: action=history
                c. Settings: action=settings
        
            3. For each item:
                - Create ListItem with appropriate label
                - Set icon (addon icon for Stations/History, built-in for Settings)
                - Mark as folder (isFolder=True)
                - Add to directory
        
            4. Finalize directory with endOfDirectory()

        ICONS:
            - Stations and History use the addon's icon (from artwork.py)
            - Settings uses Kodi's built-in settings icon (no explicit path needed, Kodi provides it)

        URL FORMAT:
            Each menu item has a URL like: plugin://plugin.audio.rainwave/?action=stations

            When a user selects an item, Kodi invokes the plugin with: action={action_name}

            router.py handles these actions in its run() method.

        DIRECTORY PROPERTIES:
            Each item is marked as a folder (isFolder=True) because selecting it leads to another directory listing, not a playable item.

        FINALIZATION:
            xbmcplugin.endOfDirectory(handle) MUST be called after adding all items. Without this, Kodi will wait indefinitely and the menu won't display properly.
        """
        
        self._add("Stations", "stations")
        self._add("History", "history")
        self._add("Settings", "settings")
