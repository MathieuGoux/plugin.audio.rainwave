"""
Rainwave Kodi Addon - Utility Functions

This module provides common utility functions used throughout the addon. Currently, it contains only the logging function, but can be expanded to include other shared utilities as needed.

PURPOSE:
=======

Centralizing utility functions here:
    - Avoids code duplication across modules
    - Provides consistent behavior (e.g., logging format)
    - Makes maintenance easier (change in one place)
    - Improves code organization

FUTURE EXPANSIONS:

    This module could be expanded to include:
        - String formatting utilities
        - Path manipulation helpers
        - Data validation functions
        - Common Kodi interaction helpers
        - etc.
"""

import xbmc

#==LOGGING FUNCTION================

def log(msg):
    """
    Log a message to Kodi's log file.
    """
    
    xbmc.log(f"[Rainwave] {msg}", xbmc.LOGINFO)