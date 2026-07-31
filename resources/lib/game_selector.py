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
    """Extract a display year (as a string) from a SteamGridDB game
    object, or None if it doesn't have one. Works for both
    /search/autocomplete result entries and a /games/id/{id} single-game
    response -- both use the same year/release_date shape.
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


class GameSelectorDialog:
    def __init__(self, api_key, current_album=None, current_sid=None, current_song_title=None,
                 current_game=None, current_game_id=None):
        self.api_key = api_key
        self.current_album = current_album #Album currently playing
        self.current_sid = current_sid #Station currently playing
        self.current_song_title = current_song_title #Song currently playing
        self.current_game = current_game #Game chosen by logic
        self.current_game_id = current_game_id #SteamGridDB id of current_game, if known
        self.selected_game = None

    def show(self):
        """Prompt for a game name, then display matching games."""
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
        """Open game selector and update manifest with manual override."""
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
        """Look up the release year for self.current_game_id (the game
        currently resolved by the addon's logic) via SteamGridDB's
        single-game endpoint, so the search prompt can show it next to
        the current game's name -- useful for telling apart same-named
        games. Returns None if there's no id to look up, or on any
        request failure; the prompt just omits the year in that case,
        same as before this existed.
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

        # An override now records both the SteamGridDB id and the name.
        # The id is what actually disambiguates true homonyms (two
        # unrelated games sharing an identical title) -- game_art.py
        # re-searches SteamGridDB by name every time it resolves a
        # title, so a name-only override still lands back on whichever
        # candidate the automatic ranking prefers, which may not be
        # the one the user picked here. Storing the id lets game_art.py
        # skip that re-search entirely and fetch art for this exact
        # entry.
        override_value = {
            "id": selected_game.get("id"),
            "name": selected_game.get("name", ""),
        }

        # New shape per album: {"game": {...} | None, "songs": {title: {...}}}.
        # Normalize whatever's already on disk for this album into that
        # shape first, so saving one override type doesn't clobber a
        # previously-saved override of the other type, and so older
        # manifests (name-only strings, or a bare {song: name} dict)
        # keep working instead of being silently discarded.
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
            