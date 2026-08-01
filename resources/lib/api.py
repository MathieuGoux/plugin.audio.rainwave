"""
Rainwave Kodi Addon - API Client

This module provides the RainwaveAPI class which handles all communication with the Rainwave API (v4) at https://rainwave.cc/api4/.

KEY FEATURES:
    - HTTP POST requests with form-urlencoded body (works for all endpoints)
    - Automatic cookie handling for session management
    - Graceful error handling with comprehensive logging
    - Response parsing and data normalization
    - No authentication required (Rainwave moved to Discord-only auth)

IMPORTANT: All API calls are anonymous and stateless. The addon does not require or use user accounts, login credentials, or authentication tokens. This means some API features (like rating songs or making requests) are not available, but basic playback and metadata retrieval work perfectly.

ENDPOINTS USED:
    - info: Get station information, current/next/previous songs
    - tune_in: Register as listener (currently non-functional for anonymous users)
"""

import json
import time
import urllib.request
import urllib.parse
import http.cookiejar
import xbmc

from .constants import STATIONS, USER_AGENT

#==CONSTANTS================

# Base URL for all Rainwave API v4 endpoints

BASE = "https://rainwave.cc/api4/"

# URL format for album artwork images
# {0} is replaced with the art path from the API response

ART_FORMAT = "https://rainwave.cc{0}_320.jpg"

#==RAINWAVE API CLIENT================

class RainwaveAPI:
    """
    Client for the Rainwave API (v4).

    Provides methods for interacting with all Rainwave API endpoints used by this addon. All methods follow the same pattern:

        1. Build the request with appropriate headers and body
        2. Make the HTTP request with timeout
        3. Handle errors gracefully (return empty dict)
        4. Parse and normalize the JSON response
        5. Return the data

    ATTRIBUTES:
        cookiejar: http.cookiejar.CookieJar - for session cookie management
        opener: urllib.request.OpenerDirector - HTTP opener with cookie processor
        current_sid: int - currently selected station ID (default: 5 = All)

    NOTE ON SESSIONS:
        The cookiejar and opener are maintained to support session-based features, though currently most endpoints work without sessions. The tune_in() endpoint is the primary session-related call.
    """
    
    def __init__(self):
        """
        Initialize the API client.

        Sets up:
            - Cookie jar for session management
            - HTTP opener with cookie processor
            - Default current station ID (5 = All stations)

        Note: No authentication is performed. All calls are anonymous.
        """
        
        self.cookiejar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar)
        )
        self.current_sid = 5

    def _request(self, endpoint, params=None):
        """
        Make an HTTP request to the Rainwave API.

        ALL RAINWAVE API ENDPOINTS ACCEPT POST:
        The Rainwave API documentation guarantees that every endpoint accepts POST requests. Only a subset of endpoints also accept GET (the API docs specify which ones). POSTing unconditionally with the params as a form-urlencoded body works everywhere.

        This is also what the Rainwave website's own JavaScript client and every official usage example do, so we follow the same pattern.

        ARGS:
            endpoint (str): API endpoint path (e.g., 'info', 'tune_in')
            params (dict, optional): Dictionary of parameters. Defaults to None.

        RETURNS:
            dict: Parsed JSON response as dictionary, or empty dict on error

        ERROR HANDLING:
            - Network errors: Logged at ERROR level, returns empty dict
            - Empty responses: Returns empty dict
            - JSON parse errors: Logged at ERROR level, returns empty dict
            - Timeout: 10 seconds for all requests

        LOGGING:
            - Raw response (first 300 characters) logged at DEBUG level for troubleshooting API issues
        """
        
        if params is None:
            params = {}

        data = urllib.parse.urlencode(params).encode("utf-8")
        url = f"{BASE}{endpoint}"

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            with self.opener.open(req, timeout=10) as r:
                raw = r.read().decode("utf-8", errors="ignore")

                xbmc.log(f"[Rainwave] RAW {endpoint}: {raw[:300]}", xbmc.LOGDEBUG)

                if not raw.strip():
                    return {}

                return json.loads(raw)

        except Exception as e:
            xbmc.log(f"[Rainwave] ERROR {endpoint}: {e}", xbmc.LOGERROR)
            return {}

    def tune_in(self, sid):
        """
        Register this session as a listener of a station.

        This increments the station's listener count on the Rainwave website. However, it consistently returns 404 for anonymous clients (likely requires an authenticated user with credentials).

        CRITICAL: THIS IS BEST-EFFORT AND MUST NEVER GATE OTHER API CALLS

        The get_station_info() and get_now_playing() methods work as plain, stateless calls WITHOUT any prior tune_in(). The Rainwave API docs explicitly confirm this: "you can simply GET http://rainwave.cc/api4/ info?sid=1 to get a full JSON payload" - no session required.

        If we were to make other API calls dependent on tune_in() succeeding, the entire addon would fail to function. Therefore, tune_in() is called but its failure is ignored.

        ARGS:
            sid (int): Station ID to tune in to

        RETURNS:
            dict: API response (usually empty dict due to 404 for anonymous)
        """
        
        self.current_sid = sid
        return self._request("tune_in", {"sid": sid})

    def get_station_info(self, sid=None):
        """
        Get information about a station.

        This is a SELF-CONTAINED, STATELESS CALL that doesn't require prior tune_in(). It's the primary endpoint for getting now-playing data and is confirmed to work without authentication.

        ARGS:
            sid (int, optional): Station ID to query. If None, uses current_sid.

        RETURNS:
            dict: Station information including:
                - all_stations_info: Current song info for ALL stations
                - sched_current: Current election/song information
                - sched_next: Array of upcoming election events
                - sched_history: Array of past election events
                - api_info: Server time and metadata

        NOTE:
            This method updates self.current_sid to the provided sid (or keeps it if sid is None).
        """
        
        sid = sid or self.current_sid
        self.current_sid = sid
        return self._request("info", {"sid": sid})

    @staticmethod
    def _art_url(path):
        """
        Format an album art path into a full URL.

        ARGS:
            path (str): Album art path from API (e.g., "/albums/123/art")

        RETURNS:
            str: Full artwork URL, or empty string if path is None/empty
        """
        
        if not path:
            return ""
        return ART_FORMAT.format(path)

    def _parse_song(self, song):
        """
        Parse a song object from the API into a standardized format.

        SONG OBJECTS APPEAR IN MULTIPLE PLACES:
            - sched_current.songs[]: Currently playing songs (election in progress)
            - sched_next[].songs[]: Next song candidates (upcoming election)
            - sched_history[].songs[]: Previously played songs (past election)

        Each song object contains:
            - artists: List of artist objects, each with 'name' field
            - albums: List of album objects (first is the primary/winning one)
            - title: Song title
            - length: Duration in seconds

        THIS METHOD NORMALIZES TO:
            - title: Song title (string)
            - artist: Comma-separated artist names (string)
            - album: Album name (string)
            - art: Album artwork URL (string, formatted)

        ARGS:
            song (dict): Song object from API response

        RETURNS:
            dict: Standardized song information
        """
        
        artists = ", ".join(a["name"] for a in song.get("artists", []))
        albums = song.get("albums", [])
        album = albums[0] if albums else {}
        return {
            "title": song.get("title", ""),
            "artist": artists,
            "album": album.get("name", ""),
            "art": self._art_url(album.get("art", "")),
        }

    def get_now_playing(self, sid=None):
        """
        Get complete now-playing information for a station.

        This is the MAIN METHOD used by the widget to get current song data. It combines data from multiple parts of the API response to provide everything needed for the now-playing display.

        IMPORTANT: RETURNS NONE ON API FAILURE

        Unlike returning an empty dict or dict with empty fields, returning None allows callers to recognize "no data this cycle" and simply leave whatever was already on screen alone, rather than overwriting good title/artist/album/art with empty strings.

        DATA SOURCES:
            1. all_stations_info: Quick access to current song for all stations
               - Contains title, artists, album, art for each station
            2. sched_current: Timing data for progress bar
               - start_actual: Unix timestamp song actually started playing
               - length: Song duration in seconds
               - server_time: Server's own clock (from api_info.time)
            3. sched_next: Upcoming song candidates
               - Array of election events, each with songs[]
            4. sched_history: Previously played song
               - Most recent past election with its song

        ARGS:
            sid (int, optional): Station ID. If None, uses current_sid.

        RETURNS:
            dict or None: Complete now-playing data including:
                - title, artist, album, art: Current song info
                - station: Station name
                - next_candidates: List of upcoming song dicts
                - previous: Previously played song dict
                - start_actual: Unix timestamp when song started
                - length: Song duration in seconds
                - server_time: Server clock at time of API response

        NOTE ON SCHED_NEXT:
            The sched_next endpoint returns an array of upcoming election events. Each election is still open for voting and Rainwave doesn't expose live vote counts through this endpoint (the vote counts stayed frozen across repeated polls even while real counts on the website climbed).

            Rainwave tracks live vote tallies through a separate real-time channel not exposed by this endpoint. Rather than presenting a guess that looks authoritative but often isn't, EVERY candidate is returned here and the caller (widget.py) rotates through them one at a time.
        """
        
        sid = sid or self.current_sid
        info = self.get_station_info(sid)

        if not info:
            return None

        station_info = info.get("all_stations_info", {}).get(str(sid))

        sched_current = info.get("sched_current", {})
        songs = sched_current.get("songs", [])
        song = songs[0] if songs else {}

        timing = {
            "start_actual": sched_current.get("start_actual"),
            "length": song.get("length") or sched_current.get("length"),
            "server_time": info.get("api_info", {}).get("time", time.time()),
        }

        if station_info:
            result = {
                "title": station_info.get("title", ""),
                "artist": station_info.get("artists", ""),
                "album": station_info.get("album", ""),
                "art": self._art_url(station_info.get("art", "")),
                "station": STATIONS.get(sid, ""),
            }
        else:
            
            result = self._parse_song(song)
            result["station"] = STATIONS.get(sid, "")

        sched_next = info.get("sched_next", [])
        next_songs = sched_next[0].get("songs", []) if sched_next else []
        result["next_candidates"] = [self._parse_song(s) for s in next_songs]

        sched_history = info.get("sched_history", [])
        history_songs = sched_history[0].get("songs", []) if sched_history else []
        result["previous"] = self._parse_song(history_songs[0]) if history_songs else {}

        result.update(timing)
        return result

    def get_history(self, sid=None):
        """
        Get recently played songs for a station.

        This method returns the recently played songs from sched_history, which is already included in the same stateless info() call that get_now_playing() uses.

        ALTERNATIVE: playback_history endpoint
            The separate playback_history endpoint can return up to the last 100 songs, but it requires a logged-in user's own account ID and API key. Since this addon only ever plays anonymously (see tune_in() comments), it doesn't have those credentials to send.

            Therefore, we use sched_history which gives us fewer entries (typically 5) but requires zero extra setup for the user.

        ARGS:
            sid (int, optional): Station ID. If None, uses current_sid.

        RETURNS:
            list: List of song dicts with played_at timestamps, most recent first.
                Each dict contains: title, artist, album, art, played_at Returns empty list if API call fails or no history available.
        """
        
        sid = sid or self.current_sid
        info = self.get_station_info(sid)
        if not info:
            return []

        entries = []
        for event in info.get("sched_history", []):
            songs = event.get("songs", [])
            if not songs:
                continue
            entry = self._parse_song(songs[0])
            entry["played_at"] = event.get("start_actual")
            entries.append(entry)
        return entries