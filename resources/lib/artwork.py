"""
Rainwave Kodi Addon - Station Artwork Helper

This module provides the Artwork class, a simple helper for retrieving artwork paths for stations and the addon's icon/fanart.

PURPOSE:
=======

The addon displays artwork in several places:
    - Station icons in the station selection menu
    - Addon icon in Kodi's addon list
    - Addon fanart as background

This class provides a centralized way to get the paths for these images.

CUSTOM ARTWORK:
==============

The addon does NOT include station artwork by default. Users can add
their own custom station artwork by placing image files in:

    resources/skins/media/{station_name}.png

Where {station_name} matches the station names in constants.py:
    - Game.png
    - OC Remix.png
    - Covers.png
    - Chiptune.png
    - All.png
    - Chill.png

If no custom artwork is provided, the addon will use default Kodi-provided icons or no artwork.

ICON AND FANART:
===============

The addon icon (icon.png) and fanart (fanart.png) are defined in:
    - addon.xml: <icon> and <fanart> elements
    - These should be in the addon root and resources/media/ respectively

This class provides methods to get the full paths to these files.
"""

#==ARTWORK CLASS================

class Artwork:
    """
    Simple helper to get artwork paths for stations and addon assets.

    This class provides methods to retrieve full paths to:
        - Station-specific artwork
        - Addon icon
        - Addon fanart

    All paths returned are absolute paths suitable for use in:
        - xbmcgui.ListItem.setArt()
        - xbmcgui.ListItem.setIcon()
        - Skin XML <texture> elements

    ATTRIBUTES:
        None - this is a stateless utility class

    USAGE:
        art = Artwork()
        station_icon = art.station("Game")
        addon_icon = art.icon()
        addon_fanart = art.fanart()
    """
    
    def icon(self):
        """
        Get the addon icon path.

        RETURNS:
            str: Full path to icon.png

        PATH:
            The icon is located at:
            
            {addon_path}/icon.png

            This is the same icon defined in addon.xml:
            
            <assets>
                <icon>icon.png</icon>
            </assets>

        USAGE:
            This icon is used in:
                - Kodi's addon list
                - Station menu items (as icon)
                - Any place that needs the addon's icon
        """
        
        return "special://home/addons/plugin.audio.rainwave/icon.png"

    def fanart(self):
        """
        Get the addon fanart path.

        RETURNS:
            str: Full path to fanart image

        PATH:
            The fanart is located at:
                {addon_path}/resources/media/fanart.png

            This is the same fanart defined in addon.xml:
            
            <assets>
                <fanart>resources/media/fanart.png</fanart>
            </assets>

        USAGE:
            This fanart is used as:
                - Background in Kodi's addon info dialog
                - Background in the station menu
                - Any place that needs the addon's fanart
        """
        
        return "special://home/addons/plugin.audio.rainwave/resources/media/fanart.png"

    def station(self, name):
        """
        Get the artwork path for a station.

        ARGS:
            name (str): Station name (e.g., "Game", "OC Remix")

        RETURNS:
            str: Full path to station artwork image

        PATH CONSTRUCTION:
            The method constructs the path as:
            
            {addon_path}/resources/skins/media/{station_name}.png

            where {addon_path} is the path to the addon directory.

        FALLBACK:
            If the file doesn't exist at the constructed path, this method still returns the path. It's up to Kodi to handle missing images (typically by showing a default icon or nothing).

        CUSTOM ARTWORK:
            Users can add custom station artwork by placing PNG files in: resources/skins/media/

        The files should be named to match the station names exactly (including case) as defined in constants.py.

        EXAMPLE:
            station("Game") -> "{addon_path}/resources/skins/media/Game.png"
            station("All") -> "{addon_path}/resources/skins/media/All.png"
        """
        
        return f"special://home/addons/plugin.audio.rainwave/resources/media/stations/{name}.png"