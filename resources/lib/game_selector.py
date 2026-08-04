"""
Rainwave Kodi Addon - Game Selector Dialog

This module provides the GameSelectorDialog class which allows users to manually select a game for artwork display. This is accessed by pressing the Information key ('i') during playback.

PURPOSE:
=======

The addon automatically detects which game is playing based on song
metadata (album title, song title). However, this automatic detection
can fail for various reasons:

    - Album title doesn't match game name exactly
    - Song metadata is incomplete or inaccurate
    - Game has multiple names or spellings
    - Obscure remix albums with unclear naming

When this happens, users can manually override the automatic detection by opening this dialog and selecting the correct game. The selected game's artwork will be displayed immediately.

FEATURES:
    - Search SteamGridDB for games by title
    - Display search results in a scrollable list
    - Show current automatic selection
    - Allow user to select from results
    - Persist selection for future use

USAGE:
    1. User presses Information key ('i') during playback
    2. service.py detects the key press (ACTION_SHOW_INFO)
    3. service.py calls _open_game_selector()
    4. GameSelectorDialog is created and shown
    5. User searches for and selects a game
    6. Selection is passed to slideshow.set_manual_game()
    7. Slideshow updates to show the selected game's artwork

"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xbmcaddon
import xbmcgui
import xbmc
import xbmcvfs

from .utils import log
from datetime import datetime

API_BASE = "https://www.steamgriddb.com/api/v2"
CACHE_SUBDIR = "art_cache"
MANIFEST_NAME = "manifest.json"
CACHE_SCHEMA_VERSION = 13


def _year_label(entry):
    """
    Extract a display year (as a string) from a SteamGridDB game object, or None if it doesn't have one. Works for both /search/autocomplete result entries and a /games/id/{id} single-game response -- both use the same year/release_date shape.
    """
    
    year = entry.get("year") or entry.get("release_year")
    release_date = entry.get("release_date")

    if not year and release_date is not None:
        if isinstance(release_date, str):
            year = release_date.split("-", 1)[0]
        elif isinstance(release_date, int):
            if 1900 <= release_date <= 2100:
                # Looks like a calendar year
                year = release_date
            else:
                # Assume Unix timestamp
                year = datetime.utcfromtimestamp(release_date).year

    return str(year) if year else None

#==GAME SELECTOR DIALOG CLASS================

class GameSelectorDialog:
    """
    Dialog for manual game selection.

    Provides a searchable interface for users to find and select the correct game when automatic detection fails.

    INHERITANCE:
        Inherits from xbmcgui.WindowXMLDialog to integrate with Kodi's dialog system. The parent class provides:
            - doModal(): Show the dialog modally (blocks until closed)
            - close(): Close the dialog
            - onInit(): Called when dialog is initialized
            - onAction(): Called when user performs an action
            - onClick(): Called when user clicks a control

    ATTRIBUTES:
        selected_game (dict or None): The game selected by the user
            - If user selects a game: {'name': str, 'id': int}
            - If user cancels: None
        
        api_key (str): SteamGridDB API key for searches
        results (list): Current search results
        current_query (str): Current search query
        current_selection (int): Index of currently selected result

    LIFECYCLE:
        1. service.py creates instance with API key
        2. service.py calls doModal() to show dialog
        3. User interacts with dialog
        4. User selects a game or cancels
        5. service.py checks selected_game attribute
        6. If game selected, calls slideshow.set_manual_game()
    """
    
    def __init__(self, api_key, current_album=None, current_sid=None, current_song_title=None, current_game=None, current_game_id=None):
        """
        Initialize the game selector dialog.

        ARGS:
            api_key (str): SteamGridDB API key for searches

        SETS UP:
            - Parent class initialization
            - API key for SteamGridDB searches
            - Internal state for search results and selection
            - selected_game set to None (will be set if user selects)
        """
        
        self.api_key = api_key
        self.current_album = current_album #Album currently playing
        self.current_sid = current_sid #Station currently playing
        self.current_song_title = current_song_title #Song currently playing
        self.current_game = current_game #Game chosen by logic
        self.current_game_id = current_game_id #SteamGridDB id of current_game, if known
        self.selected_game = None

    def show(self):
        """
        Prompt for a game name, then display matching games.
        """
        
        if not self.api_key:
            xbmcgui.Dialog().ok(
                "SteamGridDB",
                "SteamGridDB API key is not configured."
            )
            return False

        # Debug: show what we received
        log(f"GameSelector: album={self.current_album}, song={self.current_song_title}")

        prompt = f"Search for a game"
        if self.current_game:
            current_label = self.current_game
            year = self._fetch_current_game_year()
            if year:
                current_label = f"{current_label} ({year})"
            prompt = f"Search for a game (Current: {current_label})"
        elif self.current_album:
            prompt = f"Search for a game (Album: {self.current_album})"

        keyboard = xbmc.Keyboard("", prompt)

        #keyboard = xbmc.Keyboard("", "Search for a game")
        keyboard.doModal()

        if not keyboard.isConfirmed():
            return False

        query = keyboard.getText().strip()

        if len(query) < 2:
            xbmcgui.Dialog().ok(
                "SteamGridDB",
                "Please enter at least two characters."
            )
            return False

        games = self._search_games(query)

        if not games:
            xbmcgui.Dialog().ok(
                "SteamGridDB",
                "No matching games found."
            )
            return False

        names = []

        for g in games:
            name = g.get("name", "Unknown")
            year = _year_label(g)
            names.append(f"{name} ({year})" if year else name)

        selected = xbmcgui.Dialog().select(
            "Select Game",
            names
        )

        if selected == -1:
            return False

        self.selected_game = {
            "id": str(games[selected]["id"]),
            "name": games[selected]["name"]
        }

        # Save manual override if we have an album title
        if self.current_album:
            # Ask user: song-level or album-level override?
            if self.current_song_title:
                choices = [
                    f"Song only: '{self.current_song_title}'",
                    f"Entire album: '{self.current_album}'"
                ]
                override_type = xbmcgui.Dialog().select(
                    "Override scope",
                    choices
                )
                # If user cancels, default to song-level
                is_song_override = override_type == 0 if override_type != -1 else True
            else:
                # No song title available, must be album-level
                is_song_override = False

            self._save_manual_override(
                self.current_album,
                self.selected_game,
                is_song_override
            )

        return True
        
    def _open_game_selector(self):
        """
        Open game selector and update manifest with manual override.
        """
        
        try:
            from .game_selector import GameSelectorDialog
            addon = xbmcaddon.Addon()
            
            # Get current album and song from the now-playing widget
            current_album = xbmcgui.Window(10000).getProperty("Rainwave.Album")
            
            # Try multiple property names for song title
            current_song_title = (xbmcgui.Window(10000).getProperty("Rainwave.Song") or
                                 xbmcgui.Window(10000).getProperty("Rainwave.NowPlaying") or
                                 xbmcgui.Window(10000).getProperty("Rainwave.Track"))
            current_sid = xbmcgui.Window(10000).getProperty("Rainwave.CurrentStation")
            current_sid = int(current_sid) if current_sid else None

            dialog = GameSelectorDialog(
                addon.getSettingString("steamgriddb_api_key"),
                current_album=current_album,
                current_sid=current_sid,
                current_song_title=current_song_title
            )
            if dialog.show():
                # Update slideshow immediately
                xbmcgui.Window(10000).setProperty(
                    "Rainwave.ManualGame",
                    dialog.selected_game.get("name", "")
                )
        except Exception as e:
            log(f"Game selector failed: {e}")
        
    def _fetch_current_game_year(self):
        """
        Look up the release year for self.current_game_id (the game currently resolved by the addon's logic) via SteamGridDB's single-game endpoint, so the search prompt can show it next to the current game's name. Useful for telling apart same-named games. Returns None if there's no id to look up, or on any request failure; the prompt just omits the year in that case, same as before this existed.
        """
        
        if not self.current_game_id:
            return None

        url = f"{API_BASE}/games/id/{self.current_game_id}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Kodi"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())["data"]
        except Exception as e:
            log(f"GameSelector: couldn't fetch release year for game {self.current_game_id}: {e}")
            return None

        return _year_label(data)

    def _search_games(self, query):

        if len(query.strip()) < 2:
            return []

        query = urllib.parse.quote(query)

        url = f"{API_BASE}/search/autocomplete/{query}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Kodi"
            }
        )

        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["data"]
            
    def _save_manual_override(self, album_title, selected_game, is_song_override=False):
        """Save manual override at song-level or album-level."""
        if not album_title or not selected_game:
            return

        addon = xbmcaddon.Addon()
        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        cache_dir = os.path.join(profile, CACHE_SUBDIR)
        manifest_path = os.path.join(cache_dir, MANIFEST_NAME)

        # Load existing manifest
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {"_schema": CACHE_SCHEMA_VERSION, "games": {}, "manual_overrides": {}}

        # Ensure schema is up to date
        manifest["_schema"] = CACHE_SCHEMA_VERSION

        # Initialize manual_overrides if missing
        if "manual_overrides" not in manifest:
            manifest["manual_overrides"] = {}
        
        '''
        An override now records both the SteamGridDB id and the name. The id is what actually disambiguates true homonyms (two unrelated games sharing an identical title). game_art.py re-searches SteamGridDB by name every time it resolves a title, so a name-only override still lands back on whichever candidate the automatic ranking prefers, which may not be the one the user picked here. Storing the id lets game_art.py skip that re-search entirely and fetch art for this exact entry.
        '''
        
        override_value = {
            "id": selected_game.get("id"),
            "name": selected_game.get("name", ""),
        }
        
        '''
        New shape per album: {"game": {...} | None, "songs": {title: {...}}}. Normalize whatever's already on disk for this album into that shape first, so saving one override type doesn't clobber a previously-saved override of the other type, and so older manifests (name-only strings, or a bare {song: name} dict) keep working instead of being silently discarded.
        '''
        
        raw_entry = manifest["manual_overrides"].get(album_title)
        if isinstance(raw_entry, dict) and ("game" in raw_entry or "songs" in raw_entry):
            entry = raw_entry
        elif isinstance(raw_entry, str):
            
            # Legacy album-level override: name only, no id on file.
            entry = {"game": {"id": None, "name": raw_entry}, "songs": {}}
        elif isinstance(raw_entry, dict):
            
            # Legacy song-title -> name mapping, no ids on file.
            entry = {
                "game": None,
                "songs": {song: {"id": None, "name": name} for song, name in raw_entry.items()},
            }
        else:
            entry = {"game": None, "songs": {}}

        entry.setdefault("songs", {})

        # Save based on override type
        if is_song_override and self.current_song_title:
            # Song-level override
            entry["songs"][self.current_song_title] = override_value
        else:
            # Album-level override
            entry["game"] = override_value

        manifest["manual_overrides"][album_title] = entry

        # Save back to disk
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            xbmcgui.Dialog().ok("Error", f"Failed to save override: {e}")
            return

        '''
        The running GameArtProvider instance (the one slideshow.py's tick() actually calls .get() against, once a second) keeps its own in-memory copy of manual_overrides. It only refreshes that copy from disk after it completes an actual fetch, or every MANIFEST_SAVE_INTERVAL (currently 5 minutes) -- see game_art.py's _save_manifest()/_maybe_save_manifest(). Since this override was just written straight to disk from here, without either of those happening yet, the live instance would otherwise keep serving whatever was already cached under the old key for up to 5 minutes, which looks indistinguishable from "the override didn't work".

        Setting this property mirrors the existing Rainwave.ManualGame mechanism below: slideshow.py's tick() checks for it on every poll and calls reload_manual_overrides() on its live GameArtProvider as soon as it sees it, so the override the user just picked applies on the very next poll instead of waiting on either of the paths above.
        '''
        
        xbmcgui.Window(10000).setProperty("Rainwave.OverridesDirty", "1")