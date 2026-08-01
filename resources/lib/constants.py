"""
Rainwave Kodi Addon - Global Constants

This module contains global constants used throughout the addon.
Centralizing constants here makes them easier to find, modify, and maintain.

CURRENT CONSTANTS:
    - API_BASE: Base URL for Rainwave API v4
    - USER_AGENT: User agent string for API requests
    - DEFAULT_TIMEOUT: Default HTTP timeout in seconds
    - STATIONS: Station ID to name mapping

NOTE:
    Some constants that should logically be here are currently defined elsewhere (like STREAM_HOST in service.py, STATIONS in player.py). Consider consolidating all global constants to this file in a future refactor for better maintainability.
"""

#==API CONFIGURATION================

# Base URL for Rainwave API version 4
# All API endpoints are relative to this base URL
# Example: https://rainwave.cc/api4/info
API_BASE = "https://rainwave.cc/api4"

# User agent string sent with all API requests
# Identifies this addon to the Rainwave server
# Format: "AddonName Version/Platform"
USER_AGENT = "Kodi Rainwave Addon"

# Default timeout for HTTP requests (seconds)
# Used by urllib.request.urlopen() calls
# Prevents hanging indefinitely on network issues
DEFAULT_TIMEOUT = 10 #seconds

#==STATIONS DEFINITIONS================

'''
Station ID to display name mapping

These IDs correspond to the station IDs used in:
- Rainwave API (sid parameter)
- Stream URLs (relay.rainwave.cc/{slug}.mp3)
- Internal routing
#
# Station IDs and their meanings:
#   5: All - Mix of all Rainwave stations
#   1: Game - Video game music only
#   2: OC Remix - OverClocked ReMix community tracks
#   3: Covers - Video game music cover versions
#   4: Chiptune - Chiptune and 8-bit music
#   6: Chill - Relaxing video game music
#
# Note: The order in this dict doesn't matter for functionality,
# but the IDs must match what Rainwave uses.
'''

STATIONS = {
    5: "All",
    1: "Game",
    2: "OC Remix",
    3: "Covers",
    4: "Chiptune",
    6: "Chill",
}
